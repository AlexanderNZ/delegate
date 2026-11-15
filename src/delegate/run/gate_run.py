"""The run of the gates of a step, and the handling of their logs and of the tails of their output (#341).

`run_gate_with_output` runs one gate of a step through the gate port and records `gate-start`, `gate-result` or `gate-skip`.
`gate_outcomes` runs each gate of a stack. `GateRun` is the outcome of one gate. `green_result` finds the green result that
lets a gate skip its run on the same commit. `copy_gate_logs`, `gate_log_path` and `red_gates_text` hold the full log of a gate
and the end of its output for the brief of a fix-up. The module imports no engine: the engine, and the verification of a chain
(`delegate.run.chain`, through `ChainSteps`), call these functions with the step whose gates run (`GateStep`, #342).
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple, Protocol

from ..ports.vcs import VersionControl
from .brief import GateLogCopy, gate_log_copies_text
from .context import RunContext
from .events import PAUSED_BEFORE_GATE
from .journal import Journal
from .step_pause import pause_point
from .time_limits import minutes_text, seconds
from .vcs_access import head_commit, vcs_errors, worktree_environment
from .workflow import Stack, Ticket

# The journal keeps this many characters of the end of a gate's output.
GATE_OUTPUT_TAIL_CHARS: int = 4000

# The directory of the run that holds the log file of each gate run, in one directory for each ticket.
GATE_LOG_DIRECTORY: str = "gates"

# The reason of a gate that left a change to a tracked file in the worktree (#276).
GATE_CHANGED_TRACKED_FILES: str = "the gate changed tracked files"


class GateRun(NamedTuple):
    """The outcome of one gate for the engine: whether it is green, its output, and the name that a reason gives it.

    `label` is the name of the gate, or its command when it has no name, and a gate that the runner ended at its time limit has `(timed out)` after it.
    `log` is the log file of the gate run, with its full output, or None for a gate that did not run again (see `gate-skip`).
    `commit` is the commit that the result is for: the HEAD of the worktree before the gate.
    """

    command: str
    green: bool
    output: str
    label: str
    log: Path | None = None
    commit: str | None = None


class GateStep(Protocol):
    """The step whose gates run: the engine's `_Step`. The gate run reads these fields and no other.

    The gates of the step run in its worktree, and its journal events name the id of its ticket.
    """

    run: RunContext
    journal: Journal
    ticket: Ticket

    @property
    def stack(self) -> Stack: ...

    @property
    def worktree(self) -> Path: ...


def gate_outcomes(step: GateStep, round_number: int, phase: str, head: str) -> list[GateRun]:
    """Run each gate of the stack of the step as `run_gate_with_output` does, in order, and return their outcomes.

    `head` is the HEAD of the worktree, which the caller read after the last step that can move it. The first gate uses
    it. A gate that ran is a step that can move HEAD, so the gate after it reads HEAD again. A gate that the journal
    let skip ran nothing, so the gate after it uses the same commit (#239).
    """
    outcomes: list[GateRun] = []
    commit: str | None = head
    for gate in step.stack.gates:
        outcome = run_gate_with_output(step, gate, round_number, phase, commit=commit)
        outcomes.append(outcome)
        commit = outcome.commit if outcome.log is None else None
    return outcomes


def run_gate_with_output(step: GateStep, command: str, round_number: int, phase: str, *, commit: str | None = None) -> GateRun:
    """Run one gate command in the worktree of the step through the gate runner of the run, and record the result.

    Return its outcome: whether it is green, and the tail of its output.

    `round_number` is 0 for the first build and the fix-up round for a later run.
    `phase` is one of GATE_PHASE_BUILD, GATE_PHASE_REBASE and GATE_PHASE_FIXUP, or GATE_PHASE_CHAIN: the gates on the tip of an `economy` chain.

    The time limit of the gate is the `gate_limit` of the stack of the step, in minutes (#179): 120 minutes
    when the stack has no `gate-timeout` (#275). With None the runner waits for the gate with no limit. A gate that runs longer gets its process group ended by
    the runner. The result then has `timed_out`, and it is red. The engine writes `gate-result` with `green` false and
    `timed_out` true, and ends the output with a line that names the limit, so the specialist and a reader see the cause.
    The step goes on as for any red gate.

    A gate runs at most once green on one commit. When the journal holds a green
    `gate-result` of the same command on the commit that the worktree has
    checked out, the engine does not run the gate again: it writes `gate-skip`
    with that commit and the `seq` of the green result, and the gate counts as
    green. A rebase that keeps the commit, and a resume after a stop, so cost no
    second gate run. The output of a skipped gate is the output of that green result.

    The name of the gate is the name that the workflow gives to it in the stack (#223), or None. A gate with a name has `name` in its
    `gate-start`, `gate-result` and `gate-skip`, and in the `paused` event before it, and the reason of a red gate gives
    the name in place of the command. A gate with no name has no such field.

    Before a gate runs, the engine makes an empty log file for it and writes
    `gate-start` with the path of the file. The gate runner appends the output
    to the file while the gate runs, so `delegate status` and `delegate watch`
    see the activity of a long gate.

    `commit` is the HEAD of the worktree when the caller read it after the last step that can move it. With None the
    engine reads it here.

    After the gate, the engine checks the worktree (see `changed_by_gate`). A gate that changed a tracked file is red,
    whatever its exit status is: its `gate-result` has `clean` false and the paths in `changed`, its output ends with a
    line that names the paths, and its label gives the reason. Each other result has `clean` true (#276).
    """
    gates, vcs, journal, ticket, worktree = step.run.gates, step.run.vcs, step.journal, step.ticket.id, step.worktree
    gate_timeout, name = step.stack.gate_limit, step.stack.gate_names.get(command)
    if commit is None:
        commit = head_commit(vcs, worktree)
    named: dict[str, object] = {} if name is None else {"name": name}
    shown = name or command
    green = green_result(journal, command, commit)
    if green is not None:
        green_seq = int(green["seq"])  # type: ignore[call-overload]
        journal.append(
            "gate-skip", ticket=ticket, round=round_number, phase=phase, command=command, commit=commit, green_seq=green_seq,
            reason=f"the journal holds a green result of this gate on commit {commit} (seq {green_seq})", **named,
        )
        return GateRun(command, True, str(green.get("output_tail", "")), shown, commit=commit)
    pause_point(journal, ticket, PAUSED_BEFORE_GATE, f"before the gate `{shown}` of ticket {ticket}", command=command, phase=phase, **named)
    log = gate_log_path(journal, ticket, phase, round_number)
    journal.append("gate-start", ticket=ticket, round=round_number, phase=phase, command=command, gate_log=str(log), **named)
    result = gates.run(command, worktree, log=log, timeout=seconds(gate_timeout), environment=worktree_environment(vcs, worktree))
    output = result.output[-GATE_OUTPUT_TAIL_CHARS:]
    labels: list[str] = []
    if result.timed_out:
        output += f"\n[delegate: the gate ran past the gate-timeout of {minutes_text(gate_timeout)} minutes, and the engine ended it]\n"
        labels.append("timed out")
    changed = changed_by_gate(vcs, worktree)
    if changed:
        paths = ", ".join(f"`{path}`" for path in changed)
        output += f"\n[delegate: {GATE_CHANGED_TRACKED_FILES}: {paths}; the engine put them back to the commit]\n"
        labels.append(f"{GATE_CHANGED_TRACKED_FILES}: {paths}")
    green = result.green and not changed
    journal.append(
        "gate-result", ticket=ticket, round=round_number, phase=phase, command=command, commit=commit,
        exit_status=result.exit_status, green=green, timed_out=result.timed_out, output_tail=output, clean=not changed,
        **({"changed": changed} if changed else {}), **named,
    )
    return GateRun(command, green, output, f"{shown} ({'; '.join(labels)})" if labels else shown, log, commit)


def changed_by_gate(vcs: VersionControl, worktree: Path) -> list[str]:
    """Check the worktree after a gate, and give the tracked paths that the gate changed, in the order of the paths.

    A gate result is a fact about the commit that it names (#176), so a gate must leave the tracked files as the
    commit holds them (#276). The check is the check before the gates (see `_uncommitted_before_gates`), with no path
    that no commit holds: a file that the gate made and that git does not ignore stays, as a file of a red gate does.
    When a path changed, the engine puts the tracked files and the index back to the commit, so the next gate runs on
    the commit. The tree was clean before the gate: the engine checks it before the first gate of a phase, and after
    each gate, and a rebase needs a clean tree.
    """
    with vcs_errors():
        changed = [change.path for change in vcs.uncommitted_changes(worktree) if change.code != "??"]
        if changed:
            vcs.restore_tracked(worktree)
    return changed


def copy_gate_logs(red: Sequence[GateRun], scratch: Path) -> list[GateLogCopy]:
    """Copy the full log of each red gate into the scratch directory `scratch` of the next session, and give the copies (#246).

    The brief gives only the end of the output of a gate (`GATE_OUTPUT_TAIL_CHARS`), and a host can refuse a read of the
    gate log in the directory of the run. The copy has the name of the log with `gate-` before it, so a later copy does not
    replace it. The engine does not read the output of a test runner: the copy is the log as the gate wrote it.
    """
    copies: list[GateLogCopy] = []
    for outcome in red:
        if outcome.log is None:
            continue
        copy = scratch / f"gate-{outcome.log.name}"
        shutil.copyfile(outcome.log, copy)
        copies.append(GateLogCopy(outcome.command, copy, len(copy.read_bytes().splitlines())))
    return copies


def gate_log_path(journal: Journal, ticket: str, phase: str, round_number: int) -> Path:
    """Make a new, empty log file for one gate run, and return its path.

    The file is `gates/<ticket>/<phase>-<round>.log` in the run directory, then
    `<phase>-<round>-2.log`, and so on. It is never an earlier log.
    """
    directory = journal.path.parent / GATE_LOG_DIRECTORY / ticket
    directory.mkdir(parents=True, exist_ok=True)
    number = 1
    while True:
        path = directory / (f"{phase}-{round_number}.log" if number == 1 else f"{phase}-{round_number}-{number}.log")
        try:
            path.touch(exist_ok=False)
        except FileExistsError:
            number += 1
            continue
        return path


def green_result(journal: Journal, command: str, commit: str) -> dict[str, object] | None:
    """The first green `gate-result` of `command` on `commit` in the journal, or None when it holds none.

    Only a result with `clean: true` counts: the worktree had no change that was
    not committed when the gate ran (#176), and the gate changed no tracked file
    (#276). A result of an older journal has no such field, so it skips no gate.
    """
    for event in journal.events:
        if (
            event["event"] == "gate-result" and event["green"] and event.get("clean") is True
            and event["command"] == command and event.get("commit") == commit
        ):
            return event
    return None


def red_gates_text(
    commit: str, red: list[tuple[str, str]], copies: Sequence[GateLogCopy] = (), place: str = "the tip of the chain"
) -> str:
    """The findings of a fix-up round for red gates: each red gate on `commit`, its output, and the copy of each full log (#246).

    `place` names `commit` for the specialist: the tip of an `economy` chain, or the fix-up commit of `assure` mode (#277).
    """
    gates = "".join(f"- The gate `{gate}` is red.\n" for gate, _ in red)
    output = "\n\n".join(f"$ {gate}\n{text.rstrip(chr(10))}" for gate, text in red)
    text = f"The engine ran the gates on {place}, the commit `{commit}`.\n\n{gates}\nThe output of the red gates:\n\n```\n{output}\n```\n"
    return text + (f"\n{gate_log_copies_text(copies)}\n" if copies else "")
