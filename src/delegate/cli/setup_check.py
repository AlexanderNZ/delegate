"""The setup check of `delegate run --dry-run` (#182, #216).

A run fails for two reasons of setup that show late: an agent file that is missing, which fails the first agent of its
stack, and a workflow gate that the verifier guard does not permit, which shows when the verifier tries to run it. The
gates of a stack come from two places that nothing else compares: the workflow (`stacks.gates`, which the engine runs)
and the declaration (`gateCommands`, which became the allow list of the verifier guard, ADR 0004).

The check knows no harness. For each stack, it asks the adapter of the workflow to seal the specialist and the verifier
into a temporary directory (`Adapter.seal`): the adapter looks in the directories of its harness, the project directory
first, then the user directory. Then it asks the adapter whether the guard of the sealed verifier permits each gate of
the stack (`Adapter.permits`). The temporary directory goes away when the check ends.

An adapter with no check (for example a scripted adapter of a test) gets one line that says the check was skipped.
This module is in `cli/`, because it reads the run context (the workflow).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..ports.harness import Adapter, AdapterError, SealedAgent
from ..run.workflow import Stack, Workflow


@dataclass
class SetupCheck:
    """The result: lines for standard output, and one message for each problem."""

    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def check_setup(workflow: Workflow, adapter: Adapter, repo: Path) -> SetupCheck:
    """Check the agent files and the verifier gates of each stack of `workflow`, through `adapter`.

    `repo` is the project directory. The check changes nothing outside a temporary directory that it removes.
    """
    result = SetupCheck()
    if not getattr(adapter, "checks_setup", False):
        result.lines.append(f"setup check skipped: the adapter {workflow.adapter!r} has no check of its agent files and its verifier guard")
        return result
    with tempfile.TemporaryDirectory(prefix="delegate-setup-check-") as scratch:
        for stack in workflow.stacks.values():
            problems = _stack_problems(workflow.adapter, adapter, stack, repo, Path(scratch) / stack.name)
            if problems:
                result.problems.extend(problems)
            else:
                result.lines.append(f"stack {stack.name}: checked")
    return result


def _stack_problems(name: str, adapter: Adapter, stack: Stack, repo: Path, scratch: Path) -> list[str]:
    problems: list[str] = []
    sealed: dict[str, SealedAgent] = {}
    for agent in (stack.specialist, stack.verifier):
        try:
            found = adapter.seal(agent, repo, scratch / agent)
        except AdapterError as error:
            problems.append(f"stack {stack.name}: the agent {agent!r} cannot be sealed for {name}: {error}")
            continue
        if found is None:
            looked = ", ".join(str(path) for path in adapter.agent_paths(agent, repo))
            problems.append(f"stack {stack.name}: the agent {agent!r} has no agent file for {name}; looked in {looked}")
        else:
            sealed[agent] = found
    if stack.verifier in sealed:
        source = sealed[stack.verifier].source
        for gate in stack.gates:
            reason = adapter.permits(sealed[stack.verifier], gate)
            if reason is not None:
                problems.append(f"stack {stack.name}: the verifier {stack.verifier!r} ({source}) does not permit the gate {gate!r}: {reason}")
    return problems
