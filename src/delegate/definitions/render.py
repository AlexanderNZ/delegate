"""Render one declaration into complete agent files per harness.

No harness composes agent files, so every file is complete. A pair emits a
specialist and a verifier; the verifier shares the domain, the skills, and the
references, and differs in a read-only posture and the verifier tier.
"""

from __future__ import annotations

from importlib import resources
from string import Template


from .declaration import READ_PREFIX_MARK, Agent
from .guard import (
    GIT_PUSH_HOOK,
    SPECIALIST_OPENCODE_BASH,
    VERIFIER_OPENCODE_EDIT,
    VERIFIER_WRITE_HOOK,
    free_gates,
    verifier_bash_hook,
    verifier_bash_permission,
    verifier_external_directory_permission,
)
from ..shared import yamlio
from ..shared.tiers import Tiers

READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "Bash"]
#: The tools of a Claude Code verifier: the read tools, the guarded Bash, and
#: the Write tool, which a guard hook limits to a temp directory.
VERIFIER_TOOLS = [*READ_ONLY_TOOLS, "Write"]
OPENCODE_READ_TOOLS = ["read", "grep", "glob", "list"]


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
    return "---\n" + yamlio.dump(fm, width=100000) + "---\n"


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def _read_prefixes(agent: Agent) -> str:
    """The paragraph of the Claude Code verifier that states each prefix entry of getOnlyCommands (#164).

    It is empty when the declaration has no such entry.
    """
    prefixes = [e[: -len(READ_PREFIX_MARK)] for e in agent.get_only_commands if e.endswith(READ_PREFIX_MARK)]
    if not prefixes:
        return ""
    stated = " ".join(f"The read command `{p}` is a prefix." for p in prefixes)
    return (
        f"\n\n{stated} The declaration marks each such command with a trailing `{READ_PREFIX_MARK.strip()}`, so the "
        "hook permits each word after it, except a method flag, a body flag, an output flag with a path outside a temp "
        "directory, and a word that the shell can expand. After each other read command of the declaration, the hook "
        "permits only options, option values, numbers and paths, because another word can be a subcommand that writes."
    )


def _free_gate_rule(agent: Agent) -> str:
    """The paragraph of the verifier that names each gate that takes free arguments (#169).

    The guard permits each word after such a gate, except a word that the
    shell can expand (#175), and the OpenCode map permits each word after it,
    also a word that the shell can expand. So the rule of #124 and #163 is in
    the text that the verifier reads. The text gives the exception of the
    guard and tells the verifier to put a pattern character in single quotes
    (#238). It is empty when the declaration has no such gate.
    """
    free = free_gates(agent.gate_commands)
    if not free:
        return ""
    stated = " ".join(f"The gate `{g}` takes free arguments." for g in free)
    return (
        f"\n\n{stated} The declaration marks each such gate with a trailing `{READ_PREFIX_MARK.strip()}`, so the "
        "guard of your shell permits each word after it, except a word that the shell can expand. Such a word holds "
        "an unquoted `{`, `}`, `*`, `?`, `[`, `]`, `(`, `)`, `^` or `~`, an unquoted `=` at the start, a `$` or a "
        "backtick. Put a word with a pattern character in single quotes, for example `-t 'a*'`. The guard denies a "
        "word with a `$` or a backtick also in quotes, except the temp prefix `$TMPDIR/`. "
        "This rule is binding. Do not put a word after such a gate that can make the gate write, for example "
        "`--fix`, `-u`, `--outDir` or a task that formats the source. If you must run such a form, report it under "
        "What you could not verify (the `unverified` list of a JSON verdict), so the coordinator runs it."
    )


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
        read_prefixes=_read_prefixes(agent),
        free_gates=_free_gate_rule(agent),
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
            fm["tools"] = ", ".join(VERIFIER_TOOLS)
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
            verifier_bash_hook(
                tuple(agent.gate_commands), tuple(agent.get_only_commands), tuple(agent.gate_extra_flags)
            )
            if read_only
            else GIT_PUSH_HOOK
        )
        if not read_only:
            fm["isolation"] = "worktree"
        pre_tool_use = [{"matcher": "Bash", "hooks": [{"type": "command", "command": guard}]}]
        if read_only:
            # The verifier writes its verdict with Write, and this guard keeps
            # every Write in a temp directory.
            pre_tool_use.append({"matcher": "Write", "hooks": [{"type": "command", "command": VERIFIER_WRITE_HOOK}]})
        fm["hooks"] = {"PreToolUse": pre_tool_use}
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
            # `all`, not `subagent`: `opencode run --agent` refuses a subagent and runs its default agent instead (#214).
            "mode": "all",
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
            # The write tool for the verdict: a path outside the project, and
            # that path only in a temp directory. Both maps open with their
            # own catch-all deny.
            perm["edit"] = dict(VERIFIER_OPENCODE_EDIT)
            perm["external_directory"] = verifier_external_directory_permission()
            fm["permission"] = perm
        else:
            # The catch-all comes first: see SPECIALIST_OPENCODE_BASH.
            fm["permission"] = {"bash": dict(SPECIALIST_OPENCODE_BASH)}
        files[f"{name}.md"] = _frontmatter(fm) + _body(f"opencode-{role}", agent, name, twin if agent.pair else "")
    return files


#: The version of the format of `guard_sets`. Increase it when a field changes
#: its meaning or goes away. A new field does not change the version.
GUARD_SETS_VERSION = 1


def guard_sets(agents: list[Agent]) -> dict:
    """The command sets of the guard of each verifier, as data (#237).

    The shell text of the guard is no interface: its form changed in v1.14.0,
    and a consumer that read the sets from it found no gate. This structure is
    the interface. The key of each agent is the name of the rendered verifier,
    and an agent that renders no verifier has no entry. Each list keeps the
    order of the declaration, and holds the words of each entry without the
    " *" mark:

    - `gates`: each gate (gateCommands): plain, free and exact.
    - `freeGates`: each gate that takes free arguments (#169).
    - `reads`: each read command (getOnlyCommands).
    - `readPrefixes`: each read command that is a prefix (#164).
    """

    def words(entry: str) -> str:
        return entry[: -len(READ_PREFIX_MARK)] if entry.endswith(READ_PREFIX_MARK) else entry

    out: dict[str, dict[str, list[str]]] = {}
    for agent in agents:
        for name, read_only, _role in _roles(agent):
            if not read_only:
                continue
            out[name] = {
                "gates": [words(g) for g in agent.gate_commands],
                "freeGates": list(free_gates(agent.gate_commands)),
                "reads": [words(e) for e in agent.get_only_commands],
                "readPrefixes": [words(e) for e in agent.get_only_commands if e.endswith(READ_PREFIX_MARK)],
            }
    return {"version": GUARD_SETS_VERSION, "agents": out}


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
