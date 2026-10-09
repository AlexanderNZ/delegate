"""A bootstrapped context skill loads from any directory of the repository.

A coordinator shell in `app/` spawned a continuation, and the continuation
stopped in about 250 ms: each live-read block named its reference by a path
relative to the working directory, and that path did not resolve in `app/`.
Each case here runs the generated blocks with bash from a directory in the
repository, as Claude Code runs them when it loads the skill. The expected
output comes from the fixture documents, not from the code under test.
"""

import subprocess
from pathlib import Path

import pytest

from agent_definitions.cli import main
from tests.test_bootstrap import LONG_DOC, SHORT_DOC, _args, _blocks, _git, _paths

# The heading index of LONG_DOC: line 1 is "# Operate", line 201 is "## Runbooks".
LONG_DOC_INDEX = "1:# Operate\n201:## Runbooks\n"

_COMMIT = ("-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")


def _make_repo(tmp_path: Path, state: str) -> Path:
    """A repository with two reference documents and a source tree under app/."""
    r = tmp_path / "acme-api"
    (r / "docs").mkdir(parents=True)
    (r / "docs" / "gates.md").write_text(SHORT_DOC)
    (r / "docs" / "operate.md").write_text(LONG_DOC)
    (r / "app" / "src").mkdir(parents=True)
    (r / "app" / "src" / "main.py").write_text("print('acme')\n")
    for s in ("tdd", "test-quality"):
        d = r / ".claude" / "skills" / s
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\nname: {s}\ndescription: The {s} skill.\n---\n")
    (r / "prompt.md").write_text("Prove every change with the suite. Never push.\n")
    _git(r, "init", "-q")
    if state == "tracked":
        _git(r, "add", "-A")
        _git(r, *_COMMIT, "commit", "-q", "-m", "docs")
    elif state == "ignored":
        (r / ".gitignore").write_text("docs/\n")
    return r


@pytest.fixture(params=["tracked", "untracked", "ignored"])
def repo(request, tmp_path):
    """The "tracked" case commits the documents, as a real repository does. The
    "untracked" and "ignored" cases keep them out of the index, and "ignored"
    also lists them in .gitignore: the earlier sed block read any file on
    disk, and the new one must too.
    """
    return _make_repo(tmp_path, request.param)


@pytest.fixture
def tracked_repo(tmp_path):
    return _make_repo(tmp_path, "tracked")


def _run(block: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", block], cwd=cwd, capture_output=True, text=True)


def _skill_blocks(repo: Path) -> list[str]:
    assert main(_args(repo)) == 0
    blocks = _blocks(_paths(repo)["skill"].read_text())
    assert len(blocks) == 2
    return blocks


@pytest.mark.parametrize("cwd", [".", "app", "app/src"])
def test_full_text_block_prints_the_200_line_reference_from_root_and_subdirectory(repo, cwd):
    block = _skill_blocks(repo)[0]
    r = _run(block, repo / cwd)
    assert r.returncode == 0, f"{block!r} in {cwd}: {r.stderr}"
    assert r.stdout == SHORT_DOC


@pytest.mark.parametrize("cwd", [".", "app", "app/src"])
def test_index_block_prints_the_201_line_reference_headings_from_root_and_subdirectory(repo, cwd):
    block = _skill_blocks(repo)[1]
    r = _run(block, repo / cwd)
    assert r.returncode == 0, f"{block!r} in {cwd}: {r.stderr}"
    assert r.stdout == LONG_DOC_INDEX


def test_blocks_read_the_linked_worktree_files_from_its_subdirectory(tracked_repo, tmp_path):
    # A specialist works in a linked worktree. Its preload must show the files
    # of that worktree, its uncommitted change included, not the files of the
    # main checkout.
    full, index = _skill_blocks(tracked_repo)
    wt = tmp_path / "wt"
    _git(tracked_repo, "worktree", "add", "-q", "-b", "task", str(wt))
    (wt / "docs" / "operate.md").write_text(LONG_DOC + "## Worktree runbook\n")
    (wt / "docs" / "gates.md").write_text("# Gates in the worktree\n")

    r = _run(index, wt / "app")
    assert r.returncode == 0, r.stderr
    assert r.stdout == LONG_DOC_INDEX + "202:## Worktree runbook\n"

    r = _run(full, wt / "app" / "src")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "# Gates in the worktree\n"


def test_full_text_block_stops_at_200_lines_when_the_reference_grows(tracked_repo):
    # The block was chosen when the document had 200 lines. A later edit that
    # makes it longer must not load the whole document into every agent.
    full = _skill_blocks(tracked_repo)[0]
    with (tracked_repo / "docs" / "gates.md").open("a") as f:
        f.write("".join(f"added line {i}\n" for i in range(1, 51)))
    r = _run(full, tracked_repo / "app")
    assert r.returncode == 0, r.stderr
    assert r.stdout == SHORT_DOC
