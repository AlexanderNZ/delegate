"""The docstrings of the engine say what the code does (#365).

A case reads the source of a module of the engine with `ast` and checks its docstrings: the module docstring of the engine,
the docstring of the loop of a run, and the docstring of `ChainRun`. No case imports a private name. The expected words come
from the ticket and from ADR 0009, not from the code.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import delegate.run.chain as chain
import delegate.run.engine as engine
import delegate.run.run_state as run_state


def source_tree(module: object) -> ast.Module:
    """The syntax tree of the source file of a module of the engine."""
    return ast.parse(Path(module.__file__).read_text())  # type: ignore[attr-defined]


def flat(text: str | None) -> str:
    """The text with each run of white space as one space, so a line break does not split a phrase."""
    return re.sub(r"\s+", " ", text or "")


def docstring_of(module: object, name: str) -> str:
    """The docstring of the function or class `name` at the top level of the module, flat."""
    for node in source_tree(module).body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return flat(ast.get_docstring(node))
    raise AssertionError(f"{name} is not in the module")


def test_the_module_docstring_says_why_the_step_fails_after_the_last_round() -> None:
    """The sentence about the limit says the cause once, and does not say that the step fails twice."""
    text = flat(engine.__doc__)
    assert "the step fails and the step fails" not in text
    assert "After `FIXUP_ROUND_LIMIT` rounds a REJECT or red gates fail the step" in text


def test_the_loop_of_a_run_has_a_docstring_that_names_how_the_run_stops() -> None:
    """`_execute` has a docstring: the tickets in order, the chain verification, and each way that the process ends."""
    text = docstring_of(engine, "_execute")
    for word in ("plan order", "run-end", "paused", "halted", "crashed"):
        assert word in text, word


def test_the_chain_run_docstring_names_the_rule_of_the_pass_of_the_run_state() -> None:
    """A `retry` at `chain-fixup` does not start a pass (#320), and the docstring names the rule of the run state."""
    text = docstring_of(chain, "ChainRun")
    assert "after the last `retry` or `plan-change`" not in text
    assert "run_state.starts_chain_verification" in text
    assert "chain-fixup" in text


def test_the_rule_that_the_chain_docstring_names_exists_in_the_run_state() -> None:
    """The name that the docstring uses is a public name of the run state, so a rename cannot leave the docstring false."""
    assert callable(run_state.starts_chain_verification)
    assert run_state.starts_chain_verification({"event": "retry", "phase": "chain-fixup"}) is False
    assert run_state.starts_chain_verification({"event": "retry", "phase": "gates"}) is True
