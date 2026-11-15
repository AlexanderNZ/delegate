"""The verification of an `economy` chain is its own module of the run context (#340).

`delegate.run.chain` holds `ChainRun`, which runs the gates and the verifiers at the end of a chain, and `chain_redo`, which
finds the chain fix-up that the agent check stopped. The engine imports the module and holds none of them, and the module
imports no engine: the engine gives it the steps that it drives (`ChainSteps`). The behaviour of the chain has its own tests
(`test_economy.py`, `test_chain_fixup_retry.py`); these cases check the place of the code and the rule of `chain_redo`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import delegate
from delegate.run.chain import ChainRedo, chain_redo

RUN = Path(delegate.__file__).parent / "run"
MOVED = {"_ChainRedo", "_chain_redo", "_ChainRun", "_chain_text"}


def defined_in(path: Path) -> set[str]:
    return {node.name for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, (ast.ClassDef, ast.FunctionDef))}


def test_the_engine_holds_no_definition_of_the_verification_of_a_chain():
    assert defined_in(RUN / "engine.py") & MOVED == set()


def test_the_chain_module_imports_no_engine():
    imported = {
        alias.name for node in ast.walk(ast.parse((RUN / "chain.py").read_text())) if isinstance(node, ast.ImportFrom)
        for alias in node.names
    } | {
        node.module for node in ast.walk(ast.parse((RUN / "chain.py").read_text())) if isinstance(node, ast.ImportFrom) and node.module
    }

    assert "journal" in imported
    assert "engine" not in imported


def stopped_fixup(findings_file: Path) -> list[dict[str, object]]:
    """The events of a chain fix-up that the agent check stopped, then a retry at the chain fix-up."""
    return [
        {"event": "verify-start", "ticket": "b", "stack": "py", "commit": "c1"},
        {"event": "verdict", "ticket": "b", "stack": "py", "verdict": "REJECT", "findings": ["b: wrong"]},
        {
            "event": "agents-changed", "ticket": "b", "role": "fix-up", "round": 1, "rejected_commit": "c1",
            "findings_file": str(findings_file), "trigger": "reject",
        },
        {"event": "retry", "ticket": "b", "phase": "chain-fixup"},
    ]


def test_a_journal_with_no_stopped_chain_fixup_gives_no_redo():
    events: list[dict[str, object]] = [{"event": "verify-start", "ticket": "b", "stack": "py", "commit": "c1"}]

    assert chain_redo(events, "b", "py") is None


def test_the_redo_of_a_stopped_fixup_after_a_reject_holds_the_round_the_commit_and_the_rejected_state(tmp_path):
    findings_file = tmp_path / "findings.md"
    findings_file.write_text("fix b\n")

    redo = chain_redo(stopped_fixup(findings_file), "b", "py")

    assert redo == ChainRedo(
        round=1, commit="c1", findings_text="fix b\n", red_gates=False, rejected="c1", findings=["b: wrong"],
    )


def test_a_stopped_fixup_with_no_retry_after_it_gives_no_redo(tmp_path):
    findings_file = tmp_path / "findings.md"
    findings_file.write_text("fix b\n")

    assert chain_redo(stopped_fixup(findings_file)[:-1], "b", "py") is None
