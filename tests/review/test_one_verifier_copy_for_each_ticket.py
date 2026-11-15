"""A ticket has one verifier copy for all its rounds, and the engine resets the copy between the rounds (#349).

A clone and a full checkout for each round cost git processes and time. The engine now makes the copy once for each
ticket and updates it from the repository before a later round. The copy still keeps the verifier away from the worktree
of the specialist, and a verifier sees no file that an earlier verifier left.

The engine cases call `delegate.main` against a real temporary git repository. A shim of `git` on `PATH` is the system
boundary: it logs each git process and passes it to the real git. The port cases call the git backend through the
version-control port.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from delegate.adapters.git import GitVersionControl
from delegate.cli import delegate

from ..support import WORKFLOW, ScriptedAdapter, commit_head, committed_repo, git, make_repo, read_journal


@pytest.fixture
def git_calls(tmp_path, monkeypatch):
    """A `git` first on PATH that appends its arguments to a log and runs the real git. Call the result for the logged calls."""
    shim, log = tmp_path / "shim", tmp_path / "git-calls.log"
    shim.mkdir()
    (shim / "git").write_text(f'#!{shutil.which("sh")}\nprintf \'%s\\n\' "$*" >> "{log}"\nexec "{shutil.which("git")}" "$@"\n')
    (shim / "git").chmod(0o755)
    monkeypatch.setenv("PATH", f"{shim}{os.pathsep}{os.environ['PATH']}")

    def calls() -> list[str]:
        return log.read_text().splitlines() if log.exists() else []

    return calls


pytestmark = pytest.mark.scripted_adapter()


# The temp prefixes where a guard lets a verifier write. On a host whose temp directory is in one, the copy is in the
# temp directory of the host. On any other host, as `/home/agent/.tmp` (#166), the copy is in the scratch directory of
# the session, and a new session moves it into its own scratch directory.
FIXED = ("/tmp/", "/var/folders/", "/private/tmp/", "/private/var/folders/")


@pytest.fixture(params=["temp-in-a-fixed-prefix", "temp-outside-the-fixed-prefixes"])
def host_temp(request, tmp_path, monkeypatch):
    """Give the host a temp directory in a fixed temp prefix, or outside them."""
    if request.param == "temp-in-a-fixed-prefix":
        if not os.access("/tmp", os.W_OK):
            pytest.skip("/tmp is not writable on this host")
        made = Path(tempfile.mkdtemp(dir="/tmp"))
    else:
        base = Path(os.path.realpath(tmp_path)) / "host-tmp"
        if not str(base).startswith(FIXED):
            base.mkdir()
            made = base
        elif os.access("/var/tmp", os.W_OK) and not os.path.realpath("/var/tmp").startswith(FIXED):
            made = Path(tempfile.mkdtemp(dir="/var/tmp"))
        else:
            pytest.skip("no writable temp directory outside the fixed temp prefixes")
    monkeypatch.setenv("TMPDIR", str(made))
    monkeypatch.setattr(tempfile, "tempdir", None)  # the module keeps the first value that it read
    yield made
    shutil.rmtree(made, ignore_errors=True)


def files_of(checkout: Path) -> dict[str, str]:
    """Each file of a checkout outside `.git`, with its text, by its path relative to the checkout."""
    found: dict[str, str] = {}
    for top, directories, names in os.walk(checkout):
        directories[:] = [d for d in directories if d != ".git"]
        for name in names:
            path = Path(top) / name
            found[path.relative_to(checkout).as_posix()] = path.read_text()
    return found


class Rounds:
    """A verifier that leaves files in its copy in the first round, and records what the copy holds in each round."""

    def __init__(self) -> None:
        self.files: list[dict[str, str]] = []
        self.heads: list[str] = []

    def __call__(self, request) -> None:
        copy = request.cwd
        self.files.append(files_of(copy))
        self.heads.append(git(copy, "rev-parse", "HEAD").strip())
        if len(self.files) == 1:
            (copy / "left-by-round-1.txt").write_text("untracked\n")
            (copy / "notes").mkdir()
            (copy / "notes" / "round-1.txt").write_text("untracked in a new directory\n")
            (copy / "seed.txt").write_text("changed by the verifier\n")


def run_two_rounds(repo: Path, capsys, scripted: ScriptedAdapter) -> tuple[Rounds, list[dict[str, object]]]:
    """Run one ticket whose first verifier rejects and whose second verifier accepts the fix-up."""
    rounds = Rounds()
    scripted.during_verifier = rounds
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = ["the header row is missing"]

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    return rounds, read_journal(captured.out)


def test_a_ticket_with_two_rounds_of_verification_clones_once_and_the_second_round_sees_the_fixup_commit(
    tmp_path, capsys, git_calls, scripted, host_temp
):
    repo = make_repo(tmp_path)

    started_before = len(git_calls())

    rounds, events = run_two_rounds(repo, capsys, scripted)

    clones = [line for line in git_calls()[started_before:] if " clone " in f" {line} "]
    assert len(clones) == 1, clones
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "ACCEPT"]
    fixup_tip = git(repo, "rev-parse", "run/demo-a").strip()
    assert git(repo, "log", "-1", "--format=%s", "run/demo-a").strip() != "seed"
    first, second = rounds.files
    assert first == {"seed.txt": "seed\n", "workflow.toml": first["workflow.toml"], "feature.txt": "feature\n"}
    assert rounds.heads[1] == fixup_tip
    assert rounds.heads[0] != fixup_tip
    assert second == {**first, "fixup-1.txt": "fix\n"}


def test_an_untracked_file_that_a_verifier_left_in_the_copy_is_gone_before_the_next_round(tmp_path, capsys, scripted, host_temp):
    repo = make_repo(tmp_path)

    rounds, _ = run_two_rounds(repo, capsys, scripted)

    second = rounds.files[1]
    assert "left-by-round-1.txt" not in second
    assert "notes/round-1.txt" not in second
    assert second["seed.txt"] == "seed\n"


def test_the_chain_of_an_economy_run_with_two_rounds_clones_once_and_removes_its_copy_at_the_end(
    tmp_path, capsys, git_calls, scripted, host_temp
):
    repo = make_repo(tmp_path, WORKFLOW.replace('mode = "assure"', 'mode = "economy"'))
    started_before = len(git_calls())

    rounds, events = run_two_rounds(repo, capsys, scripted)

    clones = [line for line in git_calls()[started_before:] if " clone " in f" {line} "]
    assert len(clones) == 1, clones
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "ACCEPT"]
    assert rounds.heads[1] == git(repo, "rev-parse", "run/demo-a").strip()
    assert "left-by-round-1.txt" not in rounds.files[1]
    assert [call.cwd.exists() for call in scripted.verifier_calls] == [False, False]
    assert sorted(path.name for path in host_temp.iterdir() if path.name.startswith("delegate-verif")) == []


# The port: the update of a copy that an earlier call made.


@pytest.fixture
def vcs() -> GitVersionControl:
    return GitVersionControl()


def repo_with_ticket_branch(tmp_path: Path) -> Path:
    """A repository with the run branch `run/demo` and the ticket branch `run/demo-a` one commit ahead of it."""
    repo = make_repo(tmp_path)
    git(repo, "branch", "run/demo", "main")
    git(repo, "checkout", "-q", "-b", "run/demo-a", "run/demo")
    (repo / "feature.txt").write_text("feature\n")
    git(repo, "add", "feature.txt")
    commit_head(repo, "-m", "feature")
    git(repo, "checkout", "-q", "main")
    return repo


def add_fixup(repo: Path) -> str:
    """Add a fix-up commit to `run/demo-a` and give its id."""
    git(repo, "checkout", "-q", "run/demo-a")
    (repo / "fixup.txt").write_text("fix\n")
    git(repo, "add", "fixup.txt")
    head = commit_head(repo, "-m", "fix-up")[0]
    git(repo, "checkout", "-q", "main")
    return head


def test_a_second_call_on_the_same_copy_holds_the_new_commit_and_no_file_or_ignored_file_of_the_verifier(tmp_path, vcs):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "scratch" / "copy"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    (copy / ".gitignore").write_text("build/\n")
    git(copy, "add", ".gitignore")
    commit_head(copy, "-m", "the verifier commits in its copy")
    (copy / "build").mkdir()
    (copy / "build" / "out.o").write_text("ignored\n")
    (copy / "loose.txt").write_text("untracked\n")
    git(copy, "checkout", "-q", "-b", "elsewhere")
    git(repo, "branch", "-f", "run/demo", "run/demo-a")
    fixup = add_fixup(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD").strip() == "run/demo-a"
    assert git(copy, "rev-parse", "HEAD").strip() == fixup
    assert git(copy, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo").strip()
    assert files_of(copy) == {
        "seed.txt": "seed\n", "workflow.toml": (repo / "workflow.toml").read_text(), "feature.txt": "feature\n", "fixup.txt": "fix\n",
    }
    assert git(copy, "status", "--porcelain", "--ignored").strip() == ""
    assert git(copy, "remote").strip() == ""


def test_a_hook_that_a_verifier_wrote_in_the_copy_does_not_run_when_the_copy_is_updated(tmp_path, vcs):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "copy"
    marker = tmp_path / "the-hook-ran"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    for name in ("post-checkout", "reference-transaction", "post-index-change"):
        hook = copy / ".git" / "hooks" / name
        hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n")
        hook.chmod(0o755)
    add_fixup(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert not marker.exists()
    assert "fixup.txt" in files_of(copy)
    assert not (copy / ".git" / "hooks" / "post-checkout").exists()


def test_a_copy_whose_settings_a_verifier_changed_is_made_again_from_the_start(tmp_path, vcs):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "copy"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    git(copy, "remote", "add", "origin", str(repo))
    add_fixup(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(copy, "remote").strip() == ""
    assert "fixup.txt" in files_of(copy)


def test_a_copy_whose_git_directory_a_verifier_sent_to_the_repository_is_made_again_and_the_repository_keeps_its_refs(
    tmp_path, vcs
):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "copy"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    # With a `commondir` file, git in the copy reads and writes the refs and the objects of the repository.
    (copy / ".git" / "commondir").write_text(f"{repo / '.git'}\n")
    git(repo, "branch", "kept-by-the-repository", "main")
    refs_before = git(repo, "for-each-ref")

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(repo, "for-each-ref") == refs_before
    assert not (copy / ".git" / "commondir").exists()
    assert "kept-by-the-repository" not in git(copy, "branch", "--list")


def test_a_copy_that_the_update_cannot_reset_is_made_again_from_the_start(tmp_path, vcs):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "copy"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    # A verifier that a stop cut off in a git command leaves the lock of the index, and no checkout can go on.
    (copy / ".git" / "index.lock").write_text("")
    (copy / "loose.txt").write_text("untracked\n")
    fixup = add_fixup(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(copy, "rev-parse", "HEAD").strip() == fixup
    assert not (copy / ".git" / "index.lock").exists()
    assert "loose.txt" not in files_of(copy)
    assert git(copy, "status", "--porcelain", "--ignored").strip() == ""


# The copy lives across the rounds, also while the fix-up specialist works. A writer in the copy between two rounds must
# not make it show a commit that the repository does not hold (review of #349).


@pytest.mark.parametrize("storage", ["loose", "packed"])
def test_a_replace_ref_that_a_writer_left_in_the_copy_does_not_change_what_the_next_round_reads(tmp_path, vcs, storage):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "copy"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    lie = tmp_path / "lie.txt"
    lie.write_text("not what the repository holds\n")
    git(copy, "replace", git(copy, "rev-parse", "HEAD:seed.txt").strip(), git(copy, "hash-object", "-w", str(lie)).strip())
    if storage == "packed":
        git(copy, "pack-refs", "--all")
    fixup = add_fixup(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert git(copy, "for-each-ref", "refs/replace/") == ""
    assert git(copy, "show", "HEAD:seed.txt") == "seed\n"
    assert (copy / "seed.txt").read_text() == "seed\n"
    assert git(copy, "rev-parse", "HEAD").strip() == fixup


def test_a_copy_that_a_writer_sent_to_other_objects_is_made_again_from_the_start(tmp_path, vcs):
    repo = repo_with_ticket_branch(tmp_path)
    other = committed_repo(tmp_path / "other", {"other.txt": "other\n"})
    copy = tmp_path / "copy"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    (copy / ".git" / "objects" / "info" / "alternates").write_text(f"{other / '.git' / 'objects'}\n")
    other_commit = git(other, "rev-parse", "HEAD").strip()
    fixup = add_fixup(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert not (copy / ".git" / "objects" / "info" / "alternates").exists()
    assert subprocess.run(["git", "-C", str(copy), "cat-file", "-e", other_commit], capture_output=True).returncode != 0
    assert git(copy, "rev-parse", "HEAD").strip() == fixup


@pytest.mark.parametrize("name", ["info/grafts", "shallow"])
def test_a_copy_with_a_file_that_cuts_the_history_is_made_again_from_the_start(tmp_path, vcs, name):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "copy"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    # Each file makes git show the tip of the ticket branch with no parent, so the history of the copy is not that of the repository.
    (copy / ".git" / name).write_text(git(copy, "rev-parse", "HEAD"))
    fixup = add_fixup(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert not (copy / ".git" / name).exists()
    assert git(copy, "rev-list", "HEAD") == git(repo, "rev-list", "run/demo-a")
    assert git(copy, "rev-parse", "HEAD").strip() == fixup


def test_a_tracked_file_that_a_writer_hid_from_the_status_is_as_the_commit_has_it_in_the_next_round(tmp_path, vcs):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "copy"
    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    git(copy, "update-index", "--skip-worktree", "seed.txt")
    (copy / "seed.txt").write_text("not what the repository holds\n")
    add_fixup(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")

    assert (copy / "seed.txt").read_text() == "seed\n"
    assert git(copy, "ls-files", "-t", "seed.txt") == "H seed.txt\n"


@pytest.mark.parametrize("round_of_copy", ["new copy", "updated copy"])
def test_the_copy_holds_the_tip_that_the_caller_read_also_when_the_branch_moved_after_the_read(tmp_path, vcs, round_of_copy):
    repo = repo_with_ticket_branch(tmp_path)
    copy = tmp_path / "copy"
    if round_of_copy == "updated copy":
        vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo")
    read = add_fixup(repo)
    moved = add_fixup_again(repo)

    vcs.make_verifier_copy(repo, copy, "run/demo-a", "run/demo", tip=read)

    assert git(copy, "rev-parse", "HEAD").strip() == read != moved
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD").strip() == "run/demo-a"
    assert git(copy, "status", "--porcelain", "--ignored").strip() == ""


def add_fixup_again(repo: Path) -> str:
    """Add a second fix-up commit to `run/demo-a` and give its id."""
    git(repo, "checkout", "-q", "run/demo-a")
    (repo / "fixup-2.txt").write_text("fix 2\n")
    git(repo, "add", "fixup-2.txt")
    head = commit_head(repo, "-m", "second fix-up")[0]
    git(repo, "checkout", "-q", "main")
    return head
