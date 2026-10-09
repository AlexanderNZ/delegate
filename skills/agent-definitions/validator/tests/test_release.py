"""`scripts/release.py`: the next version, the release notes, and the CHANGELOG and version edit of a release.

The script sits outside the package source, so a Nix build that copies only the
package skips these cases with a reason. Each case runs the script as a command
against a real temporary git repository.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from .pages import ROOT, outside_the_package
from .support import git

pytestmark = outside_the_package

SCRIPT = ROOT / "scripts" / "release.py"

PYPROJECT = '[project]\nname = "demo"\nversion = "{version}"\n'
CHANGELOG = """\
# Changelog

## [Unreleased]
{unreleased}
## [0.1.0] - 2026-01-01

The first release.
"""


def make_repo(tmp_path: Path, version: str = "0.1.0", unreleased: str = "") -> Path:
    """A real git repository on `main` with a minimal `pyproject.toml` and `CHANGELOG.md`, and one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "pyproject.toml").write_text(PYPROJECT.format(version=version))
    (repo / "CHANGELOG.md").write_text(CHANGELOG.format(unreleased=unreleased))
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "chore: seed")
    return repo


def release(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the script as a command on the repository."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--pyproject", "pyproject.toml", *args],
        capture_output=True,
        text=True,
    )


def test_the_first_release_is_the_version_of_pyproject_unchanged_when_no_version_tag_exists(tmp_path):
    repo = make_repo(tmp_path, version="0.3.7")

    done = release(repo, "next-version", "--bump", "minor")

    assert (done.returncode, done.stdout, done.stderr) == (0, "0.3.7\n", "")
