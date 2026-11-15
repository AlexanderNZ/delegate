"""One run context replaces the long parameter lists of the engine (#342).

`delegate.run.context` holds `RunContext`, the frozen value of the things that do not change during a run, and
`ResumeRequest`, what a resume asks for. `run_workflow` keeps its public parameters and makes the context. The functions
of the engine and of the modules moved out of it take the context and their own values. The behaviour of the run has its
own tests (`test_run_*.py`, `test_economy.py`); these cases check the length of each parameter list and the frozen value.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
from pathlib import Path

import pytest

import delegate
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.run.context import ResumeRequest, RunContext
from delegate.run.engine import run_workflow
from delegate.run.workflow import Workflow
from delegate.shared.tiers import load_tiers

RUN = Path(delegate.__file__).parent / "run"
# The engine and the modules that #338 to #341 moved out of it.
MODULES = (
    "engine", "plan_review", "dry_run", "retry", "chain", "gate_run", "step_pause", "time_limits", "vcs_access", "context",
)
LIMIT = 8


def parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    arguments = node.args
    names = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    count = len(names) + (arguments.vararg is not None) + (arguments.kwarg is not None)
    return count - 1 if names and names[0].arg in ("self", "cls") else count


def long_parameter_lists() -> list[str]:
    found = []
    for module in MODULES:
        for node in ast.walk(ast.parse((RUN / f"{module}.py").read_text())):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and parameter_count(node) > LIMIT:
                found.append(f"{module}.{node.name}: {parameter_count(node)}")
    return found


def test_no_function_of_the_engine_or_of_its_moved_modules_takes_more_than_eight_parameters_but_run_workflow():
    assert long_parameter_lists() == ["engine.run_workflow: 18"]


def test_run_workflow_keeps_its_public_parameters():
    assert list(inspect.signature(run_workflow).parameters) == [
        "workflow", "workflow_path", "repo", "tiers", "adapter", "vcs", "gates", "resume", "retry", "break_lock", "on_start",
        "run_id", "reviewed", "agents_changed", "coordinator_commit", "location", "stack_changed", "observers",
    ]


def test_the_run_context_is_frozen_and_names_the_directories_of_its_run(tmp_path):
    workflow = Workflow("main", "run/x", "assure", "claude-code", {}, [], {})
    context = RunContext(
        workflow, tmp_path / "workflow.toml", object(), GitVersionControl(), ShellGateRunner(), load_tiers(), {}, tmp_path,
        tmp_path / "state", "20261115T000000Z-abcd",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        context.run_id = "other"  # type: ignore[misc]
    assert context.run_dir == tmp_path / "state" / "runs" / "20261115T000000Z-abcd"
    assert context.worktree("a") == tmp_path / "state" / "worktrees" / "20261115T000000Z-abcd" / "a"
    assert context.config_paths == ()


def test_a_resume_request_asks_for_no_retry_and_no_review_by_default():
    request = ResumeRequest("20261115T000000Z-abcd")

    assert (request.reviewed, request.retry, request.coordinator_commit, request.stack_changed) == ((), None, None, False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        request.retry = "a"  # type: ignore[misc]
