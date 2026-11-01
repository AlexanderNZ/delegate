"""Each of the three commands prints its name and the version of the package with --version (#236)."""

import tomllib

import pytest

from delegate.cli import agent_definitions, brief, delegate

from .pages import ROOT

# The version that the release step writes in pyproject.toml. The commands must print this value and no other.
PACKAGE_VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


@pytest.mark.parametrize(
    ("command", "main"),
    [("delegate", delegate.main), ("agent-definitions", agent_definitions.main), ("verifier-brief", brief.main)],
)
def test_the_option_prints_the_name_of_the_command_and_the_version_of_the_package(command, main, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == f"{command} {PACKAGE_VERSION}\n"
