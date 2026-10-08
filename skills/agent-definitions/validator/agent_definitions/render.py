"""Render one declaration into complete agent files per harness.

No harness composes agent files, so every file is complete. A pair emits a
specialist and a verifier; the verifier shares the domain, the skills, and the
references, and differs in a read-only posture and the verifier tier.
"""

from __future__ import annotations

from importlib import resources
from string import Template

import yaml

from .declaration import Agent
from .tiers import Tiers

READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "Bash"]
OPENCODE_READ_TOOLS = ["read", "grep", "glob", "list"]

# --- The verifier's guarded Bash --------------------------------------------
#
# A verifier with no Bash cannot run the gates, so the coordinator runs them
# instead. The verifier keeps Bash and loses every command that writes. The
# tables below are the single source for three artifacts: the Claude Code hook,
# the OpenCode permission map, and the table in SKILL.md.

#: The marker that makes the guard identifiable in a rendered file. The hook
#: text changes with the tables; the marker does not.
VERIFIER_GUARD_MARKER = "# agent-definitions:verifier-guard"

#: Commands the verifier may run. Each entry matches the command alone or the
#: command with arguments, and never a longer command name: "ls" does not match
#: "lsof". The list holds only what every verifier needs. A build tool or a
#: tracker CLI is one repository's gate: its declaration names it in
#: gateCommands or getOnlyCommands.
VERIFIER_READ_COMMANDS: tuple[str, ...] = (
    "git diff",
    "git log",
    "git show",
    "git status",
    "git ls-files",
    "git ls-tree",
    "git rev-parse",
    "git merge-base",
    "git worktree list",
    "git branch --show-current",
    "ls",
    "cat",
    "head",
    "tail",
    "sed -n",
    "grep",
    "rg",
    "find",
    "wc",
    "diff",
    "jq",
    "shasum",
    "stat",
    "readlink",
    "file",
    "which",
    "env",
    "pwd",
    "echo",
    "printf",
    "date",
    "mktemp",
    "python3",
    "bash -c",
)

#: Commands that write. The guard permits one only when every path argument is
#: in a temp directory.
VERIFIER_TEMP_WRITE_COMMANDS: tuple[str, ...] = ("mkdir", "mv", "rm", "tee", "touch")

#: Commands the guard permits when the LAST path argument is in a temp
#: directory. The earlier path arguments are sources, and a read of any path is
#: a read. The verifier method needs this: it copies the worktree into a temp
#: directory to prove a check red. For "cp" the last path is the destination;
#: for "cd" it is the new working directory. "mv" stays out of this group: it
#: removes its source.
VERIFIER_TEMP_DEST_COMMANDS: tuple[str, ...] = ("cp", "cd")

VERIFIER_TEMP_PATH_PREFIXES: tuple[str, ...] = ("/tmp/", "/private/tmp/", "$TMPDIR", "/var/folders/")


def _exact_or_prefix(commands: tuple[str, ...]) -> str:
    """A bash case pattern. It matches each command alone or with arguments."""
    return "|".join(f'"{c}"|"{c} "*' for c in commands)


def _temp_path_pattern() -> str:
    """A bash case pattern for a path in a temp directory."""
    return "|".join(f"'{p}'*" if "$" in p else f"{p}*" for p in VERIFIER_TEMP_PATH_PREFIXES)


def _verifier_bash_hook(gates: tuple[str, ...] = (), get_only: tuple[str, ...] = ()) -> str:
    """Build the verifier's Bash guard as one bash line from the tables above.

    The guard divides the command at &&, ||, ; and |, and it examines each
    segment. One segment that is not on the allow list denies the full command,
    and the message names that segment.

    A segment that starts with "git -C <path>" loses that prefix before the
    test, so a read of a repository by path is the same read as a read of the
    current repository. A verifier with no worktree of its own reads a worktree
    by its path. The subcommand after the path is still tested, so
    "git -C <path> push" stays a deny.

    A segment that starts with "nix develop -c" or "nix develop <flake-ref> -c"
    (or --command) loses that prefix in the same way, so a gate that runs in a
    flake dev shell is tested as the gate command itself. Only a flake
    reference may come before -c: a flag there can write (--profile writes a
    symlink, --build runs build phases), so a hyphen-led word denies. Without
    -c, nix develop starts an interactive shell, and it stays a deny.

    A segment that starts with "direnv exec <dir>" loses that prefix too, so a
    gate that runs with a repository's .envrc is tested as the gate command.
    "direnv allow" writes direnv's allow list, and it stays a deny.

    `gates` are the repository's gate commands from its declaration. The
    gates differ in each repository, so the global list holds none of them
    (ADR 0004). Each one is permitted like a read command, in that verifier
    only.

    `get_only` are the declaration's commands that read only while they stay a
    GET, such as a tracker API client. Each one is permitted alone or with
    arguments, in that verifier only, and a segment that holds " -X" is a
    deny. With no such command the branch is absent.

    A temp path is a text prefix and a path with no ".." component. The guard
    rejects "..", it does not resolve it, because it reads the command as text
    and a resolution needs the file system.

    The guard fails closed. No jq, no command text, or a shell without the bash
    string operators all end at exit 2, which blocks the call.
    """
    quote = '\'"\'*|"\'"*'  # a leading double quote or a leading single quote
    redirect = "A redirection writes outside a temp directory."
    # The shell removes a redirection before it runs the command, so a path
    # that comes after one is still a path argument. A scan that stopped at the
    # redirection read the source as the destination: "cp /tmp/a >/tmp/log
    # /Users/x" writes to /Users/x.
    after_redirect = "A path argument comes after a redirection."
    # GNU cp takes its destination from -t or --target-directory, and a word
    # that starts with a hyphen is otherwise dropped as a flag. Two literal
    # prefixes do not hold the class: GNU short options cluster, so "cp -Rt
    # /Users/y /tmp/a" still takes the next word as the destination, and a GNU
    # long option accepts an unambiguous abbreviation such as "--targ". The
    # guard therefore denies every hyphen-led word that holds "t" or "T", and
    # every long option that starts with "--t" or "--n". For "cp" and "cd" the
    # only short option with a lower-case "t" is -t itself, the only long
    # options that start with "--t" are the target-directory pair, and -T with
    # --no-target-directory changes what the last path means.
    target_flag = "A flag that holds t or T can move the destination, so it is not permitted. Give the destination as the last path."
    get_only_branch = (
        f"{_exact_or_prefix(get_only)}) "
        'case "$seg" in *" -X"*) deny "$seg" "This command reads only; -X makes it a write." ;; esac ;; '
        if get_only
        else ""
    )
    segment = (
        '[ -n "$seg" ] || continue; '
        "set -- $seg; "
        "r=0; "
        'for a in "$@"; do '
        f'if [ "$r" = 1 ]; then r=0; istmp "$a" || deny "$seg" "{redirect}"; continue; fi; '
        'case "$a" in '
        "*'>&'*) ;; "
        "'>'|'>>'|'1>'|'1>>'|'2>'|'2>>'|'&>'|'&>>') r=1 ;; "
        f'*\'>\'*) istmp "${{a##*>}}" || deny "$seg" "{redirect}" ;; '
        "esac; "
        "done; "
        'g=$seg; case "$seg" in "git -C "*) t=${seg#git -C }; g="git ${t#* }" ;; esac; '
        'case "$g" in "direnv exec "*) t=${g#direnv exec }; '
        'case "$t" in *" "*) g=${t#* } ;; *) g="direnv exec" ;; esac ;; esac; '
        'case "$g" in "nix develop "*) t=${g#nix develop }; '
        'case "$t" in -*) ;; *) t=${t#* } ;; esac; '
        'case "$t" in "-c "*|"--command "*) g=${t#* } ;; *) g="nix develop" ;; esac ;; esac; '
        # The write tests below read the arguments from $@: take them from the
        # command after the prefix, not from the segment.
        "set -- $g; "
        'case "$g" in '
        f"{get_only_branch}"
        f"{_exact_or_prefix(VERIFIER_READ_COMMANDS + gates)}) ;; "
        f"{_exact_or_prefix(VERIFIER_TEMP_DEST_COMMANDS)}) shift; d=; q=0; k=0; "
        'for a in "$@"; do '
        'if [ "$k" = 1 ]; then k=0; continue; fi; '
        'case "$a" in '
        f'--t*|--n*|-*t*|-*T*) deny "$seg" "{target_flag}" ;; '
        "-*) continue ;; "
        "'>'|'>>'|'1>'|'1>>'|'2>'|'2>>'|'&>'|'&>>'|'<'|'<<'|'0<') q=1; k=1; continue ;; "
        "*'>'*|*'<'*) q=1; continue ;; "
        "esac; "
        f'[ "$q" = 0 ] || deny "$seg" "{after_redirect}"; '
        "d=$a; "
        "done; "
        '[ -n "$d" ] || deny "$seg" "This command needs a temp path argument."; '
        'istmp "$d" || deny "$seg" "The last path argument is outside a temp directory." ;; '
        f"{_exact_or_prefix(VERIFIER_TEMP_WRITE_COMMANDS)}) shift; n=0; "
        'for a in "$@"; do case "$a" in -*) continue ;; esac; '
        'if istmp "$a"; then n=$((n+1)); else deny "$seg" "A path argument is outside a temp directory."; fi; '
        "done; "
        '[ "$n" -gt 0 ] || deny "$seg" "A write command needs a temp path argument." ;; '
        '*) deny "$seg" "Only read commands and temp-directory writes are permitted." ;; '
        "esac;"
    )
    return "; ".join(
        [
            'deny() { echo "verifier guard: the command segment [$1] is not permitted. $2" >&2; exit 2; }',
            f'istmp() {{ p=$1; case "$p" in {quote}) p=${{p#?}};; esac; '
            'case "$p" in ..|../*|*/..|*/../*) return 1;; esac; '
            f'case "$p" in {_temp_path_pattern()}) return 0;; esac; return 1; }}',
            'cmd=$(jq -r ".tool_input.command // empty" 2>/dev/null)',
            '[ -n "$cmd" ] || { echo "verifier guard: no command text, or jq is absent. '
            'The guard fails closed." >&2; exit 2; }',
            # A tab becomes a space first: the prefix strips cut at a space,
            # and a tab between words must not hide a flag or a write.
            "s=${cmd//$'\\t'/ }",
            "s=${s//&&/$'\\n'}",
            "s=${s//||/$'\\n'}",
            "s=${s//;/$'\\n'}",
            "s=${s//|/$'\\n'}",
            "set -f",
            f'while read -r seg; do {segment} done <<< "$s"',
            f"exit 0 {VERIFIER_GUARD_MARKER}",
        ]
    )


VERIFIER_BASH_HOOK = _verifier_bash_hook()


def verifier_bash_permission(gates: tuple[str, ...] = ()) -> dict[str, str]:
    """The OpenCode permission.bash map for a verifier.

    The catch-all deny is the first key, because OpenCode keeps the last
    matching rule. Each read command becomes "<command> *": OpenCode treats a
    trailing " *" as optional, so one pattern covers the bare command and the
    command with arguments, and it does not match a longer command name.

    This map is smaller than the Claude Code allow list on purpose. An OpenCode
    pattern cannot examine a path argument, so the temp-write commands and the
    GET-only commands stay out.
    """
    perm: dict[str, str] = {"*": "deny"}
    for command in VERIFIER_READ_COMMANDS + gates:
        perm[f"{command} *"] = "allow"
    return perm

# A PreToolUse hook on Bash. Exit 2 blocks the call and returns stderr to the
# agent. A disallowedTools entry with a specifier would remove Bash entirely,
# so the block is a hook. Fail-open when jq is absent: jq exits 127, the &&
# chain skips, and the command exits 0.
GIT_PUSH_HOOK = (
    "jq -e '(.tool_input.command // \"\") | test(\"\\\\bgit(\\\\s+-C\\\\s+\\\\S+)?\\\\s+push\\\\b\")' "
    ">/dev/null && { echo 'git push is blocked for specialist agents. Report the branch to the coordinator.' >&2; exit 2; }; exit 0"
)


#: The language rule that opens the Report section, by outputLanguage. The
#: default "none" adds no rule, so the section opens with its content.
OUTPUT_LANGUAGE_RULES: dict[str, str] = {
    "none": "",
    "ste": "Write in ASD-STE100 Simplified Technical English. ",
}

#: The reason that follows the staging rule in the Claude Code specialist when
#: trackedFileBuild is true. The staging rule itself is in every specialist.
TRACKED_FILE_REASON = " An unstaged file is invisible to a flake."


def _template(name: str) -> Template:
    return Template(resources.files(__package__).joinpath("templates", f"{name}.md").read_text())


def _frontmatter(fm: dict) -> str:
    # The width is large because the verifier guard is one long line. A fold
    # inside a quoted scalar puts a line break where the hook has a space, and
    # a reader then cannot copy the hook out of the file.
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100000) + "---\n"


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def _body(template: str, agent: Agent, name: str, twin: str) -> str:
    return _template(template).substitute(
        name=name,
        twin=twin,
        domain=agent.key,
        description=agent.description,
        skills=", ".join(agent.skills) if agent.skills else "none declared",
        references=_bullets(agent.references, "none declared beyond CLAUDE.md and the delegation doc"),
        prompt=agent.prompt or "(no domain notes declared)",
        output_language=OUTPUT_LANGUAGE_RULES[agent.output_language],
        tracked_file_reason=TRACKED_FILE_REASON if agent.tracked_file_build else "",
    )


def _roles(agent: Agent) -> list[tuple[str, bool, str]]:
    """(name, read_only, role) for each file the agent produces."""
    if agent.pair:
        return [
            (f"{agent.key}-specialist", False, "specialist"),
            (f"{agent.key}-verifier", True, "verifier"),
        ]
    return [(agent.key, agent.read_only, "verifier" if agent.read_only else "specialist")]


def render_claude_code(agent: Agent, tiers: Tiers) -> dict[str, str]:
    files: dict[str, str] = {}
    for name, read_only, role in _roles(agent):
        twin = f"{agent.key}-verifier" if role == "specialist" else f"{agent.key}-specialist"
        fm: dict = {"name": name}
        if read_only:
            fm["description"] = (
                f"{agent.key} verifier. {agent.description} Use it to review a diff in this domain. "
                "It is read-only and blind. It returns ACCEPT or REJECT with evidence."
            )
            fm["tools"] = ", ".join(READ_ONLY_TOOLS)
        else:
            fm["description"] = (
                f"{agent.key} specialist. {agent.description} Use it for one implementation task in this domain. "
                "It works in an isolated worktree and never pushes."
            )
        # model is omitted on purpose: the spawn call sets it, and effort
        # follows the model through modelSettings (ADR 0001).
        if agent.effort:
            fm["effort"] = agent.effort
        if agent.skills:
            fm["skills"] = list(agent.skills)
        if agent.max_turns:
            fm["maxTurns"] = agent.max_turns
        # Each half gets a PreToolUse hook on Bash, and the two hooks differ.
        # The specialist keeps a full shell and loses "git push". The verifier
        # keeps only the read commands and the gates: with no shell it cannot
        # run the gates, and with a full shell it can write.
        guard = (
            _verifier_bash_hook(tuple(agent.gate_commands), tuple(agent.get_only_commands))
            if read_only
            else GIT_PUSH_HOOK
        )
        if not read_only:
            fm["isolation"] = "worktree"
        fm["hooks"] = {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": guard}]}]
        }
        files[f"{name}.md"] = _frontmatter(fm) + _body(f"claude-code-{role}", agent, name, twin if agent.pair else "")
    return files


def render_opencode(agent: Agent, tiers: Tiers) -> dict[str, str]:
    files: dict[str, str] = {}
    for name, read_only, role in _roles(agent):
        twin = f"{agent.key}-verifier" if role == "specialist" else f"{agent.key}-specialist"
        tier = "verifier" if read_only else agent.tier
        fm: dict = {
            "description": (
                f"{agent.key} verifier. {agent.description} Read-only, blind review. Returns ACCEPT or REJECT with evidence."
                if read_only
                else f"{agent.key} specialist. {agent.description} One implementation task. Never pushes."
            ),
            "mode": "subagent",
            "model": tiers.model_for(tier, "opencode"),
        }
        if agent.effort and agent.effort in tiers.opencode_variants:
            fm["variant"] = agent.effort
        if agent.max_turns:
            fm["steps"] = agent.max_turns
        if read_only:
            # "*" must be the first key: rules match in order and the last
            # matching rule wins, so the catch-all deny goes first.
            perm: dict = {"*": "deny"}
            for t in OPENCODE_READ_TOOLS:
                perm[t] = "allow"
            # The bash map is a second catch-all deny with the read commands
            # after it. It does not replace the outer deny: without that outer
            # rule every other tool becomes available again.
            perm["bash"] = verifier_bash_permission(tuple(agent.gate_commands))
            fm["permission"] = perm
        else:
            # Catch-all first here too: OpenCode keeps the last matching rule,
            # so a deny placed before "*" is shadowed and the agent can push.
            # Two denies, because an OpenCode pattern is an anchored glob:
            # "git push*" becomes "^git push.*$" and does not cover the
            # "git -C <path> push" form that the Claude Code hook stops. The map
            # does not add a wider "*git push*" pattern (ADR 0002).
            fm["permission"] = {
                "bash": {"*": "allow", "git push*": "deny", "git -C * push*": "deny"}
            }
        files[f"{name}.md"] = _frontmatter(fm) + _body(f"opencode-{role}", agent, name, twin if agent.pair else "")
    return files


def render(agents: list[Agent], tiers: Tiers) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {"claude-code": {}, "opencode": {}}
    for agent in agents:
        for fname, content in render_claude_code(agent, tiers).items():
            if fname in out["claude-code"]:
                raise ValueError(f"duplicate agent file {fname}")
            out["claude-code"][fname] = content
        for fname, content in render_opencode(agent, tiers).items():
            out["opencode"][fname] = content
    return out
