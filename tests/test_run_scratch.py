"""Each agent session gets a scratch directory in the directory of the run, and the brief names it (#227).

A host in `dontAsk` mode refuses a write to `/tmp`, and a scratch file in the worktree stops the step at the check for
uncommitted changes. So the engine makes `runs/<run id>/scratch/<ticket>/<role>/` before each session, adds it to the
session, sets `TMPDIR` to it, and names it in the brief. `delegate clean` removes the scratch directories of a run that
it cleans.

Each case runs `delegate.main` against a real temporary git repository. The Claude Code cases start the real adapter on
a stand-in `claude` command, which records its arguments and its environment. A scripted adapter does the work of each
session (the commit, the report, the verdict) before the stand-in starts, so the run goes through the specialist, the
verifier, the fix-up and the second verifier.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate
from delegate.ports.harness import AdapterRequest, AdapterResult

from . import claude_fake
from .support import WORKFLOW, ScriptedAdapter, make_repo, read_journal, read_journal_file, state_dir, valid_report, write_project_agents

FINDING = "FINDING-2271 feature.txt has no header row"

#: The fixed temp prefixes of the verifier guards, as text.
FIXED = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")


class WorkingClaude:
    """The Claude Code adapter, with the work of each session done first by a scripted adapter.

    `run` lets `work` commit and write the report or the verdict, then starts the real adapter, so the stand-in
    `claude` records the command and the environment of the session. `scratch_existed` holds, for each session, whether
    the directory in `TMPDIR` of the request existed when the session started. Every other attribute is the attribute
    of the real adapter.
    """

    def __init__(self, real, work: ScriptedAdapter):
        self.real = real
        self.work = work
        self.scratch_existed: list[bool] = []

    def __getattr__(self, name: str):
        return getattr(self.real, name)

    def run(self, request: AdapterRequest) -> AdapterResult:
        if "touch delegate-guard-probe" not in request.prompt:
            self.scratch_existed.append(Path(request.environment.get("TMPDIR", "")).is_dir())
            self.work.run(request)
        return self.real.run(request)


@pytest.fixture
def working_claude(tmp_path, monkeypatch, git_identity, home):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    adapter = WorkingClaude(adapters.get("claude-code"), ScriptedAdapter())
    adapters.register("claude-code", adapter)
    yield adapter, fake
    adapters.unregister("claude-code")


@pytest.fixture
def scripted(scripted):
    """The scripted adapter of conftest, with the default files: ticket a writes `feature.txt`, which the gate needs."""
    scripted.files_by_ticket = {}
    return scripted


def run(repo: Path, capsys, *extra: str) -> tuple[int, str, str]:
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def clean(repo: Path, capsys, *args: str) -> tuple[int, str, str]:
    code = delegate.main(["clean", *args, "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def run_id_of(out: str) -> str:
    return str(read_journal(out)[0]["run_id"])


def scratch_of(repo: Path, run_id: str) -> Path:
    return state_dir(repo) / "runs" / run_id / "scratch"


def role_of(prompt: str) -> str:
    """The role of a session, from its brief."""
    if "## Working copy" in prompt:
        return "verifier"
    return "fix-up" if "## Findings to fix" in prompt else "specialist"


def added_directories(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, word in enumerate(argv) if word == "--add-dir"]


def test_the_harness_gets_the_scratch_directory_of_each_session_in_add_dir_and_tmpdir_and_the_brief_names_it(
    tmp_path, capsys, working_claude
):
    adapter, fake = working_claude
    adapter.work.verdict_sequence = ["REJECT", "ACCEPT"]
    adapter.work.verdict_findings = [FINDING]
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    write_project_agents(repo)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    scratch = scratch_of(repo, run_id_of(out)) / "a"
    calls = fake.calls()
    assert [role_of(str(call["stdin"])) for call in calls] == ["specialist", "verifier", "fix-up", "verifier"]
    for call in calls:
        role = role_of(str(call["stdin"]))
        directory = str(scratch / role)
        assert directory in added_directories(call["argv"]), (role, call["argv"])
        assert call["env"]["TMPDIR"] == directory, role
        assert f"`{directory}`" in call["stdin"], role
    assert adapter.scratch_existed == [True] * 4


@pytest.mark.parametrize("resumed", [False, True], ids=["new-agent", "resumed-session"])
def test_a_continuation_brief_names_the_scratch_directory_of_the_specialist(tmp_path, capsys, scripted, resumed):
    scripted.supports_resume = resumed
    scripted.files = {"other.txt": "other\n"}  # the gate needs feature.txt, so the gates are red and the specialist continues
    scripted.continuation_files = [{"feature.txt": "feature\n"}]
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (continuation,) = scripted.continuation_calls
    directory = scratch_of(repo, run_id_of(out)) / "a" / "specialist"
    assert continuation.environment["TMPDIR"] == str(directory)
    assert f"`{directory}`" in continuation.prompt


def test_a_report_correction_brief_names_the_scratch_directory_of_the_specialist(tmp_path, capsys, scripted):
    # The first session commits and writes no report, so the engine asks for a corrected report.
    scripted.report_text = lambda request, head: (
        json.dumps(valid_report("a", "run/demo-a", head)) if "## Continuation" in request.prompt else None
    )
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (correction,) = scripted.continuation_calls
    assert "is missing or not valid" in correction.prompt
    assert f"`{scratch_of(repo, run_id_of(out)) / 'a' / 'specialist'}`" in correction.prompt


def test_the_brief_says_to_write_each_scratch_file_there_and_never_to_tmp_or_the_tree(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    for request, tree in ((scripted.calls[0], "worktree"), (scripted.verifier_calls[0], "copy")):
        section = request.prompt.split("## Scratch directory\n\n", 1)[1].split("\n## ", 1)[0]
        assert f"`{request.environment['TMPDIR']}`" in section
        assert "Write each scratch file there" in section
        assert f"Never write a scratch file to `/tmp` or to the {tree}" in section
        assert "`TMPDIR`" in section


def test_clean_removes_the_scratch_directories_of_a_run_that_it_cleans(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, out, _ = run(repo, capsys)
    run_id = run_id_of(out)
    scratch = scratch_of(repo, run_id)
    assert sorted(path.name for path in (scratch / "a").iterdir()) == ["specialist", "verifier"]
    (scratch / "a" / "specialist" / "vitest.log").write_text("output\n")

    code, out, err = clean(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert not scratch.exists()
    assert f"removed the scratch directory ({scratch})" in out
    assert read_journal_file(repo, run_id)[-1]["scratch_removed"] == [str(scratch)]


def test_a_dry_run_clean_keeps_the_scratch_directories_and_says_that_it_would_remove_them(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    code, out, _ = run(repo, capsys)
    run_id = run_id_of(out)

    code, out, err = clean(repo, capsys, "--dry-run", run_id)

    assert (code, err) == (0, "")
    assert (scratch_of(repo, run_id) / "a" / "specialist").is_dir()
    assert f"would remove the scratch directory ({scratch_of(repo, run_id)})" in out


def test_an_abandon_removes_the_scratch_directories_of_the_run(tmp_path, capsys, scripted):
    scripted.failing_tickets = {"a"}
    repo = make_repo(tmp_path)
    code, out, _ = run(repo, capsys)
    run_id = run_id_of(out)
    assert (scratch_of(repo, run_id) / "a" / "specialist").is_dir()

    code, out, err = clean(repo, capsys, "--abandon", run_id, "--reason", "the ticket is gone")

    assert (code, err) == (0, "")
    assert not scratch_of(repo, run_id).exists()
    assert read_journal_file(repo, run_id)[-1]["scratch_removed"] == [str(scratch_of(repo, run_id))]


@pytest.fixture
def outside_fixed_prefixes(tmp_path):
    """A temp directory whose real path does not start with a fixed temp prefix, as `/home/agent/.tmp` on an agent host (#166).

    On macOS the real path of the pytest temp directory starts with /private/var/folders, and on a Linux host it is in
    /tmp, so the directory then goes in /var/tmp.
    """
    base = Path(os.path.realpath(tmp_path))
    if not str(base).startswith(FIXED):
        yield base
        return
    if not os.access("/var/tmp", os.W_OK):
        pytest.skip("no writable temp directory outside the fixed temp prefixes")
    made = Path(os.path.realpath(tempfile.mkdtemp(dir="/var/tmp")))
    try:
        if str(made).startswith(FIXED):
            pytest.skip("/var/tmp is in a fixed temp prefix on this host")
        yield made
    finally:
        shutil.rmtree(made, ignore_errors=True)


def test_on_a_host_whose_temp_directory_is_outside_the_fixed_prefixes_the_verifier_works_under_its_tmpdir(
    tmp_path, capsys, scripted, monkeypatch, outside_fixed_prefixes
):
    # The guard of a verifier permits a write under the fixed temp prefixes and under the runtime value of `TMPDIR`.
    # The session gets `TMPDIR` set to its scratch directory, so its copy, its proof copy and its report go there too.
    host_temp = outside_fixed_prefixes / "host-tmp"
    host_temp.mkdir()
    monkeypatch.setenv("TMPDIR", str(host_temp))
    monkeypatch.setattr(tempfile, "tempdir", None)  # the module keeps the first value that it read
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (verifier,) = scripted.verifier_calls
    session_temp = Path(verifier.environment["TMPDIR"])
    assert session_temp == scratch_of(repo, run_id_of(out)) / "a" / "verifier"
    assert verifier.cwd.is_relative_to(session_temp)
    assert verifier.report_path.is_relative_to(session_temp)
    assert [str(path) for path in verifier.added_directories] == [str(verifier.cwd.parent / "proof"), str(session_temp)]


def test_on_a_host_whose_temp_directory_is_in_a_fixed_prefix_the_verifier_copy_stays_there(tmp_path, capsys, scripted, monkeypatch):
    # An OpenCode verifier can write outside its copy only under a fixed temp prefix, so the copy stays in the temp
    # directory of the host when that directory is in one.
    if not os.access("/tmp", os.W_OK):
        pytest.skip("/tmp is not writable on this host")
    monkeypatch.setenv("TMPDIR", "/tmp")
    monkeypatch.setattr(tempfile, "tempdir", None)
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (verifier,) = scripted.verifier_calls
    assert str(verifier.cwd).startswith("/tmp/delegate-verify-")
    assert verifier.environment["TMPDIR"] == str(scratch_of(repo, run_id_of(out)) / "a" / "verifier")
