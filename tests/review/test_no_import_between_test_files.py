"""No test file imports from another test file, and the fixture `scripted` has one definition (#354).

A helper that two test files share is in a support module (`tests/support.py` and the modules `tests/support_*.py`), and
a fixture that two test files share is in `tests/conftest.py`. A test file that imports from another test file couples
the two: a change of one breaks the other, and pytest collects the imported fixtures twice.

Each case reads the source of the test files with `ast`. The test files are each `test_*.py` file under `tests/`.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent


def sources_of_tests() -> list[Path]:
    return sorted(TESTS.rglob("test_*.py"))


def _is_test_module(name: str) -> bool:
    return name.split(".")[-1].startswith("test_")


def _imports_a_test_module(node: ast.AST) -> bool:
    """`from .test_x import y`, `from tests.test_x import y`, `from . import test_x` and `import tests.test_x`."""
    if isinstance(node, ast.ImportFrom):
        return _is_test_module(node.module or "") or any(_is_test_module(alias.name) for alias in node.names)
    if isinstance(node, ast.Import):
        return any(_is_test_module(alias.name) for alias in node.names)
    return False


def test_no_test_file_imports_from_another_test_file():
    importers = []
    for path in sources_of_tests():
        for node in ast.walk(ast.parse(path.read_text())):
            if _imports_a_test_module(node):
                importers.append(f"{path.relative_to(TESTS)}:{node.lineno}")
    assert importers == []


def test_the_fixture_scripted_has_one_definition_in_the_shared_fixtures():
    definers = [
        str(path.relative_to(TESTS))
        for path in sorted(TESTS.rglob("*.py"))
        if any(isinstance(node, ast.FunctionDef) and node.name == "scripted" for node in ast.walk(ast.parse(path.read_text())))
    ]
    assert definers == ["conftest.py"]
