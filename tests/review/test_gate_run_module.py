"""The run of the gates of a step is its own module of the run context (#341).

`delegate.run.gate_run` holds `GateRun`, `gate_outcomes`, `run_gate_with_output`, `green_result`, and the handling of the logs and
of the output tails of the gates. The engine imports the module and holds none of them, and the module imports no engine. The
behaviour of the gates has its own tests (`test_gate_*.py`); these cases check the place of the code and three rules of its
helpers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import delegate
from delegate.run.gate_run import GateRun, copy_gate_logs, gate_log_path, green_result, red_gates_text
from delegate.run.journal import Journal

RUN = Path(delegate.__file__).parent / "run"
MOVED = {
    "_GateRun", "_gate_outcomes", "_run_gate_with_output", "_changed_by_gate", "_copy_gate_logs", "_gate_log_path", "_green_result",
    "_red_gates_text",
}


def defined_in(path: Path) -> set[str]:
    return {node.name for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, (ast.ClassDef, ast.FunctionDef))}


def modules_imported_by(path: Path) -> set[str]:
    nodes = [node for node in ast.walk(ast.parse(path.read_text())) if isinstance(node, ast.ImportFrom)]
    return {alias.name for node in nodes for alias in node.names} | {node.module for node in nodes if node.module}


def test_the_engine_holds_no_definition_of_the_run_of_the_gates():
    assert defined_in(RUN / "engine.py") & MOVED == set()


def test_the_gate_run_module_imports_no_engine():
    imported = modules_imported_by(RUN / "gate_run.py")

    assert "journal" in imported
    assert "engine" not in imported


def test_a_gate_log_gets_the_next_free_number_and_never_replaces_an_earlier_log(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")

    first = gate_log_path(journal, "a", "build", 0)
    second = gate_log_path(journal, "a", "build", 0)

    assert (first.name, second.name) == ("build-0.log", "build-0-2.log")
    assert first.parent == tmp_path / "gates" / "a"


def test_only_a_clean_green_result_of_the_same_command_on_the_same_commit_skips_a_gate(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    base = {"ticket": "a", "round": 0, "phase": "build", "command": "make"}
    journal.append("gate-result", green=True, commit="c1", clean=False, **base)
    journal.append("gate-result", green=True, commit="c2", clean=True, **base)
    journal.append("gate-result", green=False, commit="c1", clean=True, **base)

    assert green_result(journal, "make", "c1") is None
    found = green_result(journal, "make", "c2")
    assert found is not None and found["commit"] == "c2"


def test_a_red_gate_text_names_the_commit_the_output_and_the_copy_of_the_log(tmp_path):
    log = tmp_path / "gates" / "build-0.log"
    log.parent.mkdir()
    log.write_text("one\ntwo\n")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    copies = copy_gate_logs([GateRun("make test", False, "boom\n", "make test", log)], scratch)

    text = red_gates_text("abc123", [("make test", "boom\n")], copies)

    assert "the commit `abc123`" in text
    assert "- The gate `make test` is red." in text
    assert "$ make test\nboom\n" in text
    assert f"`{scratch / 'gate-build-0.log'}` (2 lines)" in text
