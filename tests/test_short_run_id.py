"""A run id argument also takes its short form, the part after the last `-` (#115).

`delegate watch`, `delegate status`, `delegate pause` and `delegate run --resume` find the run of `ab12` as the run of
`20261016T030000Z-ab12`. A short id that more than one run has is an error that names each match, and the command
does nothing. A full run id keeps its meaning.

Each case calls `delegate.main([...])` against a real temporary git repository. A journal is written with the journal
class of the engine, and a live lock with the lock class of the engine, held by the process of the test. The resume
case resumes a real run that a child process started and killed.
"""

from __future__ import annotations

import json

from delegate.cli import delegate

from .support import (
    bare_repo, journal_path, kill_a_run, live_lock, make_repo, read_journal_file, resume_main, run_end, run_start, step_end,
    step_start, write_journal,
)
from .test_run_multi import HEAD, ticket

RUN_ID = "20261016T030000Z-ab12"
SHORT = "ab12"


def main(capsys, *argv: str) -> tuple[int, str, str]:
    code = delegate.main(list(argv))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def ended_run(repo, run_id: str = RUN_ID):
    return write_journal(repo, run_id, run_start(run_id, "a"), step_start("a"), step_end("a"), run_end(built=["a"]))


def test_watch_follows_the_run_of_a_short_id(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    ended_run(repo)
    ended_run(repo, "20261016T020000Z-cd34")

    code, out, err = main(capsys, "watch", SHORT, "--repo", str(repo), "--poll-seconds", "0.02", "--max-minutes", "0.1")

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert [line.split()[2] for line in lines[:-1]] == ["run-start", "step-start", "step-end", "run-end"]
    assert f"run_id={RUN_ID}" in lines[0]
    assert lines[-1] == "position 4"


def test_status_shows_the_run_of_a_short_id(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    ended_run(repo)
    ended_run(repo, "20261016T040000Z-cd34")

    code, out, err = main(capsys, "status", SHORT, "--repo", str(repo))

    assert (code, err) == (0, "")
    assert out.splitlines()[0].startswith(f"run {RUN_ID}: built")


def test_status_json_shows_the_run_of_a_short_id(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    ended_run(repo)

    code, out, err = main(capsys, "status", SHORT, "--repo", str(repo), "--json")

    assert (code, err) == (0, "")
    assert json.loads(out)["run_id"] == RUN_ID


def test_pause_writes_the_request_of_the_run_of_a_short_id_and_names_its_full_id(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, RUN_ID, run_start(RUN_ID, "a"), step_start("a"))

    with live_lock(path):
        code, out, err = main(capsys, "pause", SHORT, "--repo", str(repo))

    assert (code, err) == (0, "")
    assert out.splitlines() == [
        f"run {RUN_ID}: pause requested; the run stops at its next step boundary",
        f"request {path.parent / 'pause-request'}",
    ]
    assert (path.parent / "pause-request").is_file()


def test_run_resume_goes_on_with_the_run_of_a_short_id(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id, _ = kill_a_run(repo, 1)  # killed as the first specialist starts

    code, out, err = resume_main(repo, capsys, run_id.rsplit("-", 1)[-1], "--break-lock")

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == f"run {run_id}"
    events = read_journal_file(repo, run_id)
    assert [e["event"] for e in events].count("resume") == 1
    assert events[-1]["event"] == "run-end" and events[-1]["result"] == "built"


def test_a_full_run_id_that_is_the_short_id_of_another_run_keeps_its_meaning(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    ended_run(repo, "ab12")
    ended_run(repo)

    code, out, err = main(capsys, "status", "ab12", "--repo", str(repo))

    assert (code, err) == (0, "")
    assert out.splitlines()[0].startswith("run ab12: built")


def ambiguous(tmp_path):
    repo = bare_repo(tmp_path)
    ended_run(repo)
    ended_run(repo, "20261017T030000Z-ab12")
    return repo


AMBIGUOUS = f"run id 'ab12' matches more than one run: {RUN_ID}, 20261017T030000Z-ab12; give the full run id"


def test_an_ambiguous_short_id_is_an_error_of_status_that_names_the_matches(tmp_path, capsys):
    code, out, err = main(capsys, "status", SHORT, "--repo", str(ambiguous(tmp_path)))

    assert (code, out) == (2, "")
    assert AMBIGUOUS in err


def test_an_ambiguous_short_id_is_an_error_of_watch_that_names_the_matches(tmp_path, capsys):
    code, out, err = main(capsys, "watch", SHORT, "--repo", str(ambiguous(tmp_path)))

    assert (code, out) == (2, "")
    assert AMBIGUOUS in err


def test_an_ambiguous_short_id_is_an_error_of_pause_that_writes_no_request(tmp_path, capsys):
    repo = ambiguous(tmp_path)

    code, out, err = main(capsys, "pause", SHORT, "--repo", str(repo))

    assert (code, out) == (1, "")
    assert AMBIGUOUS in err
    assert not list((repo / ".git" / "delegate" / "runs").glob("*/pause-request"))


def test_an_ambiguous_short_id_is_an_error_of_run_resume_that_changes_no_journal(tmp_path, capsys):
    repo = ambiguous(tmp_path)
    before = journal_path(repo, RUN_ID).read_text()

    code, out, err = main(capsys, "run", "--resume", SHORT, "--repo", str(repo))

    assert (code, out) == (1, "")
    assert AMBIGUOUS in err
    assert journal_path(repo, RUN_ID).read_text() == before


def test_an_unknown_short_id_keeps_the_error_of_an_unknown_run(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    ended_run(repo)

    code, out, err = main(capsys, "status", "zz99", "--repo", str(repo))

    assert (code, out) == (2, "")
    assert "run 'zz99' not found" in err
