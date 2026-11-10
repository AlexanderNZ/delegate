"""No test replaces `sleep`, `time` or `monotonic` of the module `time` (#297).

A replacement of a function of the module `time` changes it for the whole process: also for each library that the code
calls during the test. `subprocess` waits for a git command with a time limit with `time.sleep`, so each wait of git
looked like a wait of the watch, and the cases failed on Linux. A case gives the commands its own clock through the
`clock` fixture of `tests/conftest.py` (`support.FakeClock`) instead.

The test reads each Python file under `tests/` with the `ast` module. It finds a call to `setattr`, `patch` or
`patch.object` (also as `monkeypatch.setattr` and `unittest.mock.patch`) that names the module `time` and one of the
three functions: `(time, "sleep", ...)` or the dotted text `"time.sleep"`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent

# The functions of the module `time` that no test replaces.
FUNCTIONS: frozenset[str] = frozenset({"sleep", "time", "monotonic"})

# The names of a call that replaces an attribute: `setattr`, `monkeypatch.setattr`, `patch`, `mock.patch` and `patch.object`.
REPLACERS: frozenset[str] = frozenset({"setattr", "patch", "object"})

# A dotted target that names a function of the module `time`, as `"time.sleep"` or `"delegate.run.watch.time.sleep"`.
DOTTED = re.compile(r"(?:^|\.)time\.(?:sleep|time|monotonic)$")


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _text(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _replaces_time(call: ast.Call) -> str | None:
    """The function of the module `time` that the call replaces, or None."""
    if _called_name(call) not in REPLACERS or not call.args:
        return None
    target = call.args[0]
    if isinstance(target, ast.Name) and target.id == "time" and len(call.args) > 1:
        function = _text(call.args[1])
        return function if function in FUNCTIONS else None
    dotted = _text(target)
    if dotted is not None and DOTTED.search(dotted):
        return dotted.rsplit(".", 1)[1]
    return None


def replacements(root: Path) -> list[str]:
    """One line `<file>:<line>: ...` for each call under `root` that replaces a function of the module `time`."""
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls = sorted((node for node in ast.walk(tree) if isinstance(node, ast.Call)), key=lambda node: node.lineno)
        for node in calls:
            if (function := _replaces_time(node)) is not None:
                found.append(
                    f"{path.relative_to(root.parent)}:{node.lineno}: replaces time.{function} for the whole process; "
                    "give the case the `clock` fixture of tests/conftest.py and set the time or the wait of its "
                    "`support.FakeClock`"
                )
    return found


def test_no_test_replaces_sleep_time_or_monotonic_of_the_module_time():
    found = replacements(TESTS)
    assert not found, "\n".join(found)


def test_each_form_of_a_replacement_of_a_function_of_the_module_time_is_found_with_its_file_and_line(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "import time\n"
        "from unittest import mock\n"
        "def test_a(monkeypatch):\n"
        "    monkeypatch.setattr(time, 'sleep', lambda s: None)\n"
        "    monkeypatch.setattr('time.time', lambda: 0)\n"
        "    with mock.patch('time.monotonic', return_value=0):\n"
        "        pass\n"
        "    with mock.patch.object(time, 'sleep'):\n"
        "        pass\n"
        "    monkeypatch.setattr(time, 'tzset', lambda: None)\n"
        "    monkeypatch.setattr('os.sleep', None)\n"
    )
    found = replacements(tests)
    assert [line.split(": ", 1)[0] for line in found] == [
        "tests/test_sample.py:4", "tests/test_sample.py:5", "tests/test_sample.py:6", "tests/test_sample.py:8",
    ]
    assert found[0] == (
        "tests/test_sample.py:4: replaces time.sleep for the whole process; give the case the `clock` fixture of "
        "tests/conftest.py and set the time or the wait of its `support.FakeClock`"
    )
