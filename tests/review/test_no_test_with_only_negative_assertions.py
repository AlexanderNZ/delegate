"""No test has only assertions of absence (#366).

A test whose only assertions are `x not in out` or `assert not x` passes on empty output: a command that printed
nothing, or an action that did nothing, satisfies it. Each such test also asserts one part of the same result that must
be there.

A test counts as positive when one of these holds: it has one `assert` that is not an absence check, it runs code inside
`pytest.raises`, or it calls a helper that is named `assert_...` or `_assert_...`. The check reads the source of each
test file with `ast`.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent.parent


def _is_absence(test: ast.expr) -> bool:
    """`not x`, `x not in y`, and a boolean mix of only these."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return True
    if isinstance(test, ast.Compare):
        return all(isinstance(op, ast.NotIn) for op in test.ops)
    if isinstance(test, ast.BoolOp):
        return all(_is_absence(value) for value in test.values)
    return False


def _has_positive_proof(function: ast.AST) -> bool:
    for node in ast.walk(function):
        if isinstance(node, ast.Assert) and not _is_absence(node.test):
            return True
        if isinstance(node, ast.Attribute) and node.attr == "raises":
            return True
        if isinstance(node, ast.Call):
            name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
            if name.lstrip("_").startswith("assert"):
                return True
    return False


def functions_with_only_absence_assertions(source: str) -> list[str]:
    """The names of the test functions in `source` that assert only absence."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            asserts = [child for child in ast.walk(node) if isinstance(child, ast.Assert)]
            if asserts and not _has_positive_proof(node):
                found.append(node.name)
    return sorted(found)


def test_a_test_with_only_not_in_assertions_is_found():
    source = "def test_a(out):\n    assert 'x' not in out\n    assert not out.strip()\n"

    assert functions_with_only_absence_assertions(source) == ["test_a"]


def test_a_test_with_one_assertion_of_presence_is_not_found():
    source = "def test_a(out):\n    assert 'x' not in out\n    assert 'y' in out\n"

    assert functions_with_only_absence_assertions(source) == []


def test_a_test_that_expects_an_error_is_not_found():
    source = (
        "def test_a(path):\n    with pytest.raises(ValueError):\n        run(path)\n    assert not path.exists()\n"
    )

    assert functions_with_only_absence_assertions(source) == []


def test_a_test_that_calls_an_assert_helper_is_not_found():
    source = "def test_a(out):\n    _assert_permitted(out)\n    assert not out.strip()\n"

    assert functions_with_only_absence_assertions(source) == []


def test_no_test_file_has_a_test_with_only_assertions_of_absence():
    found = []
    for path in sorted(TESTS.rglob("test_*.py")):
        for name in functions_with_only_absence_assertions(path.read_text()):
            found.append(f"{path.relative_to(TESTS)}::{name}")

    assert found == []
