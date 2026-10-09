"""The adapter how-to page: it names each part of the adapter interface, and its example adapter and contract test run.

The example adapter and its contract test are files on the page. A case writes
them to a temporary project and runs the contract test with pytest, as a
contributor does.
"""

import dataclasses
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from delegate import adapters

from .pages import HOW_TO, file_block, outside_the_package, write_files

PAGE = HOW_TO / "add-a-harness-adapter.md"
SOURCE = Path(__file__).resolve().parents[1] / "src"


@outside_the_package
@pytest.mark.parametrize(
    "part",
    [
        *(field.name for field in dataclasses.fields(adapters.AdapterRequest)),  # the request
        *(field.name for field in dataclasses.fields(adapters.AdapterResult)),  # the result
        *adapters.END_STATES,
        "tier_column", "supports_resume", "run", "AdapterRequest", "AdapterResult", "AdapterError",
        "register", "read_events", "stream_path",
    ],
)
def test_the_page_names_each_part_of_the_adapter_interface(part):
    assert f"`{part}`" in PAGE.read_text()


@outside_the_package
def test_the_example_adapter_passes_the_contract_test_of_the_page(tmp_path):
    project = tmp_path / "project"
    written = write_files(PAGE, project)
    assert {"myharness.py", "tests/test_myharness_adapter.py", "tests/fixtures/manifest.json"} <= set(written)
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(SOURCE), str(project), os.environ.get("PYTHONPATH")]))}

    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], cwd=project, env=env, capture_output=True, text=True
    )

    expected = len(re.findall(r"^def test_", file_block(PAGE, "tests/test_myharness_adapter.py").body, flags=re.MULTILINE))
    assert expected >= 5
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{expected} passed" in done.stdout
