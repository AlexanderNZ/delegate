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


@pytest.mark.parametrize(
    ("bump", "expected"),
    [("patch", "1.2.4\n"), ("minor", "1.3.0\n"), ("major", "2.0.0\n")],
)
def test_each_bump_raises_its_part_of_the_newest_tag_and_resets_the_parts_after_it(tmp_path, bump, expected):
    repo = make_repo(tmp_path, version="0.1.0")
    git(repo, "tag", "v1.2.3")

    done = release(repo, "next-version", "--bump", bump)

    assert (done.returncode, done.stdout, done.stderr) == (0, expected, "")


def test_the_newest_tag_is_the_highest_version_and_a_tag_that_is_not_semver_is_ignored(tmp_path):
    repo = make_repo(tmp_path)
    for tag in ("v1.9.0", "v1.10.0", "v2.0", "v3.0.0-rc1", "release-9", "1.99.0", "v7.0.0.1", "v8.x.0"):
        git(repo, "tag", tag)

    done = release(repo, "next-version", "--bump", "patch")

    assert (done.returncode, done.stdout, done.stderr) == (0, "1.10.1\n", "")


def test_a_repository_whose_only_tags_are_not_semver_is_a_first_release(tmp_path):
    repo = make_repo(tmp_path, version="0.4.0")
    git(repo, "tag", "v1.0")

    done = release(repo, "next-version", "--bump", "major")

    assert (done.returncode, done.stdout) == (0, "0.4.0\n")


def test_a_bump_that_is_not_patch_minor_or_major_fails_with_a_usage_error(tmp_path):
    done = release(make_repo(tmp_path), "next-version", "--bump", "huge")

    assert done.returncode == 2
    assert "huge" in done.stderr and done.stdout == ""
