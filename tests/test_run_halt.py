"""A failure of the harness halts the run (#177).

An `AdapterError` from `Adapter.run` is a failure of the harness, not of the work: a harness that is not logged in, a
CLI that is gone, a rate limit. The run stops as a Ctrl-C stops it. The journal holds `halted` and no `step-end` and
no `run-end`, the lock is released, and `delegate run --resume` goes on at the open step. Each other exception from the
adapter is a crash, as before.

Each case calls `delegate.main` against a real temporary git repository with the scripted adapter.
"""

import json
import shutil

from delegate.ports.harness import AdapterError

from .support import state_dir
from .support import event_names, make_repo, read_journal_file, resume_main, run_main, ticket_of
from .test_run_multi import HEAD, ticket

CHAIN = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",))

# The exit code of `delegate run` for a halted run, and of `delegate watch` at a `halted` event.
RUN_HALTED = 4
WATCH_HALTED = 8


def not_logged_in(request):
    if ticket_of(request) == "b":
        raise AdapterError("claude: not logged in")


def halted_run(repo, capsys, scripted):
    scripted.on_specialist = not_logged_in
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    return code, out, err, run_dir.name


def test_an_adapter_error_halts_the_run_and_a_resume_builds_the_open_step_and_the_rest(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CHAIN)

    code, out, err, run_id = halted_run(repo, capsys, scripted)

    assert code == RUN_HALTED
    events = read_journal_file(repo, run_id)
    assert event_names(events)[-1] == "halted"
    halted = events[-1]
    assert (halted["ticket"], halted["role"], halted["round"]) == ("b", "specialist", 0)
    assert halted["message"] == "claude: not logged in"
    assert not [e for e in events if e["event"] in ("run-end", "skip") or (e["event"] == "step-end" and e["ticket"] == "b")]
    assert "claude: not logged in" in err
    assert f"delegate run --resume {run_id}" in err + out

    scripted.on_specialist = None
    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0, err
    events = read_journal_file(repo, run_id)
    assert event_names(events)[-1] == "run-end"
    assert (events[-1]["result"], events[-1]["built"]) == ("built", ["a", "b", "c"])


def test_an_exception_that_is_not_an_adapter_error_is_a_crash_and_not_a_halt(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CHAIN)

    def explode(request):
        if ticket_of(request) == "b":
            raise RuntimeError("harness binary missing")

    scripted.on_specialist = explode

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 4  # a crash leaves the step open and exits with the code of a halt (#281)
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    events = read_journal_file(repo, run_dir.name)
    assert "halted" not in event_names(events) and "run-end" not in event_names(events)
    assert (events[-1]["event"], events[-1]["ticket"], events[-1]["message"]) == ("crashed", "b", "harness binary missing")
    assert ("b", "failed") not in [(e["ticket"], e["state"]) for e in events if e["event"] == "step-end"]


def test_an_adapter_error_from_the_verifier_halts_the_run_and_a_resume_goes_on(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CHAIN)

    def logged_out(request):
        raise AdapterError("claude: rate limit")

    scripted.during_verifier = logged_out
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    (run_dir,) = (state_dir(repo) / "runs").iterdir()

    assert code == RUN_HALTED
    events = read_journal_file(repo, run_dir.name)
    assert event_names(events)[-1] == "halted"
    assert (events[-1]["ticket"], events[-1]["role"], events[-1]["message"]) == ("a", "verifier", "claude: rate limit")

    scripted.during_verifier = None
    code, out, err = resume_main(repo, capsys, run_dir.name)

    assert code == 0, err
    events = read_journal_file(repo, run_dir.name)
    assert (events[-1]["event"], events[-1]["result"], events[-1]["built"]) == ("run-end", "built", ["a", "b", "c"])


def test_status_shows_a_halted_run_as_halted_with_the_message(tmp_path, capsys, scripted):
    from delegate.cli import delegate

    repo = make_repo(tmp_path, CHAIN)
    _, _, _, run_id = halted_run(repo, capsys, scripted)
    capsys.readouterr()

    assert delegate.main(["status", run_id, "--repo", str(repo)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith(f"run {run_id}: halted")
    assert any("claude: not logged in" in line for line in lines)

    assert delegate.main(["status", run_id, "--repo", str(repo), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "halted"


def test_watch_exits_with_the_halt_code_on_a_halted_run(tmp_path, capsys, scripted):
    from delegate.cli import delegate

    repo = make_repo(tmp_path, CHAIN)
    _, _, _, run_id = halted_run(repo, capsys, scripted)
    capsys.readouterr()

    code = delegate.main(["watch", run_id, "--repo", str(repo), "--raw", "--poll-seconds", "0.01", "--max-minutes", "1"])

    assert code == WATCH_HALTED
    assert "halted" in capsys.readouterr().out


def test_watch_with_no_run_id_names_the_halted_run_that_stopped(tmp_path, capsys, scripted):
    from delegate.cli import delegate

    repo = make_repo(tmp_path, CHAIN)
    _, _, _, run_id = halted_run(repo, capsys, scripted)
    capsys.readouterr()

    code = delegate.main(["watch", "--repo", str(repo), "--raw"])

    out = capsys.readouterr()
    assert code == WATCH_HALTED
    assert "halted" in out.out + out.err and f"delegate run --resume {run_id}" in out.out + out.err


def test_an_adapter_error_from_the_verifier_of_the_chain_halts_an_economy_run_and_a_resume_goes_on(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, CHAIN.replace('mode = "assure"', 'mode = "economy"'))

    def rate_limited(request):
        raise AdapterError("claude: rate limit")

    scripted.during_verifier = rate_limited
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    (run_dir,) = (state_dir(repo) / "runs").iterdir()

    assert code == RUN_HALTED
    events = read_journal_file(repo, run_dir.name)
    assert event_names(events)[-1] == "halted" and events[-1]["role"] == "verifier"
    assert [e["ticket"] for e in events if e["event"] == "step-end"] == ["a", "b", "c"]
    assert "run-end" not in event_names(events)

    scripted.during_verifier = None
    code, out, err = resume_main(repo, capsys, run_dir.name)

    assert code == 0, err
    events = read_journal_file(repo, run_dir.name)
    assert (events[-1]["event"], events[-1]["result"], events[-1]["built"]) == ("run-end", "built", ["a", "b", "c"])


def only_git_on_path(tmp_path, monkeypatch) -> None:
    """Make `git` the only command on PATH, so the harness command of an adapter does not exist."""
    bin_dir = tmp_path / "git-only-bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    monkeypatch.setenv("PATH", str(bin_dir))


def test_a_harness_command_that_does_not_exist_halts_the_run_and_names_the_command(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, (HEAD + ticket("a")).replace('adapter = "scripted"', 'adapter = "opencode"'))
    only_git_on_path(tmp_path, monkeypatch)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == RUN_HALTED, err
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    events = read_journal_file(repo, run_dir.name)
    assert event_names(events)[-1] == "halted"
    assert "opencode" in events[-1]["message"]
    assert not [e for e in events if e["event"] in ("run-end", "step-end")]
    assert "opencode" in err
    assert "Traceback" not in err
