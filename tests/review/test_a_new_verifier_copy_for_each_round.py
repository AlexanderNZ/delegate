"""Each round of verification gets a new verifier copy (#349, and the ruling of the coordinator on it).

The first form of #349 kept one copy for all the rounds of a ticket and reset it before a later round. A writer in the
copy between two rounds (the verifier before, or the fix-up specialist) could make it show content that the repository
does not hold, and each new check of the reset closed only one form. So each round clones a new copy again, in a new
directory, and the copy of the round before is gone. The copy holds the tip of the ticket branch that the engine read.

The engine cases call `delegate.main` against a real temporary git repository. A shim of `git` on `PATH` is the system
boundary: it logs each git process and passes it to the real git. The port case calls the git backend through the
version-control port.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import zlib
from pathlib import Path

import pytest

from delegate.adapters.git import GitVersionControl
from delegate.cli import delegate

from ..support import WORKFLOW, ScriptedAdapter, commit_head, git, make_repo, read_journal

pytestmark = pytest.mark.scripted_adapter()

LIE = b"not what the repository holds\n"


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


# The temp prefixes where a guard lets a verifier write. On a host whose temp directory is in one, the copy is in the
# temp directory of the host. On any other host, as `/home/agent/.tmp` (#166), the copy is in the scratch directory of
# the session. Each session makes its own copy in either place.
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
    """A verifier that writes in its copy in the first round, and records what the copy shows in each round.

    The first verifier leaves untracked files, changes a tracked file, plants a replace ref behind a symbolic link that
    makes `seed.txt` show another text, and replaces the loose object of `seed.txt` with a forged one. The fix-up keeps
    `seed.txt`, so the tip of the second round uses the same object again.
    """

    def __init__(self, outside: Path) -> None:
        self.outside = outside
        self.files: list[dict[str, str]] = []
        self.heads: list[str] = []
        self.shown: list[str] = []
        self.replace_refs: list[str] = []
        self.copies: list[Path] = []
        self.earlier_copy_exists: list[bool] = []
        self.planted: tuple[str, str] | None = None  # what the first copy shows after the replace ref, and after the forged object with no replace refs

    def __call__(self, request) -> None:
        copy = request.cwd
        self.earlier_copy_exists += [earlier.exists() for earlier in self.copies]
        self.copies.append(copy)
        self.files.append(files_of(copy))
        self.heads.append(git(copy, "rev-parse", "HEAD").strip())
        self.shown.append(git(copy, "show", "HEAD:seed.txt"))
        self.replace_refs.append(git(copy, "for-each-ref", "refs/replace/"))
        if len(self.files) == 1:
            (copy / "left-by-round-1.txt").write_text("untracked\n")
            (copy / "notes").mkdir()
            (copy / "notes" / "round-1.txt").write_text("untracked in a new directory\n")
            (copy / "feature.txt").write_text("changed by the verifier\n")
            plant_replace_ref_behind_a_link(copy, self.outside)
            through_the_link = git(copy, "show", "HEAD:seed.txt")  # the object of seed.txt is still the true one here
            forge_loose_object(copy, "seed.txt")
            self.planted = (through_the_link, git(copy, "--no-replace-objects", "show", "HEAD:seed.txt"))


def plant_replace_ref_behind_a_link(copy: Path, outside: Path) -> None:
    """Make `seed.txt` of HEAD show `LIE`, with a replace ref in a directory that a symbolic link under refs/replace names."""
    lie = outside / "lie.txt"
    lie.write_bytes(LIE)
    new = git(copy, "hash-object", "-w", str(lie)).strip()
    elsewhere = outside / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / git(copy, "rev-parse", "HEAD:seed.txt").strip()).write_text(new + "\n")
    replace = copy / ".git" / "refs" / "replace"
    replace.mkdir(parents=True, exist_ok=True)
    os.symlink(elsewhere, replace / "sub")


def forge_loose_object(copy: Path, path: str) -> None:
    """Write `LIE` as the loose object of the blob of `path` at HEAD: git does not check the hash of a loose object that it reads."""
    oid = git(copy, "rev-parse", f"HEAD:{path}").strip()
    loose = copy / ".git" / "objects" / oid[:2] / oid[2:]
    loose.parent.mkdir(exist_ok=True)
    if loose.exists():
        loose.chmod(0o644)
        loose.unlink()
    loose.write_bytes(zlib.compress(b"blob %d\0" % len(LIE) + LIE))


def run_two_rounds(repo: Path, capsys, scripted: ScriptedAdapter, outside: Path) -> tuple[Rounds, list[dict[str, object]]]:
    """Run one ticket whose first verifier rejects and whose second verifier accepts the fix-up."""
    outside.mkdir()
    rounds = Rounds(outside)
    scripted.during_verifier = rounds
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    scripted.verdict_findings = ["the header row is missing"]

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    return rounds, read_journal(captured.out)


def test_each_round_of_a_ticket_clones_a_new_copy_in_its_own_directory_and_the_copy_of_the_round_before_is_gone(
    tmp_path, capsys, git_calls, scripted, host_temp
):
    repo = make_repo(tmp_path)
    started_before = len(git_calls())

    rounds, events = run_two_rounds(repo, capsys, scripted, tmp_path / "outside")

    clones = [line for line in git_calls()[started_before:] if " clone " in f" {line} "]
    assert len(clones) == 2, clones
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "ACCEPT"]
    first_copy, second_copy = rounds.copies
    assert first_copy != second_copy
    assert rounds.earlier_copy_exists == [False]
    assert [call.cwd.exists() for call in scripted.verifier_calls] == [False, False]


def test_a_later_round_sees_the_fixup_commit_and_no_file_of_an_earlier_round(tmp_path, capsys, scripted, host_temp):
    repo = make_repo(tmp_path)

    rounds, _ = run_two_rounds(repo, capsys, scripted, tmp_path / "outside")

    first, second = rounds.files
    assert first == {"seed.txt": "seed\n", "workflow.toml": first["workflow.toml"], "feature.txt": "feature\n"}
    fixup_tip = git(repo, "rev-parse", "run/demo-a").strip()
    assert rounds.heads == [rounds.heads[0], fixup_tip] and rounds.heads[0] != fixup_tip
    assert second == {**first, "fixup-1.txt": "fix\n"}


def test_a_replace_ref_behind_a_link_and_a_forged_loose_object_of_an_earlier_round_do_not_reach_the_next_round(
    tmp_path, capsys, scripted, host_temp
):
    # The two forms of the second review of #349: each passed the checks of the reset of a kept copy.
    repo = make_repo(tmp_path)

    rounds, _ = run_two_rounds(repo, capsys, scripted, tmp_path / "outside")

    assert rounds.planted == (LIE.decode(), LIE.decode())  # each form works in the copy where it was written
    assert rounds.shown == ["seed\n", "seed\n"]
    assert rounds.replace_refs == ["", ""]
    assert rounds.files[1]["seed.txt"] == "seed\n"
    assert git(repo, "rev-parse", "run/demo-a:seed.txt") == git(repo, "rev-parse", "run/demo-a~1:seed.txt")  # the same object again


def test_the_chain_of_an_economy_run_with_two_rounds_clones_a_copy_for_each_round_and_removes_each(
    tmp_path, capsys, git_calls, scripted, host_temp
):
    repo = make_repo(tmp_path, WORKFLOW.replace('mode = "assure"', 'mode = "economy"'))
    started_before = len(git_calls())

    rounds, events = run_two_rounds(repo, capsys, scripted, tmp_path / "outside")

    clones = [line for line in git_calls()[started_before:] if " clone " in f" {line} "]
    assert len(clones) == 2, clones
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["REJECT", "ACCEPT"]
    assert rounds.heads[1] == git(repo, "rev-parse", "run/demo-a").strip()
    assert rounds.earlier_copy_exists == [False]
    assert "left-by-round-1.txt" not in rounds.files[1] and rounds.shown[1] == "seed\n"
    assert [call.cwd.exists() for call in scripted.verifier_calls] == [False, False]
    assert sorted(path.name for path in host_temp.iterdir() if path.name.startswith("delegate-verif")) == []


# The port: the copy holds the tip that the caller read.


def test_the_copy_holds_the_tip_that_the_caller_read_also_when_the_branch_moved_after_the_read(tmp_path):
    repo = make_repo(tmp_path)
    git(repo, "branch", "run/demo", "main")
    git(repo, "checkout", "-q", "-b", "run/demo-a", "run/demo")
    read = add_commit(repo, "feature.txt")
    moved = add_commit(repo, "fixup.txt")
    git(repo, "checkout", "-q", "main")
    copy = tmp_path / "copy"

    GitVersionControl().make_verifier_copy(repo, copy, "run/demo-a", "run/demo", tip=read)

    assert git(copy, "rev-parse", "HEAD").strip() == read != moved
    assert git(copy, "rev-parse", "--abbrev-ref", "HEAD").strip() == "run/demo-a"
    assert git(copy, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "run/demo").strip()
    assert git(copy, "status", "--porcelain", "--ignored").strip() == ""


def add_commit(repo: Path, name: str) -> str:
    """Commit the new file `name` on the branch that is checked out, and give the commit."""
    (repo / name).write_text(f"{name}\n")
    git(repo, "add", name)
    return commit_head(repo, "-m", name)[0]
