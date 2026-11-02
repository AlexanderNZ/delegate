"""Each of the three commands prints its name and the version of the package with --version (#236)."""

import os
import shutil
import subprocess
import sys
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


def _copy_package(destination):
    """Copy the `delegate` package of this tree to `destination`, so a test can put it in a tree of its own."""
    shutil.copytree(ROOT / "src" / "delegate", destination / "delegate", ignore=shutil.ignore_patterns("__pycache__"))


def _write_metadata(directory, kind, version):
    """Write the metadata directory of the distribution with `version` in `directory`: `egg-info` or `dist-info`."""
    metadata = directory / f"agent_definitions-{version}.{kind}" if kind == "dist-info" else directory / "agent_definitions.egg-info"
    metadata.mkdir()
    (metadata / ("METADATA" if kind == "dist-info" else "PKG-INFO")).write_text(f"Metadata-Version: 2.1\nName: agent-definitions\nVersion: {version}\n")


def _version_printed_from(path_entry):
    """Run `delegate --version` with `path_entry` first on the module path, and return its stdout.

    The entries that were on the path stay after it: a Nix build gives the interpreter its packages
    (PyYAML) through PYTHONPATH, so a path with one entry would lose them. The entry is first, so its
    copy of the package, and its metadata directory, win over each other copy on the path."""
    entries = [str(path_entry), *filter(None, os.environ.get("PYTHONPATH", "").split(os.pathsep))]
    environment = {**os.environ, "PYTHONPATH": os.pathsep.join(entries)}
    result = subprocess.run(
        [sys.executable, "-m", "delegate.cli.delegate", "--version"], capture_output=True, text=True, env=environment, check=True
    )
    return result.stdout


def test_a_stale_metadata_directory_in_a_source_tree_does_not_set_the_version(tmp_path):
    # A source tree: pyproject.toml above src/delegate, and an old `pip install -e .` left an egg-info in src (#241).
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "agent-definitions"\nversion = "4.5.6"\n')
    source = tmp_path / "src"
    source.mkdir()
    _copy_package(source)
    _write_metadata(source, "egg-info", "1.7.1")

    assert _version_printed_from(source) == "delegate 4.5.6\n"


def test_an_installed_package_with_no_pyproject_above_it_reads_the_version_from_its_metadata(tmp_path):
    site = tmp_path / "site-packages"
    site.mkdir()
    _copy_package(site)
    _write_metadata(site, "dist-info", "7.8.9")

    assert _version_printed_from(site) == "delegate 7.8.9\n"
