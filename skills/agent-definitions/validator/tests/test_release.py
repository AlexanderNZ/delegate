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


def commit(repo: Path, subject: str) -> str:
    """Add an empty commit with the subject. Return its short sha."""
    git(repo, "commit", "-q", "--allow-empty", "-m", subject)
    return git(repo, "rev-parse", "--short", "HEAD").strip()


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


def test_the_notes_group_the_commits_since_the_tag_by_prefix_oldest_first_and_leave_out_merges(tmp_path):
    repo = make_repo(tmp_path)
    commit(repo, "feat: before the tag")
    git(repo, "tag", "v1.0.0")
    docs = commit(repo, "docs: explain the tag")
    first = commit(repo, "feat(cli): add the flag")
    fix = commit(repo, "fix: stop the crash")
    other = commit(repo, "chore: tidy")
    second = commit(repo, "feat: second feature")
    git(repo, "checkout", "-q", "-b", "side")
    test = commit(repo, "test: cover the flag")
    ci = commit(repo, "ci: run on main")
    git(repo, "checkout", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'side'", "side")

    done = release(repo, "notes", "--version", "1.1.0")

    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout == f"""\
### Features

- feat(cli): add the flag ({first})
- feat: second feature ({second})

### Fixes

- fix: stop the crash ({fix})

### Documentation

- docs: explain the tag ({docs})

### Tests

- test: cover the flag ({test})

### CI

- ci: run on main ({ci})

### Other changes

- chore: tidy ({other})
"""


def test_the_notes_of_a_first_release_hold_every_commit_and_leave_out_a_group_with_no_commit(tmp_path):
    repo = make_repo(tmp_path)
    fix = commit(repo, "fix: stop the crash")
    near = commit(repo, "feature flags are on")
    seed = git(repo, "rev-list", "--max-parents=0", "HEAD").strip()[:7]
    git(repo, "tag", "v1.0")

    done = release(repo, "notes", "--version", "0.1.0")

    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout == f"""\
### Fixes

- fix: stop the crash ({fix})

### Other changes

- chore: seed ({seed})
- feature flags are on ({near})
"""


def test_the_notes_fail_when_no_commit_follows_the_previous_tag_and_name_the_tag(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "tag", "v1.0.0")

    done = release(repo, "notes", "--version", "1.0.1")

    assert done.returncode == 1 and done.stdout == ""
    assert "v1.0.0" in done.stderr


def test_the_notes_fail_when_the_path_is_not_a_git_repository(tmp_path):
    done = release(tmp_path, "notes", "--version", "1.0.0")

    assert done.returncode == 1 and done.stdout == ""
    assert str(tmp_path) in done.stderr


@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0-rc1", "one"])
def test_a_version_that_is_not_digits_in_three_parts_fails_and_the_message_names_it(tmp_path, version):
    done = release(make_repo(tmp_path), "notes", "--version", version)

    assert done.returncode == 2 and done.stdout == ""
    assert repr(version) in done.stderr


def test_the_first_release_fails_when_pyproject_is_missing_and_the_message_names_the_file(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "pyproject.toml").unlink()

    done = release(repo, "next-version", "--bump", "patch")

    assert done.returncode == 1 and done.stdout == ""
    assert "pyproject.toml" in done.stderr


def test_the_first_release_fails_when_the_version_of_pyproject_is_not_semver(tmp_path):
    done = release(make_repo(tmp_path, version="1.0"), "next-version", "--bump", "patch")

    assert done.returncode == 1 and done.stdout == ""
    assert "'1.0'" in done.stderr


def test_the_notes_start_after_the_highest_tag_below_the_version_even_when_the_tag_of_the_version_exists_or_a_tag_is_not_semver(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "tag", "v1.0.0")
    commit(repo, "fix: in 1.1.0")
    git(repo, "tag", "v1.1.0")
    before = commit(repo, "feat: before the candidate tag")
    git(repo, "tag", "v1.2.0-rc1")
    after = commit(repo, "fix: after the candidate tag")
    git(repo, "tag", "v1.2.0")

    done = release(repo, "notes", "--version", "1.2.0")

    assert (done.returncode, done.stderr) == (0, "")
    assert done.stdout == f"### Features\n\n- feat: before the candidate tag ({before})\n\n### Fixes\n\n- fix: after the candidate tag ({after})\n"


FILLED = "\n### Added\n\n- A thing.\n"


def test_apply_sets_the_version_and_moves_the_unreleased_body_into_a_dated_entry_under_an_empty_unreleased_heading(tmp_path):
    repo = make_repo(tmp_path, unreleased=FILLED)

    done = release(repo, "apply", "--version", "0.2.0", "--date", "2026-03-04")

    assert (done.returncode, done.stderr) == (0, "")
    assert "0.2.0" in done.stdout
    assert (repo / "pyproject.toml").read_text() == PYPROJECT.format(version="0.2.0")
    assert (repo / "CHANGELOG.md").read_text() == """\
# Changelog

## [Unreleased]

## [0.2.0] - 2026-03-04

### Added

- A thing.

## [0.1.0] - 2026-01-01

The first release.
"""


def test_apply_writes_the_notes_into_the_entry_when_the_unreleased_body_is_empty_and_the_unreleased_heading_stays(tmp_path):
    repo = make_repo(tmp_path, unreleased="\n \n")
    git(repo, "tag", "v0.1.0")
    feat = commit(repo, "feat: add the export")
    fix = commit(repo, "fix: stop the crash")

    done = release(repo, "apply", "--version", "0.2.0", "--date", "2026-03-04")

    assert (done.returncode, done.stderr) == (0, "")
    assert (repo / "CHANGELOG.md").read_text() == f"""\
# Changelog

## [Unreleased]

## [0.2.0] - 2026-03-04

### Features

- feat: add the export ({feat})

### Fixes

- fix: stop the crash ({fix})

## [0.1.0] - 2026-01-01

The first release.
"""
    assert (repo / "pyproject.toml").read_text() == PYPROJECT.format(version="0.2.0")


def test_a_second_apply_of_the_same_version_changes_nothing_and_says_so_even_after_the_release_commit_and_tag(tmp_path):
    repo = make_repo(tmp_path, unreleased=FILLED)
    assert release(repo, "apply", "--version", "0.2.0", "--date", "2026-03-04").returncode == 0
    git(repo, "commit", "-q", "-am", "release: v0.2.0")
    git(repo, "tag", "v0.2.0")
    before = {name: (repo / name).read_bytes() for name in ("pyproject.toml", "CHANGELOG.md")}

    done = release(repo, "apply", "--version", "0.2.0", "--date", "2026-04-05")

    assert (done.returncode, done.stderr) == (0, "")
    assert "nothing to change" in done.stdout
    assert {name: (repo / name).read_bytes() for name in before} == before
    assert git(repo, "status", "--porcelain") == ""
