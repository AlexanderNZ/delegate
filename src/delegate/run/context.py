"""The values that do not change during a run, and what a resume asks for (#342).

`RunContext` holds the workflow and its path, the adapter, the version control, the gate runner, the tiers, the model of
each role, the repository, the state directory and the run id. `run_workflow` makes it once, and the functions of the
engine and of the modules moved out of it (`chain`, `gate_run`) take it with their own values. The journal is not in it:
the engine opens the journal after the seal of the agents, and a step gets it with the context.

`ResumeRequest` holds what the command asks of a resume: the run, the later tickets that the coordinator reviewed, the
ticket of `--retry`, the commit of `--coordinator-commit` and `--stack-changed`. The checks of a resume and the dry run
(`dry_run.review_plan_change`) take it.

The module imports no engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..ports.gates import GateRunner
from ..ports.harness import Adapter
from ..ports.vcs import VersionControl
from ..shared.tiers import Tiers
from .workflow import Workflow


def project_config_paths(adapter: object) -> tuple[str, ...]:
    """The project configuration paths that the harness of `adapter` loads from its working directory (#273).

    An adapter with no such attribute loads none. The paths are hotspots of each stack of the run (see `Stack.all_hotspots`).
    """
    return tuple(getattr(adapter, "project_config_paths", ()))


@dataclass(frozen=True)
class RunContext:
    """The values of one run that do not change while it runs.

    `roles` holds the tier and the model of each role, and of each specialist tier of the run (see `engine._roles`).
    `repo` is the top level of the main checkout, and `state_dir` the state directory of the run.
    """

    workflow: Workflow
    workflow_path: Path
    adapter: Adapter
    vcs: VersionControl
    gates: GateRunner
    tiers: Tiers
    roles: dict[str, tuple[str, str]]
    repo: Path
    state_dir: Path
    run_id: str

    @property
    def run_dir(self) -> Path:
        """The directory of the run: its journal, reports, findings, gate logs and scratch directories."""
        return self.state_dir / "runs" / self.run_id

    def worktree(self, ticket_id: str) -> Path:
        """The worktree of the ticket `ticket_id`."""
        return self.state_dir / "worktrees" / self.run_id / ticket_id

    def hooks_dir(self, ticket_id: str) -> Path:
        """The directory of the push-guard hooks of the worktree of the ticket `ticket_id`."""
        return self.state_dir / "hooks" / self.run_id / ticket_id

    @property
    def config_paths(self) -> tuple[str, ...]:
        """The project configuration paths of the adapter (see `project_config_paths`)."""
        return project_config_paths(self.adapter)


@dataclass(frozen=True)
class ResumeRequest:
    """What the command asks of a resume of the run `run_id`.

    `reviewed` holds the later tickets that the coordinator reviewed, or `all`. `retry` is the ticket of `--retry`, and
    `coordinator_commit` the commit of `--coordinator-commit`, or None. `stack_changed` is `--stack-changed` (#290).
    """

    run_id: str
    reviewed: tuple[str, ...] = ()
    retry: str | None = None
    coordinator_commit: str | None = None
    stack_changed: bool = False
