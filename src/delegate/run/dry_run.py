"""The dry run of a resume: what the resume does with the plan of a run, with no write (#338).

`delegate run --resume <run-id> --dry-run` prints the result of `review_plan_change`. The function makes the review of the plan
(see `plan_review`), and then each other check of the resume that needs no write, so a refusal has the message of the resume.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from ..ports.vcs import VersionControl
from .engine import plan_review_with_outlook, resume_checks, resume_events
from .errors import EngineError
from .plan_review import PlanReview
from .state_location import StateLocation, locate_state
from .workflow import Workflow, plan_order


def review_plan_change(
    workflow: Workflow, workflow_path: Path, repo: Path, run_id: str, vcs: VersionControl, reviewed: tuple[str, ...] = (),
    retry: str | None = None, *, location: StateLocation | None = None, coordinator_commit: str | None = None,
    config_paths: Sequence[str] = (), stack_changed: bool = False,
) -> PlanReview:
    """Apply the rules of a plan change to the workflow and the run `run_id`, as a resume does, and change nothing.

    `delegate run --resume <run-id> --dry-run` prints the result. `retry` is the
    ticket of `--retry`: the text of a failed ticket that it starts again can
    change (rule 10). Raise
    EngineError when the run is unknown, or when the workflow differs from the
    run in a field other than the tickets. `location` is the location that the command looked up (see `locate_state`),
    or None to look it up here.

    The result also holds what the resume does with each ticket that the run skipped (see `engine._skipped_outlook`).
    `coordinator_commit` is the commit of `--coordinator-commit`, or None. With it, the review makes the checks of
    the commit that the resume makes (`retry.check_coordinator_commit`), and a refusal has the text of the refusal of the resume
    (#283). `config_paths` are the project configuration paths of the adapter, which are hotspots too. `stack_changed` is
    `--stack-changed` (#290): a changed stack does not refuse the review, and the result holds each change.

    When the rules of the plan and of the retry accept, the review also makes each other check of the resume that needs no
    write (`resume_checks`), and `resume_refusal` of the result holds the message of the resume when one refuses (#357).
    """
    location = location or locate_state(repo, vcs)
    events = resume_events(location.state_directory(run_id), run_id)
    review = plan_review_with_outlook(
        workflow, workflow_path, location.top, run_id, vcs, events, reviewed, retry, coordinator_commit, config_paths, stack_changed
    )
    if review.problems() or review.retry_refusal is not None:
        return review
    try:
        resume_checks(
            workflow, workflow_path, plan_order(workflow), vcs, location.top, run_id, events, reviewed, retry, coordinator_commit,
            None, config_paths, stack_changed,
        )
    except EngineError as error:
        return replace(review, skipped=(), retry_outlook=None, resume_refusal=str(error))
    return review
