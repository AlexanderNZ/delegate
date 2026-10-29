"""The setup check of `delegate run --dry-run` (#182).

A run fails for two reasons of setup that show late: an agent file that is missing, which fails the first agent of its
stack, and a workflow gate that the verifier guard does not permit, which shows when the verifier tries to run it. The
gates of a stack come from two places that nothing else compares: the workflow (`stacks.gates`, which the engine runs)
and the declaration (`gateCommands`, which became the allow list of the verifier guard, ADR 0004).

For each stack, the check looks for the specialist file and the verifier file in the directories where the harness of the
adapter looks: the project directory first, then the user directory. Then it asks the rendered verifier whether it permits
each gate of the stack:

- Claude Code: the check runs the Bash hook of the verifier with the gate command on standard input, as the harness does.
  Exit status 0 permits the command, and any other status denies it (the guard blocks with status 2).
- OpenCode: the check resolves the gate against the `permission.bash` map with the matcher of the validator.

An adapter with no check (for example a scripted adapter of a test) gets one line that says the check was skipped.
This module is in `cli/`, because it reads the run context (the workflow) and the definitions context (the validator).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ..definitions.validate import opencode_bash_action, split_frontmatter
from ..run.workflow import Stack, Workflow

# The directories where each harness looks for an agent file, relative to the project and to the user directory.
# The harness reads the project directory before the user directory.
AGENT_DIRECTORIES: dict[str, tuple[Path, Path]] = {
    "claude-code": (Path(".claude") / "agents", Path(".claude") / "agents"),
    "opencode": (Path(".opencode") / "agents", Path(".config") / "opencode" / "agents"),
}

# The time that one run of a hook can take.
HOOK_TIMEOUT: float = 30.0


@dataclass
class SetupCheck:
    """The result: lines for standard output, and one message for each problem."""

    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def check_setup(workflow: Workflow, repo: Path, home: Path) -> SetupCheck:
    """Check the agent files and the verifier gates of each stack of `workflow`.

    `repo` is the project directory and `home` the user directory. The check changes nothing.
    """
    result = SetupCheck()
    directories = AGENT_DIRECTORIES.get(workflow.adapter)
    if directories is None:
        result.lines.append(
            f"setup check skipped: the adapter {workflow.adapter!r} has no check of its agent files and its verifier guard; "
            f"the check knows: {', '.join(AGENT_DIRECTORIES)}"
        )
        return result
    searched = (repo / directories[0], home / directories[1])
    for stack in workflow.stacks.values():
        problems = _stack_problems(workflow.adapter, stack, searched)
        if problems:
            result.problems.extend(problems)
        else:
            result.lines.append(f"stack {stack.name}: checked")
    return result


def _find(name: str, searched: tuple[Path, ...]) -> Path | None:
    for directory in searched:
        if (directory / f"{name}.md").is_file():
            return directory / f"{name}.md"
    return None


def _stack_problems(adapter: str, stack: Stack, searched: tuple[Path, ...]) -> list[str]:
    problems: list[str] = []
    found: dict[str, Path] = {}
    for agent in (stack.specialist, stack.verifier):
        path = _find(agent, searched)
        if path is None:
            looked = ", ".join(str(directory / f"{agent}.md") for directory in searched)
            problems.append(f"stack {stack.name}: the agent {agent!r} has no agent file for {adapter}; looked in {looked}")
        else:
            found[agent] = path
    if stack.verifier in found:
        problems.extend(_gate_problems(adapter, stack, found[stack.verifier]))
    return problems


def _gate_problems(adapter: str, stack: Stack, verifier: Path) -> list[str]:
    try:
        frontmatter, _, error = split_frontmatter(verifier.read_text())
    except (OSError, UnicodeDecodeError) as failure:
        return [f"stack {stack.name}: the verifier file {verifier} cannot be read: {failure}"]
    if error is not None or frontmatter is None:
        return [f"stack {stack.name}: the verifier file {verifier} has no usable frontmatter: {error}"]
    problems: list[str] = []
    for gate in stack.gates:
        reason = _denial(adapter, frontmatter, gate)
        if reason is not None:
            problems.append(f"stack {stack.name}: the verifier {stack.verifier!r} ({verifier}) does not permit the gate {gate!r}{reason}")
    return problems


def _denial(adapter: str, frontmatter: dict, gate: str) -> str | None:
    """Why the verifier denies `gate`, as text that starts with ': ' or is empty, or None when it permits the gate."""
    if adapter == "opencode":
        permission = frontmatter.get("permission")
        action = opencode_bash_action(permission, gate) if isinstance(permission, dict) else "allow"
        return None if action == "allow" else f": its permission.bash map resolves the gate to {action or 'no rule'}"
    return _hook_denial(frontmatter, gate)


def _bash_hooks(frontmatter: dict) -> list[str]:
    hooks = frontmatter.get("hooks")
    commands: list[str] = []
    if not isinstance(hooks, dict):
        return commands
    for entry in hooks.get("PreToolUse") or []:
        if not isinstance(entry, dict) or entry.get("matcher") != "Bash":
            continue
        commands.extend(str(h["command"]) for h in entry.get("hooks") or [] if isinstance(h, dict) and "command" in h)
    return commands


def _hook_denial(frontmatter: dict, gate: str) -> str | None:
    """Run each Bash hook of the verifier with `gate`, as the harness does. A hook that does not exit 0 denies the gate."""
    tools = frontmatter.get("tools")
    listed = [t.strip() for t in tools.split(",")] if isinstance(tools, str) else list(tools or [])
    if tools is not None and "Bash" not in listed:
        return ": the verifier does not list the Bash tool"
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": gate}})
    shell = shutil.which("bash") or shutil.which("sh") or "sh"
    for command in _bash_hooks(frontmatter):
        try:
            process = subprocess.run([shell, "-c", command], input=payload, capture_output=True, text=True, timeout=HOOK_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as failure:
            return f": its Bash hook could not run: {failure}"
        if process.returncode != 0:
            message = " ".join(process.stderr.split())
            return f": its Bash hook exits {process.returncode}" + (f" and says: {message}" if message else "")
    return None
