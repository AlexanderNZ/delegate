"""The workflow reference documents every workflow field, and its example is valid.

The reference is outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

import re
from pathlib import Path

import pytest

from delegate import delegate
from delegate.run.workflow import ADAPTERS, MODES, ROLES, STACK_FIELDS, TICKET_FIELDS, TOP_LEVEL_FIELDS

from .pages import repository_root

ROOT = repository_root(Path(__file__))
REFERENCE = ROOT / "docs" / "reference" / "workflow.md"

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


@outside_the_package
@pytest.mark.parametrize(
    "name",
    list(dict.fromkeys([*TOP_LEVEL_FIELDS, *STACK_FIELDS, *TICKET_FIELDS, *ROLES, *MODES, *ADAPTERS])),
)
def test_the_reference_names_each_workflow_field_mode_role_and_adapter(name):
    assert f"`{name}`" in REFERENCE.read_text()


@outside_the_package
def test_the_reference_example_workflow_passes_the_dry_run(tmp_path, capsys):
    blocks = re.findall(r"```toml\n(.*?)```", REFERENCE.read_text(), flags=re.DOTALL)
    assert blocks, "the reference must hold a toml example"
    workflow = tmp_path / "workflow.toml"
    workflow.write_text(blocks[0])

    code = delegate.main(["run", str(workflow), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert out.index("ticket 1 ") < out.index("ticket 2 ")  # the reference says ticket 1 comes first

