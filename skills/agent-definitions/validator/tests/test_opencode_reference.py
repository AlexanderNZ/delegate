"""The OpenCode adapter reference states the command, the end states, the recorded streams and the live smoke run.

The reference is outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

import json
from pathlib import Path

import pytest

from .opencode_fake import FIXTURES

ROOT = Path(__file__).resolve().parents[4]
REFERENCE = ROOT / "docs" / "reference" / "opencode-adapter.md"

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


@outside_the_package
def test_the_reference_names_the_harness_version_and_the_date_of_the_recorded_streams_and_of_the_live_smoke_run():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())
    text = REFERENCE.read_text()
    smoke = text[text.index("## The live smoke run"):]
    assert f"OpenCode {manifest['harness_version']}" in text.split("## The live smoke run")[0]
    assert manifest["recorded"] in text.split("## The live smoke run")[0]
    assert f"OpenCode {manifest['harness_version']}" in smoke
    assert manifest["recorded"] in smoke


@outside_the_package
@pytest.mark.parametrize("fixture", sorted(path.name for path in FIXTURES.glob("*.jsonl")))
def test_the_reference_lists_each_recorded_stream(fixture):
    assert f"`{fixture}`" in REFERENCE.read_text()


@outside_the_package
@pytest.mark.parametrize("part", ["--format json", "--model <model>", "--agent <agent>", "--dir <directory>", "--session <session id>", "PWD"])
def test_the_reference_names_each_part_of_the_command(part):
    assert part in REFERENCE.read_text()


@outside_the_package
def test_the_readme_links_the_reference():
    assert "docs/reference/opencode-adapter.md" in (ROOT / "README.md").read_text()
