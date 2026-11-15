"""The plan of a `--retry` is its own module of the run context (#339).

`delegate.run.retry` holds the rules that read a journal and say where a failed step goes on: the plan, the check of a
commit of the coordinator, and the hints that `delegate run` prints. The engine imports the module and holds none of them.
The cases read the source of the engine, and call the module against a real temporary git repository.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import delegate
from delegate import adapters
from delegate.run.progress import restore_progress
from delegate.run.errors import EngineError
from delegate.run.retry import RetryHint, retry_hints, retry_plan
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from ..support import HEAD, journal_path, make_repo, read_journal_file, run_main, state_dir, ticket

ENGINE = Path(delegate.__file__).parent / "run" / "engine.py"
MOVED = {
    "_RetryPlan", "_CoordinatorCommit", "_retry_plan", "_economy_retry_plan", "_chain_retry_plan", "_chain_fixup_plan",
    "_coordinator_commit", "_coordinator_commits", "_check_chain_hotspots", "_paths_without", "_retry_outcome", "RetryHint",
    "retry_hints",
}


def test_the_engine_holds_no_definition_of_the_plan_of_a_retry():
    defined = {node.name for node in ast.walk(ast.parse(ENGINE.read_text())) if isinstance(node, (ast.ClassDef, ast.FunctionDef))}

    assert defined & MOVED == set()


def failed_run(tmp_path, capsys):
    """A run whose only ticket failed at red gates. Return the repository, the run id and the workflow."""
    flag = tmp_path / "green"
    text = HEAD.replace('gates = ["ls *.txt"]', f'gates = ["test -f {flag}"]') + ticket("a")
    repo = make_repo(tmp_path, text)
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    workflow = load_workflow(repo / "workflow.toml", load_tiers(), adapters.registered_names())
    return repo, run_dir.name, workflow


def test_the_hints_of_a_run_with_one_failed_step_name_that_step(tmp_path, capsys, scripted):
    repo, run_id, workflow = failed_run(tmp_path, capsys)

    assert retry_hints(workflow, journal_path(repo, run_id), run_id) == [RetryHint("a")]


def test_the_plan_of_a_failed_step_with_red_gates_is_the_gates_and_a_ticket_that_the_run_does_not_hold_is_refused(tmp_path, capsys, scripted):
    repo, run_id, workflow = failed_run(tmp_path, capsys)
    progress = restore_progress(read_journal_file(repo, run_id), run_id, retrying=True)

    plan = retry_plan(workflow, progress, run_id, "a")

    assert (plan.ticket, plan.phase, plan.round, plan.restart, plan.revive) == ("a", "gates", 0, False, ())
    with pytest.raises(EngineError) as refused:
        retry_plan(workflow, progress, run_id, "zz")
    assert str(refused.value) == f"ticket 'zz' of run {run_id!r} cannot be retried: it is not a ticket of the run"
