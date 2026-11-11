"""No test replaces `sleep`, `time` or `monotonic` of the module `time` (#297).

A replacement of a function of the module `time` changes it for the whole process: also for each library that the code
calls during the test. `subprocess` waits for a git command with a time limit with `time.sleep`, so each wait of git
looked like a wait of the watch, and the cases failed on Linux. A case gives the commands its own clock through the
`clock` fixture of `tests/conftest.py` (`support.FakeClock`) instead.

The test reads each Python file under `tests/` with the `ast` module. It finds a call to `setattr`, `patch` or
`patch.object` (also as `monkeypatch.setattr` and `unittest.mock.patch`) that names the module `time` and one of the
three functions: `(time, "sleep", ...)`, `(watch.time, "sleep", ...)` or the dotted text `"time.sleep"`. It also finds an
assignment, as `time.sleep = ...` or `watch.time.sleep = ...`. A name is the module `time` when the file binds it with
`import time` or `import time as <name>`. An attribute whose last part is `time` is the module `time`.
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


def _time_names(tree: ast.AST) -> frozenset[str]:
    """The names that the file binds to the module `time`: `import time` and `import time as <name>`."""
    return frozenset(
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "time"
    )


def _is_module_time(node: ast.expr, names: frozenset[str]) -> bool:
    """A bound name of the module, or an attribute whose last part is `time` (`watch.time`)."""
    if isinstance(node, ast.Name):
        return node.id in names
    return isinstance(node, ast.Attribute) and node.attr == "time"


def _replaces_time(call: ast.Call, names: frozenset[str]) -> str | None:
    """The function of the module `time` that the call replaces, or None."""
    if _called_name(call) not in REPLACERS or not call.args:
        return None
    target = call.args[0]
    if _is_module_time(target, names) and len(call.args) > 1:
        function = _text(call.args[1])
        return function if function in FUNCTIONS else None
    dotted = _text(target)
    if dotted is not None and DOTTED.search(dotted):
        return dotted.rsplit(".", 1)[1]
    return None


def _assigned_targets(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, ast.Assign):
        targets = list(node.targets)
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    else:
        return []
    flat = []
    while targets:
        target = targets.pop()
        if isinstance(target, (ast.Tuple, ast.List)):
            targets.extend(target.elts)
        else:
            flat.append(target)
    return flat


def _assigns_time(node: ast.AST, names: frozenset[str]) -> str | None:
    """The function of the module `time` that the assignment replaces, or None."""
    for target in _assigned_targets(node):
        if isinstance(target, ast.Attribute) and target.attr in FUNCTIONS and _is_module_time(target.value, names):
            return target.attr
    return None


def replacements(root: Path) -> list[str]:
    """One line `<file>:<line>: ...` for each call or assignment under `root` that replaces a function of `time`."""
    found = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _time_names(tree)
        hits = []
        for node in ast.walk(tree):
            function = _replaces_time(node, names) if isinstance(node, ast.Call) else _assigns_time(node, names)
            if function is not None:
                hits.append((node.lineno, function))
        for lineno, function in sorted(hits):
            found.append(
                f"{path.relative_to(root.parent)}:{lineno}: replaces time.{function} for the whole process; "
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


def test_a_replacement_through_an_attribute_an_alias_or_an_assignment_is_found_and_a_non_time_target_is_not(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_forms.py").write_text(
        "import os\n"
        "import time as t\n"
        "from unittest import mock\n"
        "from delegate.run import watch\n"
        "def test_a(monkeypatch):\n"
        "    monkeypatch.setattr(watch.time, 'sleep', lambda s: None)\n"
        "    monkeypatch.setattr(t, 'monotonic', lambda: 0)\n"
        "    with mock.patch.object(t, 'sleep'):\n"
        "        pass\n"
        "    t.sleep = lambda s: None\n"
        "    watch.time.time = lambda: 0\n"
        "    monkeypatch.setattr(t, 'tzset', lambda: None)\n"
        "    monkeypatch.setattr(os, 'sleep', None)\n"
        "    monkeypatch.setattr('os.sleep', None)\n"
        "    os.sleep = None\n"
        "    t.tzset = None\n"
        "    watch.os.sleep = None\n"
    )
    found = replacements(tests)
    assert [line.split(": ", 1)[0] for line in found] == [
        "tests/test_forms.py:6", "tests/test_forms.py:7", "tests/test_forms.py:8",
        "tests/test_forms.py:10", "tests/test_forms.py:11",
    ]


def test_a_plain_time_assignment_is_found_with_the_module_bound_by_import_time(tmp_path):
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_plain.py").write_text("import time\ndef test_a():\n    time.sleep = lambda s: None\n")
    assert [line.split(": ", 1)[0] for line in replacements(tests)] == ["tests/test_plain.py:3"]
