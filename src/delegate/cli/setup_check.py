"""The setup check of `delegate run --dry-run` (#182, #216).

A run fails for two reasons of setup that show late: an agent file that is missing, which fails the first agent of its
stack, and a workflow gate that the verifier guard does not permit, which shows when the verifier tries to run it. The
gates of a stack come from two places that nothing else compares: the workflow (`stacks.gates`, which the engine runs)
and the declaration (`gateCommands`, which became the allow list of the verifier guard, ADR 0004).

The check knows no harness. For each stack, it asks the adapter of the workflow to seal the specialist and the verifier
into a temporary directory (`Adapter.seal`): the adapter looks in the directories of its harness, the project directory
first, then the user directory. Then it asks the adapter whether the guard of the sealed verifier permits each gate of
the stack (`Adapter.permits`). The temporary directory goes away when the check ends.

After that, for each stack with no problem, the check runs the guard probe of the sealed verifier (#219, `run/probe.py`)
when the adapter has `probes_guard`: a short session on the tier `cheap` that must show a denial of the command
`touch delegate-guard-probe`. A result other than `denied` is a problem of the stack. One verifier that two stacks name
is probed once.

An adapter with no check (for example a scripted adapter of a test) gets one line that says the check was skipped.
This module is in `cli/`, because it reads the run context (the workflow).
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ..ports.harness import Adapter, AdapterError, SealedAgent
from ..ports.vcs import VcsError, VersionControl
from ..run.agents import unpermitted_gates
from ..run.probe import DENIED, PROBE_TIER, probe_guard, refusal
from ..run.workflow import Stack, Workflow
from ..shared.tiers import Tiers


@dataclass
class SetupCheck:
    """The result: lines for standard output, and one message for each problem."""

    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def check_setup(workflow: Workflow, adapter: Adapter, repo: Path, tiers: Tiers, vcs: VersionControl) -> SetupCheck:
    """Check the agent files, the verifier gates and the guard of each stack of `workflow`, through `adapter`.

    `repo` is the project directory. `tiers` gives the model of the guard probe, and `vcs` makes its repository. The check
    changes nothing outside temporary directories that it removes.
    """
    result = SetupCheck()
    if not getattr(adapter, "checks_setup", False):
        result.lines.append(f"setup check skipped: the adapter {workflow.adapter!r} has no check of its agent files and its verifier guard")
        return result
    with tempfile.TemporaryDirectory(prefix="delegate-setup-check-") as scratch:
        probed: dict[str, str] = {}  # the result of the probe of each verifier, so that a verifier of two stacks runs one probe
        for stack in workflow.stacks.values():
            problems, sealed = _stack_problems(workflow.adapter, adapter, stack, repo, Path(scratch) / stack.name)
            if problems:
                result.problems.extend(problems)
                continue
            result.lines.append(f"stack {stack.name}: checked")
            if sealed is None or not getattr(adapter, "probes_guard", False):
                continue
            verdict = probed.get(stack.verifier)
            if verdict is None:
                verdict = _probe(adapter, tiers, vcs, sealed, Path(scratch) / f"{stack.name}-probe.stream.jsonl", stack, result)
                probed[stack.verifier] = verdict
            if verdict == DENIED:
                result.lines.append(f"stack {stack.name}: guard probe denied")
            elif verdict != "":
                result.problems.append(f"stack {stack.name}: {refusal(stack.verifier, verdict)}")
    return result


def _probe(adapter: Adapter, tiers: Tiers, vcs: VersionControl, sealed: SealedAgent, stream: Path, stack: Stack, result: SetupCheck) -> str:
    """The result of the guard probe of the sealed verifier, or an empty text after a problem that the function added to `result`."""
    try:
        model = tiers.model_for(PROBE_TIER, adapter.tier_column)
        return probe_guard(adapter, vcs, sealed, model, stream).result
    except (KeyError, ValueError):
        result.problems.append(f"stack {stack.name}: the tier table has no model of the tier {PROBE_TIER!r} for the guard probe")
    except (AdapterError, VcsError) as error:
        result.problems.append(f"stack {stack.name}: the guard probe of the verifier {stack.verifier!r} could not run: {error}")
    return ""


def _stack_problems(name: str, adapter: Adapter, stack: Stack, repo: Path, scratch: Path) -> tuple[list[str], SealedAgent | None]:
    """The problems of one stack, and its sealed verifier (None when the stack has no sealed verifier)."""
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
        problems.extend(unpermitted_gates(adapter, stack, sealed[stack.verifier]))
    return problems, sealed.get(stack.verifier)
