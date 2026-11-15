"""The shared helpers of the engine tests are in `tests/support.py`, not in a test file (#353).

Each case reads the source of the test files with `ast`. The expected values come from the ticket: no test file imports
from `tests/test_run_multi.py`, and the helper `frozen` has one definition.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent


def sources_of_tests() -> list[Path]:
    return sorted(TESTS.rglob("test_*.py"))


def test_no_test_file_imports_from_the_test_run_multi_module():
    importers = []
    for path in sources_of_tests():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "test_run_multi":
                importers.append(f"{path.relative_to(TESTS)}:{node.lineno}")
    assert importers == []


def test_the_helper_frozen_has_one_definition_in_the_support_module():
    definers = [
        str(path.relative_to(TESTS))
        for path in [TESTS / "support.py", *sources_of_tests()]
        if any(isinstance(node, ast.FunctionDef) and node.name == "frozen" for node in ast.walk(ast.parse(path.read_text())))
    ]
    assert definers == ["support.py"]
