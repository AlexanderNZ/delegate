"""A command imports only the code of its own subcommand, so the start of `status`, `watch`, `runs` and `run` is fast (#344).

The import of the definitions (with PyYAML) and of `importlib.metadata` costs about half a second at each start of the command on a
machine with an endpoint scanner. A case runs the command in a fresh interpreter and reads `sys.modules` after it. The expected values
come from the ticket: no module of `delegate.definitions`, no `yaml`, and no `importlib.metadata`.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

PROBE = """\
import sys
from delegate.cli.delegate import main

try:
    main(sys.argv[1:])
except SystemExit:
    pass
loaded = sorted(
    name for name in sys.modules
    if name == "yaml" or name.startswith(("yaml.", "delegate.definitions", "importlib.metadata"))
)
print("LOADED " + " ".join(loaded))
"""


def modules_loaded_by(*args: str) -> list[str]:
    """Run `delegate <args>` in a fresh interpreter and return the heavy modules that it imported."""
    result = subprocess.run([sys.executable, "-c", PROBE, *args], capture_output=True, text=True, check=True)
    line = next(line for line in result.stdout.splitlines() if line.startswith("LOADED"))
    return line.split()[1:]


@pytest.mark.parametrize("command", ["status", "watch", "runs", "run"])
def test_the_help_of_a_run_command_imports_no_definitions_and_no_yaml(command):
    assert modules_loaded_by(command, "--help") == []


def test_version_imports_neither_the_definitions_nor_yaml():
    assert modules_loaded_by("--version") == []


def test_the_help_of_render_still_loads_the_definitions():
    # The definitions are not gone: `render` needs them, and an import by name of the subcommand brings them.
    loaded = modules_loaded_by("render", "--help")

    assert "yaml" in loaded
    assert "delegate.definitions.render" in loaded
