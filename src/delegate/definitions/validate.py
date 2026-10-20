"""Validate rendered agent files against each harness's documented schema.

Every check is an observable property of the file on disk, never of the
declaration that produced it, so hand-written files get the same judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .declaration import CONTEXT_LOAD_READ_MARKER
from .render import VERIFIER_GUARD_MARKER, VERIFIER_WRITE_GUARD_MARKER, VERIFIER_WRITE_PATH_PREFIXES
from ..shared.tiers import Tiers

CLAUDE_CODE_KEYS = {
    "name", "description", "tools", "disallowedTools", "model", "permissionMode",
    "maxTurns", "skills", "mcpServers", "hooks", "memory", "background",
    "omitClaudeMd", "effort", "isolation", "color", "initialPrompt", "experimental",
}
OPENCODE_KEYS = {
    "name", "model", "variant", "prompt", "description", "temperature", "top_p",
    "mode", "hidden", "color", "steps", "maxSteps", "options", "permission",
    "disable", "tools",
}
CLAUDE_CODE_WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "Bash"}
OPENCODE_WRITE_TOOLS = {"edit", "write", "bash", "patch"}
# The OpenCode permissions that write a file. OpenCode matches each one against
# the path relative to the worktree, so a pattern that starts with "../" opens
# only a path outside the project.
OPENCODE_FILE_WRITE_TOOLS = ("edit", "write", "patch")
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# OpenCode escapes these characters before it turns the glob into a regular
# expression. Source: packages/core/src/util/wildcard.ts @ tag v1.18.31.
_OPENCODE_REGEX_SPECIALS = re.compile(r"[.+^${}()|\[\]\\]")

# The command forms that a push guard must stop. A guard must stop all three.
# The third form is the one that "git push*" does not cover, because that
# pattern compiles to an anchored "^git push.*$" and the command starts with
# "git -C". The first two forms are both inside that anchored pattern.
PUSH_PROBES = ("git push", "git push origin main", "git -C /tmp/x push")

# The command forms that a verifier's OpenCode bash map must deny. An OpenCode
# pattern cannot examine a path argument, so the map has no temp-write rule and
# every write command in this list must resolve to deny.
VERIFIER_BASH_WRITE_PROBES = (
    "git push",
    "git commit -m x",
    "git checkout main",
    "rm -rf /Users/x",
    "darwin-rebuild switch",
    "tee /etc/hosts",
    # A gate command must not open these either (ADR 0004).
    "nixos-rebuild switch",
    "direnv allow",
    "git add .",
    "git reset --hard",
    "git stash",
    "sudo rm -rf /x",
)


def opencode_pattern_matches(command: str, pattern: str) -> bool:
    """Match one command against one OpenCode permission pattern.

    OpenCode uses a simple anchored glob, not a regular expression: "*" is
    zero or more characters and "?" is exactly one character. A pattern that
    ends in " *" also matches the bare command, so "git push *" covers
    "git push". Source: https://opencode.ai/docs/permissions/ and
    packages/core/src/util/wildcard.ts @ tag v1.18.31, both read 2026-09-19.
    """
    normalized = command.replace("\\", "/")
    escaped = _OPENCODE_REGEX_SPECIALS.sub(lambda m: "\\" + m.group(0), pattern.replace("\\", "/"))
    escaped = escaped.replace("*", ".*").replace("?", ".")
    if escaped.endswith(" .*"):
        escaped = escaped[:-3] + "( .*)?"
    return re.fullmatch(escaped, normalized, re.DOTALL) is not None


def opencode_permission_action(permission: dict, tool: str, target: str) -> str | None:
    """Resolve the action that OpenCode applies to one call of one permission.

    `tool` is the permission name (`bash`, `edit`, `external_directory`), and
    `target` is the text that OpenCode matches against the patterns of that
    permission: the command for `bash`, the path relative to the worktree for
    `edit`, and the absolute parent directory with `/*` for
    `external_directory`. OpenCode flattens the permission map into rules in
    key order and keeps the last rule whose permission name and whose pattern
    both match. A rule that comes before the catch-all is therefore shadowed
    and does nothing.
    """
    action = None
    if not isinstance(permission, dict):
        return None
    for key, value in permission.items():
        if not opencode_pattern_matches(tool, str(key)):
            continue
        if isinstance(value, dict):
            for pattern, sub in value.items():
                if opencode_pattern_matches(target, str(pattern)):
                    action = sub
        else:
            action = value
    return action


def opencode_bash_action(permission: dict, command: str) -> str | None:
    """Resolve the action that OpenCode applies to one bash command."""
    return opencode_permission_action(permission, "bash", command)


#: Every code that the validator writes, with its meaning. The finding-code
#: tables of the skill and of the commands reference are generated from this
#: table (`delegate docs`), and `Finding` accepts no code outside it, so a
#: code cannot be missing from the reference.
FINDING_CODES: dict[str, str] = {
    "UNKNOWN_KEY": "a frontmatter key the harness does not recognise; inert on Claude Code, swept into provider options on OpenCode",
    "BAD_MODEL": "model not in the tier table's allowed set for that harness",
    "BAD_EFFORT": "Claude Code `effort` outside the levels, or OpenCode `variant` outside the variants",
    "MISSING_TWIN": "a `-specialist` without its `-verifier`, or the reverse",
    "DUPLICATE_NAME": "two files declare one name; the harness picks one by filesystem read order",
    "VERIFIER_WRITE_TOOL": (
        "a verifier lists Edit, MultiEdit, NotebookEdit, an unguarded Bash, or a Write with no `PreToolUse` hook on Write whose command "
        "carries `# agent-definitions:verifier-write-guard`; on OpenCode it permits a write tool with `allow` or `ask`, except an "
        "`edit` map that opens with `\"*\": deny` and allows only patterns that start with `../`, beside an `external_directory` map "
        "that opens with `\"*\": deny` and allows only temp prefixes; or its `bash` map does not open with `\"*\": deny`, "
        "or that map resolves a write command to allow"
    ),
    "VERIFIER_BASH_UNGUARDED": (
        "a Claude Code verifier lists `Bash` with no `PreToolUse` hook on Bash whose command carries `# agent-definitions:verifier-guard`"
    ),
    "RULE_SHADOWED": "an OpenCode permission rule placed before `\"*\"` in the same map; the last matching rule wins, so it does nothing",
    "VERIFIER_NOT_READONLY": (
        "a Claude Code verifier with no `tools` allowlist; an OpenCode verifier whose first rule is not `\"*\": deny`"
    ),
    "SPECIALIST_CAN_PUSH": (
        "a `-specialist` with no push guard: on Claude Code no `PreToolUse` hook that selects Bash and names `push`; on OpenCode "
        "no `permission.bash` deny that covers `git push`. A deny that the catch-all shadows does not count"
    ),
    "BAD_FRONTMATTER": "frontmatter absent on line 1, unclosed, or not YAML",
    "NAME_INVALID": "a name that starts with a hyphen or holds a character outside letters, digits, hyphen and underscore, so the harness would skip the file",
    "MISSING_NAME": "a Claude Code file with no name; the harness treats it as documentation",
    "MISSING_DESCRIPTION": "a name with no description; the harness skips the file, and OpenCode needs a description for routing",
    "BAD_TYPE": "OpenCode `tools` as a string; that aborts config load for the whole session",
    "CROSS_HARNESS_MISMATCH": "an agent present for one harness and absent for the other",
    "MISSING_SKILL": "a preloaded skill absent from `--skills-dir`",
    "DESCRIPTION_BUDGET": "combined Claude Code descriptions exceed the budget in `tiers.toml`",
    "LOAD_BLOCK_NOT_PLAIN": (
        "a load-time block (`` ```! ``) of a preloaded skill holds, outside quotes, `$(`, a backtick, `;`, `&&`, a double pipe, "
        "a pipe, `if` or a redirection; Claude Code cannot analyse the command, so a subagent that preloads the skill does not "
        "start (ADR 0006). The finding gives the file and the line"
    ),
    "LOAD_BLOCK_IN_READ_SKILL": (
        f"a preloaded skill that carries `{CONTEXT_LOAD_READ_MARKER}` (a context skill with `contextLoad = \"read\"`) "
        "holds a load-time block (`` ```! ``); that skill must read its references with the Read tool only. "
        "The finding gives the file and the line"
    ),
}


@dataclass(frozen=True)
class Finding:
    code: str
    file: str
    message: str
    line: int | None = None

    def __post_init__(self) -> None:
        if self.code not in FINDING_CODES:
            raise ValueError(f"finding code {self.code!r} is not in FINDING_CODES, so the reference would not list it")

    def __str__(self) -> str:
        where = self.file if self.line is None else f"{self.file}:{self.line}"
        return f"{self.code} {where}: {self.message}"


def split_frontmatter(text: str) -> tuple[dict | None, str, str | None]:
    """Return (frontmatter, body, error). Frontmatter must open on line 1."""
    if not text.startswith("---\n"):
        return None, text, "no frontmatter on the first line"
    end = text.find("\n---", 4)
    if end == -1:
        return None, text, "frontmatter never closes"
    raw = text[4:end]
    try:
        fm = yaml.safe_load(raw)
    except yaml.YAMLError as e:  # pragma: no cover - message text varies
        return None, text, f"frontmatter YAML does not parse: {e}"
    if not isinstance(fm, dict):
        return None, text, "frontmatter is not a mapping"
    return fm, text[end + 4 :], None


def _tools_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    return [str(t) for t in value]


def _pair_findings(names: set[str], where: str) -> list[Finding]:
    out = []
    for n in sorted(names):
        if n.endswith("-specialist") and n[: -len("-specialist")] + "-verifier" not in names:
            out.append(Finding("MISSING_TWIN", where, f"{n} has no verifier twin"))
        if n.endswith("-verifier") and n[: -len("-verifier")] + "-specialist" not in names:
            out.append(Finding("MISSING_TWIN", where, f"{n} has no specialist twin"))
    return out


def _matcher_selects(matcher, tool: str) -> bool:
    """Claude Code matches a hook matcher against the tool name as a regular
    expression. An absent or empty matcher selects every tool."""
    if matcher is None or matcher == "" or matcher == "*":
        return True
    try:
        return re.search(str(matcher), tool) is not None
    except re.error:
        return str(matcher) == tool


def _claude_code_blocks_push(fm: dict) -> bool:
    """A Claude Code push guard is a PreToolUse hook that selects Bash and runs
    a command that names push. The hook is the only per-agent instrument:
    a disallowedTools entry with a specifier removes Bash completely."""
    hooks = fm.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for entry in hooks.get("PreToolUse") or []:
        if not isinstance(entry, dict) or not _matcher_selects(entry.get("matcher"), "Bash"):
            continue
        for hook in entry.get("hooks") or []:
            if isinstance(hook, dict) and "push" in str(hook.get("command", "")):
                return True
    return False


def _claude_code_guarded(fm: dict, tool: str, marker: str) -> bool:
    """A guarded tool is a PreToolUse hook that selects the tool and whose
    command carries the guard marker of that tool: VERIFIER_GUARD_MARKER for
    Bash, VERIFIER_WRITE_GUARD_MARKER for Write. The marker is the stable
    evidence: the hook text itself changes each time the allow list changes."""
    hooks = fm.get("hooks")
    if not isinstance(hooks, dict):
        return False
    for entry in hooks.get("PreToolUse") or []:
        if not isinstance(entry, dict) or not _matcher_selects(entry.get("matcher"), tool):
            continue
        for hook in entry.get("hooks") or []:
            if isinstance(hook, dict) and marker in str(hook.get("command", "")):
                return True
    return False


def _opencode_outside_only(value) -> bool:
    """An OpenCode file-write map that opens only a path outside the project.

    It opens with its own catch-all deny, and each other rule is a deny or an
    allow whose pattern starts with "../". OpenCode matches the rule against
    the path relative to the worktree, and a path in the project never starts
    with "../". An ask is not accepted: `--auto` turns it into an allow."""
    if not isinstance(value, dict) or not value:
        return False
    keys = list(value)
    if keys[0] != "*" or value["*"] != "deny":
        return False
    return all(action == "deny" or (action == "allow" and str(pattern).startswith("../")) for pattern, action in value.items())


def _opencode_temp_only(value) -> bool:
    """An OpenCode external_directory map that opens only a temp directory.

    It opens with its own catch-all deny, and each allow pattern starts with a
    fixed temp prefix and has no ".." component."""
    if not isinstance(value, dict) or not value:
        return False
    keys = list(value)
    if keys[0] != "*" or value["*"] != "deny":
        return False
    return all(
        action == "deny"
        or (action == "allow" and str(pattern).startswith(VERIFIER_WRITE_PATH_PREFIXES) and ".." not in str(pattern).split("/"))
        for pattern, action in value.items()
    )


def _opencode_blocks_push(fm: dict) -> bool:
    """An OpenCode push guard is a permission.bash deny that covers git push.
    Any spelling of the pattern counts, because the check resolves the map the
    way OpenCode does instead of comparing the key text. Every probe must
    resolve to a deny: one deny that leaves another probe allowed is a hole."""
    permission = fm.get("permission")
    if not isinstance(permission, dict):
        return False
    return all(opencode_bash_action(permission, probe) == "deny" for probe in PUSH_PROBES)


def validate_claude_code_dir(d: Path, tiers: Tiers) -> list[Finding]:
    d = Path(d)
    findings: list[Finding] = []
    seen: dict[str, str] = {}
    desc_chars = 0
    for f in sorted(d.glob("*.md")):
        fm, _body, err = split_frontmatter(f.read_text())
        if err:
            findings.append(Finding("BAD_FRONTMATTER", f.name, err))
            continue
        name = fm.get("name")
        if not name:
            findings.append(Finding("MISSING_NAME", f.name, "no name; Claude Code treats the file as documentation"))
            continue
        if not NAME_RE.match(str(name)) or ":" in str(name):
            findings.append(Finding("NAME_INVALID", f.name, f"name {name!r} must not start with '-' or contain ':'"))
        if not fm.get("description"):
            findings.append(Finding("MISSING_DESCRIPTION", f.name, "name without description; the harness skips the file"))
        desc_chars += len(str(fm.get("description", "")))
        unknown = set(fm) - CLAUDE_CODE_KEYS
        for k in sorted(unknown):
            findings.append(Finding("UNKNOWN_KEY", f.name, f"{k!r} is not a Claude Code agent frontmatter field; it is inert"))
        if "model" in fm and fm["model"] not in tiers.allowed_models["claude-code"]:
            findings.append(Finding("BAD_MODEL", f.name, f"model {fm['model']!r} is not in the tier table's allowed set"))
        if "effort" in fm and fm["effort"] not in tiers.effort_levels:
            findings.append(Finding("BAD_EFFORT", f.name, f"effort {fm['effort']!r} is not one of {tiers.effort_levels}"))
        if name in seen:
            findings.append(Finding("DUPLICATE_NAME", f.name, f"name {name!r} also declared by {seen[name]}"))
        seen.setdefault(str(name), f.name)
        if str(name).endswith("-specialist") and not _claude_code_blocks_push(fm):
            findings.append(Finding("SPECIALIST_CAN_PUSH", f.name, "no PreToolUse hook on Bash blocks git push, so the specialist can push"))
        if str(name).endswith("-verifier"):
            tools = _tools_list(fm.get("tools"))
            if not tools:
                findings.append(Finding("VERIFIER_NOT_READONLY", f.name, "verifier has no tools allowlist, so it inherits write tools"))
            # Bash on a verifier is legal only beside the guard hook. Without
            # the guard the verifier has a full shell, so both findings fire.
            guarded = _claude_code_guarded(fm, "Bash", VERIFIER_GUARD_MARKER)
            if "Bash" in tools and not guarded:
                findings.append(Finding("VERIFIER_BASH_UNGUARDED", f.name, f"verifier lists Bash with no PreToolUse hook on Bash that carries {VERIFIER_GUARD_MARKER!r}"))
            # Write on a verifier is legal only beside the Write guard, which
            # keeps each Write in a temp directory.
            write_guarded = _claude_code_guarded(fm, "Write", VERIFIER_WRITE_GUARD_MARKER)
            write_tools = CLAUDE_CODE_WRITE_TOOLS - ({"Bash"} if guarded else set()) - ({"Write"} if write_guarded else set())
            bad = sorted(set(tools) & write_tools)
            if bad:
                unguarded = f"; Write needs a PreToolUse hook on Write that carries {VERIFIER_WRITE_GUARD_MARKER!r}" if "Write" in bad else ""
                findings.append(Finding("VERIFIER_WRITE_TOOL", f.name, f"verifier lists write-capable tools {bad}{unguarded}"))
    findings.extend(_pair_findings(set(seen), str(d)))
    if desc_chars > tiers.description_budget_chars:
        findings.append(Finding("DESCRIPTION_BUDGET", str(d), f"combined descriptions are {desc_chars} chars; budget is {tiers.description_budget_chars}"))
    return findings


def validate_opencode_dir(d: Path, tiers: Tiers) -> list[Finding]:
    d = Path(d)
    findings: list[Finding] = []
    seen: dict[str, str] = {}
    for f in sorted(d.glob("*.md")):
        fm, _body, err = split_frontmatter(f.read_text())
        if err:
            findings.append(Finding("BAD_FRONTMATTER", f.name, err))
            continue
        name = f.stem  # OpenCode names a Markdown agent by its filename
        if not fm.get("description"):
            findings.append(Finding("MISSING_DESCRIPTION", f.name, "OpenCode agents need a description for routing"))
        for k in sorted(set(fm) - OPENCODE_KEYS):
            findings.append(Finding("UNKNOWN_KEY", f.name, f"{k!r} is not an OpenCode agent field; it is swept into provider options"))
        if "model" in fm and fm["model"] not in tiers.allowed_models["opencode"]:
            findings.append(Finding("BAD_MODEL", f.name, f"model {fm['model']!r} is not in the tier table's allowed set"))
        if "variant" in fm and fm["variant"] not in tiers.opencode_variants:
            findings.append(Finding("BAD_EFFORT", f.name, f"variant {fm['variant']!r} is not one of {tiers.opencode_variants}"))
        if "tools" in fm and isinstance(fm["tools"], str):
            findings.append(Finding("BAD_TYPE", f.name, "tools must be a map; a string aborts OpenCode config load"))
        if name in seen:
            findings.append(Finding("DUPLICATE_NAME", f.name, f"name {name!r} also declared by {seen[name]}"))
        seen.setdefault(name, f.name)
        perm_any = fm.get("permission")
        if isinstance(perm_any, dict):
            findings.extend(_shadowed_rules(perm_any, f.name))
        if name.endswith("-specialist") and not _opencode_blocks_push(fm):
            findings.append(Finding("SPECIALIST_CAN_PUSH", f.name, "no permission.bash deny covers git push, so the specialist can push"))
        if name.endswith("-verifier"):
            perm = fm.get("permission")
            if not isinstance(perm, dict) or not perm:
                findings.append(Finding("VERIFIER_NOT_READONLY", f.name, "verifier has no permission map"))
            else:
                first_key, first_val = next(iter(perm.items()))
                if first_key != "*" or first_val != "deny":
                    findings.append(Finding("VERIFIER_NOT_READONLY", f.name, 'the first permission rule must be "*": deny; the last matching rule wins'))
                outside = False
                for t in sorted(OPENCODE_WRITE_TOOLS):
                    v = perm.get(t)
                    if t in OPENCODE_FILE_WRITE_TOOLS and _opencode_outside_only(v):
                        # A file write outside the project only. The
                        # external_directory check below keeps it in a temp
                        # directory.
                        outside = outside or any(a == "allow" for a in v.values())
                        continue
                    if t == "bash" and isinstance(v, dict):
                        # A guarded bash map: its own catch-all deny first, then
                        # read patterns. The probes resolve the map the way
                        # OpenCode does, so the spelling of a pattern is free.
                        keys = list(v)
                        if not keys or keys[0] != "*" or v[keys[0]] != "deny":
                            findings.append(Finding("VERIFIER_WRITE_TOOL", f.name, 'the verifier bash map must open with "*": deny; the last matching rule wins'))
                        for probe in VERIFIER_BASH_WRITE_PROBES:
                            if opencode_bash_action(perm, probe) in ("allow", "ask"):
                                findings.append(Finding("VERIFIER_WRITE_TOOL", f.name, f"the verifier bash map permits {probe!r}"))
                        continue
                    if v == "allow" or v == "ask" or (isinstance(v, dict) and any(x in ("allow", "ask") for x in v.values())):
                        findings.append(Finding("VERIFIER_WRITE_TOOL", f.name, f"verifier permits {t!r}; build read-only from deny, never ask"))
                if outside and not _opencode_temp_only(perm.get("external_directory")):
                    findings.append(Finding(
                        "VERIFIER_WRITE_TOOL", f.name,
                        "the verifier writes outside the project, and its external_directory map does not open with "
                        '"*": deny and allow only temp prefixes, so it can write outside a temp directory',
                    ))
    findings.extend(_pair_findings(set(seen), str(d)))
    return findings


def _shadowed_rules(perm: dict, fname: str) -> list[Finding]:
    """OpenCode keeps the last matching rule. Any rule that precedes a catch-all
    "*" in the same map is shadowed by it and does nothing."""
    out: list[Finding] = []

    def check(mapping: dict, where: str) -> None:
        keys = list(mapping)
        if "*" in keys:
            star = keys.index("*")
            for k in keys[:star]:
                out.append(Finding("RULE_SHADOWED", fname, f"{where}{k!r} precedes '*' and is shadowed by it; put '*' first"))

    check(perm, "permission.")
    for tool, val in perm.items():
        if isinstance(val, dict):
            check(val, f"permission.{tool}.")
    return out


def _names(d: Path, harness: str) -> set[str]:
    out = set()
    for f in Path(d).glob("*.md"):
        fm, _b, err = split_frontmatter(f.read_text())
        if err:
            continue
        out.add(str(fm.get("name")) if harness == "claude-code" and fm.get("name") else f.stem)
    return out


def _refused_forms(command: str) -> list[str]:
    """The shell forms of one command line that make it more than one plain command.

    A sign in single quotes is text. In double quotes only `$(` and a backtick
    keep their meaning. A backslash outside single quotes escapes the next sign.
    """
    forms: list[str] = []
    unquoted: list[str] = []

    def add(form: str) -> None:
        if form not in forms:
            forms.append(form)

    quote = None
    i = 0
    while i < len(command):
        c = command[i]
        if quote == "'":
            quote = None if c == "'" else quote
            unquoted.append(" ")
            i += 1
            continue
        if c == "\\":
            unquoted.append("  ")
            i += 2
            continue
        if command.startswith("$(", i):
            add("`$(`")
        elif c == "`":
            add("a backtick")
        if quote == '"':
            quote = None if c == '"' else quote
            unquoted.append(" ")
            i += 1
            continue
        if c in "'\"":
            quote = c
            unquoted.append(" ")
            i += 1
            continue
        if command.startswith(("&&", "||"), i):
            add(f"`{command[i:i + 2]}`")
            unquoted.append("  ")
            i += 2
            continue
        if c == "|":
            add("`|`")
        elif c == ";":
            add("`;`")
        elif c in "<>":
            add("a redirection")
        unquoted.append(c)
        i += 1
    if "if" in re.split(r"[\s;&|<>()]+", "".join(unquoted)):
        add("`if`")
    return forms


def load_time_block_findings(skill: Path) -> list[Finding]:
    """One LOAD_BLOCK_NOT_PLAIN finding for each load-time block of a skill that is not one plain command.

    A skill that carries CONTEXT_LOAD_READ_MARKER (bootstrap with
    contextLoad = "read") also gives one LOAD_BLOCK_IN_READ_SKILL finding for
    each load-time block, plain or not, at the line that opens the block.

    A load-time block is a fenced block that opens with ```! . Claude Code runs
    its command when it loads the skill, and only when it can analyse the
    command: a block with shell syntax stops a subagent that preloads the skill
    from starting (ADR 0006). The finding gives the first line of the block
    that holds a refused form. The content of another fenced block is not a
    command, so the check skips it.
    """
    findings: list[Finding] = []
    in_fence = False
    load_time = False
    forms: list[str] = []
    first = None
    lines = skill.read_text().splitlines()
    # A skill written with contextLoad = "read" must hold no load-time block.
    read_only_load = any(line.strip() == CONTEXT_LOAD_READ_MARKER for line in lines)
    for number, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not in_fence:
            if stripped.startswith("```"):
                in_fence = True
                load_time = stripped[3:].strip() == "!"
                forms, first = [], None
                if load_time and read_only_load:
                    findings.append(Finding(
                        "LOAD_BLOCK_IN_READ_SKILL", str(skill),
                        "this skill was written with contextLoad = \"read\", and it holds a load-time block; "
                        "remove the block, and read the document with the Read tool",
                        line=number,
                    ))
            continue
        if stripped == "```":
            if load_time and forms:
                findings.append(Finding(
                    "LOAD_BLOCK_NOT_PLAIN", str(skill),
                    f"the load-time block holds {', '.join(forms)}; Claude Code cannot analyse it, "
                    "so a subagent that preloads this skill does not start. Write one plain command (ADR 0006)",
                    line=first,
                ))
            in_fence = False
            continue
        if load_time:
            found = _refused_forms(line)
            if found and first is None:
                first = number
            forms += [f for f in found if f not in forms]
    return findings


def validate_set(
    tiers: Tiers,
    claude_code_dir: Path | None = None,
    opencode_dir: Path | None = None,
    skills_dir: Path | None = None,
    user_skills_dir: Path | None = None,
) -> list[Finding]:
    findings: list[Finding] = []
    if claude_code_dir is not None:
        findings += validate_claude_code_dir(claude_code_dir, tiers)
    if opencode_dir is not None:
        findings += validate_opencode_dir(opencode_dir, tiers)
    if claude_code_dir is not None and opencode_dir is not None:
        a, b = _names(claude_code_dir, "claude-code"), _names(opencode_dir, "opencode")
        for n in sorted(a - b):
            findings.append(Finding("CROSS_HARNESS_MISMATCH", n, "present for Claude Code, absent for OpenCode"))
        for n in sorted(b - a):
            findings.append(Finding("CROSS_HARNESS_MISMATCH", n, "present for OpenCode, absent for Claude Code"))
    if skills_dir is not None and claude_code_dir is not None:
        checked: set[Path] = set()
        for f in sorted(Path(claude_code_dir).glob("*.md")):
            fm, _b, err = split_frontmatter(f.read_text())
            if err:
                continue
            for s in fm.get("skills", []) or []:
                in_project = (Path(skills_dir) / s / "SKILL.md").exists()
                in_user = user_skills_dir is not None and (Path(user_skills_dir) / s / "SKILL.md").exists()
                # Each copy of the skill is checked: the harness loads one of them.
                for root, present in ((skills_dir, in_project), (user_skills_dir, in_user)):
                    path = Path(root) / s / "SKILL.md" if present else None
                    if path is not None and path not in checked:
                        checked.add(path)
                        findings += load_time_block_findings(path)
                if not in_project and not in_user:
                    if user_skills_dir is not None:
                        findings.append(Finding(
                            "MISSING_SKILL", f.name,
                            f"preloaded skill {s!r} is not in {skills_dir} or {user_skills_dir}",
                        ))
                    else:
                        findings.append(Finding(
                            "MISSING_SKILL", f.name,
                            f"preloaded skill {s!r} is not in {skills_dir}",
                        ))
    return findings
