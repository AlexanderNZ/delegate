"""The driver of `delegate run` has one function for each path, and a short `main` (#343).

The paths are the dry run, the detached start, and the start or the resume. The behaviour of each path has its own tests
(`test_cli.py`, `test_run_detach.py`, `test_interrupt.py` and the others); this case checks the length of `main`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import delegate

DRIVER = Path(delegate.__file__).parent / "cli" / "run_command.py"
LIMIT = 60


def test_main_of_the_driver_of_run_is_shorter_than_sixty_lines():
    [main] = [node for node in ast.parse(DRIVER.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "main"]

    assert main.end_lineno is not None
    assert main.end_lineno - main.lineno + 1 < LIMIT
