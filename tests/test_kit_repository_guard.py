"""No test can start a run against the repository of the kit itself (#285).

A draft test of #273 called `delegate run` with no `--repo` in the worktree of a specialist. The default of `--repo` is
the current directory, so the engine made branches and a worktree in the repository of the kit and started a real
session. An autouse fixture of `tests/conftest.py` now fails a test whose code looks up the paths of that repository
through the version control port. These cases drive `delegate run` as a test does.
"""

import subprocess
from pathlib import Path

import pytest

from delegate.adapters.git import GitVersionControl
from delegate.cli import delegate

from .conftest import KIT_PATHS
from .support import WORKFLOW, make_repo

KIT = Path(__file__).resolve().parents[1]

# A case that needs the repository of the kit. The Nix build runs the suite in a copy of the source with no `.git` entry:
# that copy is no repository, so the guard of `conftest` has nothing to guard, and the case has no repository to use.
needs_the_kit_repository = pytest.mark.skipif(
    KIT_PATHS is None, reason="the tree is a copy of the source with no .git entry, so it is no repository of the kit"
)


def kit_refs() -> str:
    """The branches and the worktrees of the repository of the kit, as one comparable value."""
    return "".join(
        subprocess.run(["git", "-C", str(KIT), *args], capture_output=True, text=True, check=True).stdout
        for args in (("branch", "--all"), ("worktree", "list"))
    )


@needs_the_kit_repository
def test_a_run_with_no_repo_in_the_repository_of_the_kit_fails_and_makes_nothing(
    scripted, tmp_path, monkeypatch, isolated_state_home, request
):
    workflow = make_repo(tmp_path, WORKFLOW) / "workflow.toml"
    monkeypatch.chdir(KIT)
    before = kit_refs()

    with pytest.raises(pytest.fail.Exception) as failure:
        delegate.main(["run", str(workflow)])

    message = str(failure.value)
    assert request.node.nodeid in message
    assert "--repo" in message
    assert kit_refs() == before
    assert list(isolated_state_home.iterdir()) == []


def test_a_dry_run_with_a_test_repository_works_from_the_repository_of_the_kit(scripted, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, WORKFLOW)
    monkeypatch.chdir(KIT)

    code = delegate.main(["run", str(repo / "workflow.toml"), "--dry-run", "--repo", str(repo)])

    out = capsys.readouterr().out
    assert code == 0
    assert "ticket a (1/1)" in out


def test_a_test_reads_a_file_of_the_kit_by_path_with_no_look_up_of_the_repository():
    assert (KIT / "pyproject.toml").is_file()
    assert (KIT / "src" / "delegate").is_dir()


def test_a_look_up_of_a_linked_worktree_of_a_test_repository_works(tmp_path):
    repo = make_repo(tmp_path)
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(repo), "worktree", "add", "-q", "-b", "side", str(linked)], check=True)

    top, common = GitVersionControl().repository_paths(linked)

    assert top.resolve() == linked.resolve()
    assert common == (repo / ".git").resolve()


@needs_the_kit_repository
@pytest.mark.kit_repository
def test_a_test_with_the_marker_may_look_up_the_repository_of_the_kit():
    top, common = GitVersionControl().repository_paths(KIT)

    assert top.resolve() == KIT.resolve()
    assert common.is_dir()


@needs_the_kit_repository
def test_a_look_up_of_a_subdirectory_of_the_kit_fails_at_the_port(request):
    with pytest.raises(pytest.fail.Exception) as failure:
        GitVersionControl().repository_paths(KIT / "src")

    assert request.node.nodeid in str(failure.value)
