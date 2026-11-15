"""The review of a plan change has its own modules of the run context (#338).

`delegate.run.plan_review` holds the values of the review and the rules that read a journal. `delegate.run.dry_run` holds
`review_plan_change`, which the dry run of a resume prints. The engine imports the first module and holds neither. The cases
read the source of the engine, and call `review_plan_change` against a real temporary git repository.
"""

from __future__ import annotations

import ast
from pathlib import Path

import delegate
from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.run import step_scans
from delegate.run.dry_run import review_plan_change
from delegate.run.plan_review import PlanReview, SkippedTicket, StackChange
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from ..support import kill_a_run, make_repo
from ..test_run_multi import HEAD, ticket

ENGINE = Path(delegate.__file__).parent / "run" / "engine.py"
MOVED = {"PlanReview", "SkippedTicket", "StackChange", "review_plan_change"}


def test_the_engine_holds_no_definition_of_the_review_of_a_plan_change():
    defined = {node.name for node in ast.parse(ENGINE.read_text()).body if isinstance(node, (ast.ClassDef, ast.FunctionDef))}

    assert defined & MOVED == set()


def test_the_review_of_a_plan_change_names_the_added_ticket(tmp_path, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    run_id, _ = kill_a_run(repo, 3)  # killed as the specialist of ticket b starts, after ticket a landed
    (repo / "workflow.toml").write_text(HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)))
    workflow_path = repo / "workflow.toml"
    workflow = load_workflow(workflow_path, load_tiers(), adapters.registered_names())

    review = review_plan_change(workflow, workflow_path, repo, run_id, GitVersionControl(), reviewed=("b",))

    assert isinstance(review, PlanReview)
    assert (review.old_order, review.new_order) == (["a", "b"], ["a", "b", "c"])
    assert review.changed
    assert review.check is not None and review.check.added == ["c"]
    assert review.stack_changes == () and review.skipped == ()
    assert [(rule, verdict) for rule, verdict, _ in review.verdicts()][:2] == [(1, "ok"), (2, "ok")]


def test_the_values_of_the_review_are_frozen_records():
    change = StackChange("go", {"gates": (["a"], ["b"])})
    skipped = SkippedTicket("b", "runs again")

    assert change.event_fields() == {"stack": "go", "changes": {"gates": {"old": ["a"], "new": ["b"]}}}
    assert (skipped.ticket, skipped.text) == ("b", "runs again")


def test_the_verification_of_a_chain_failed_a_step_that_has_a_built_step_end_before_its_newest_one():
    built = {"event": "step-end", "state": "built"}
    failed = {"event": "step-end", "state": "failed"}

    assert step_scans.chain_failed([built, failed]) is True
    assert step_scans.chain_failed([failed]) is False
