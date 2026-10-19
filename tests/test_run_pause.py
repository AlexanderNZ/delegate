"""`delegate pause <run-id>`: a run stops at the next step boundary (#108).

The pause writes a request file in the directory of the run. The engine reads
it at each step boundary: before a step starts, before a gate runs, and before
a verifier starts. It stops there, so no agent is cut off. The journal records
`pause-requested` and `paused`, `delegate status` shows `pausing` and then
`paused`, and `delegate run --resume` goes on from a paused run, also with a
plan change.

Each case runs `delegate.main` against a real temporary git repository with
the scripted adapter. The pause comes from another command while the run
works: from the scripted specialist, or from a gate command, as from a second
shell.
"""

import json
import sys

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, event_names, git, journal_path, make_repo, read_journal_file, resume_main, run_main, ticket_of, tree
from .test_run_multi import HEAD, ticket

ECONOMY_HEAD = HEAD.replace('mode = "assure"', 'mode = "economy"')

# The exit code of `delegate run` for a run that paused, and of `delegate watch` at a `paused` event.
RUN_PAUSED = 3
WATCH_PAUSED = 7

# A gate command that asks for a pause of its own run while it runs, as a second shell does. The worktree of a ticket
# is `worktrees/<run-id>/<ticket>` in the state directory, so the gate finds the run id in its working directory.
PAUSE_GATE = """\
import json, sys
from pathlib import Path
sys.path[:0] = json.loads(Path(sys.argv[1]).read_text())
from delegate.cli.delegate import main
sys.exit(main(["pause", Path.cwd().parent.name, "--repo", str(Path.cwd())]))
"""


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abcx"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run_id_of(repo) -> str:
    (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
    return run_dir.name


def start(repo, capsys) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))


def pause(repo, capsys, run_id: str) -> tuple[int, str, str]:
    code = delegate.main(["pause", run_id, "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def status_lines(repo, capsys, run_id: str) -> list[str]:
    assert delegate.main(["status", run_id, "--repo", str(repo)]) == 0
    return capsys.readouterr().out.splitlines()


def status_json(repo, capsys, run_id: str) -> dict:
    assert delegate.main(["status", run_id, "--repo", str(repo), "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def pause_during_the_specialist_of(repo, capsys, scripted, ticket_id: str, seen: list | None = None) -> None:
    """Ask for a pause from a second command while the specialist of `ticket_id` works.

    `seen` gets the first line of `delegate status` and the state of `delegate status --json` after the request.
    """

    def request(request):
        if ticket_of(request) != ticket_id:
            return
        code, out, err = pause(repo, capsys, run_id_of(repo))
        assert (code, err) == (0, ""), err
        assert "pause" in out
        if seen is not None:
            seen.append(status_lines(repo, capsys, run_id_of(repo))[0])
            seen.append(status_json(repo, capsys, run_id_of(repo))["state"])

    scripted.on_specialist = request


def clear_calls(scripted) -> None:
    for calls in (scripted.calls, scripted.verifier_calls, scripted.fixup_calls, scripted.continuation_calls):
        calls.clear()


def tail(events, count: int) -> list[str]:
    return event_names(events)[-count:]


def test_a_pause_during_a_specialist_lets_the_step_end_and_the_next_step_does_not_start(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY_HEAD + ticket("a") + ticket("b"))
    pause_during_the_specialist_of(repo, capsys, scripted, "a")

    code, out, err = start(repo, capsys)

    assert code == RUN_PAUSED, err
    run_id = run_id_of(repo)
    assert f"delegate run --resume {run_id}" in out
    assert [ticket_of(call) for call in scripted.calls] == ["a"]
    events = read_journal_file(repo, run_id)
    # The specialist of a ended, and its step ended built. The step of b did not start, and the run did not end.
    (step_end,) = [e for e in events if e["event"] == "step-end"]
    assert (step_end["ticket"], step_end["state"]) == ("a", "built")
    assert [e["ticket"] for e in events if e["event"] == "step-start"] == ["a"]
    assert "run-end" not in event_names(events) and "gate-start" not in event_names(events)
    assert tail(events, 2) == ["pause-requested", "paused"]
    assert (events[-1]["ticket"], events[-1]["before"]) == ("b", "step")
    # The request is used up: the run directory holds no request any more.
    assert not (journal_path(repo, run_id).parent / "pause-request").exists()


def test_a_pause_during_a_specialist_in_assure_mode_stops_after_its_report_and_before_its_gates(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    pause_during_the_specialist_of(repo, capsys, scripted, "a")

    code, out, err = start(repo, capsys)

    assert code == RUN_PAUSED, err
    events = read_journal_file(repo, run_id_of(repo))
    names = event_names(events)
    assert "adapter-result" in names
    (validation,) = [e for e in events if e["event"] == "report-validation"]
    assert validation["valid"] is True
    assert "gate-start" not in names and "verify-start" not in names and "step-end" not in names
    assert tail(events, 2) == ["pause-requested", "paused"]
    assert (events[-1]["ticket"], events[-1]["before"]) == ("a", "gate")
    assert scripted.verifier_calls == []


def test_a_pause_during_the_chain_verification_stops_after_the_gates_and_before_the_verifier(tmp_path, capsys, scripted):
    script = tmp_path / "pause_gate.py"
    script.write_text(PAUSE_GATE)
    (tmp_path / "path.json").write_text(json.dumps(sys.path))
    gate = f"{sys.executable} {script} {tmp_path / 'path.json'}"
    repo = make_repo(tmp_path, ECONOMY_HEAD.replace('gates = ["ls *.txt"]', f"gates = [{json.dumps(gate)}]") + ticket("a") + ticket("b"))

    code, out, err = start(repo, capsys)

    assert code == RUN_PAUSED, err
    run_id = run_id_of(repo)
    events = read_journal_file(repo, run_id)
    names = event_names(events)
    (result,) = [e for e in events if e["event"] == "gate-result"]
    assert (result["phase"], result["green"]) == ("chain", True)
    assert names.index("gate-result") < names.index("pause-requested") < names.index("paused") == len(names) - 1
    assert events[-1]["before"] == "verifier"
    assert "verify-start" not in names and "run-end" not in names
    assert scripted.verifier_calls == []
    assert "a.txt" not in tree(repo, "run/demo")
    clear_calls(scripted)

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert scripted.calls == [] and len(scripted.verifier_calls) == 1
    events = read_journal_file(repo, run_id)
    resumed = events[event_names(events).index("resume"):]
    # The gate ran green on the tip before the pause, so the resume does not run it again.
    assert "gate-start" not in event_names(resumed) and "gate-skip" in event_names(resumed)
    assert (resumed[-1]["event"], resumed[-1]["result"], resumed[-1]["built"]) == ("run-end", "built", ["a", "b"])
    assert {"a.txt", "b.txt"} <= set(tree(repo, "run/demo"))


def test_status_shows_pausing_after_the_request_and_paused_after_the_stop(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY_HEAD + ticket("a") + ticket("b"))
    seen: list[str] = []
    pause_during_the_specialist_of(repo, capsys, scripted, "a", seen)

    code, out, err = start(repo, capsys)

    assert code == RUN_PAUSED, err
    run_id = run_id_of(repo)
    assert seen[0].startswith(f"run {run_id}: pausing ") and seen[1] == "pausing"
    lines = status_lines(repo, capsys, run_id)
    assert lines[0].startswith(f"run {run_id}: paused ")
    assert "ETA" not in lines[1]
    assert status_json(repo, capsys, run_id)["state"] == "paused"

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    assert status_lines(repo, capsys, run_id)[0].startswith(f"run {run_id}: built ")


def test_a_resume_after_a_pause_builds_the_rest_also_with_a_ticket_that_the_rules_of_a_plan_change_accept(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
    pause_during_the_specialist_of(repo, capsys, scripted, "a")
    code, out, err = start(repo, capsys)
    assert code == RUN_PAUSED, err
    run_id = run_id_of(repo)
    scripted.on_specialist = None
    clear_calls(scripted)
    # The coordinator adds ticket x before b, which has not started. So b is a later ticket, and needs a review.
    (repo / "workflow.toml").write_text(HEAD + ticket("a") + ticket("x", ("a",)) + ticket("b", ("a", "x")))

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1 and "--reviewed" in err and "'b'" in err
    assert scripted.calls == [] and scripted.verifier_calls == []

    code, out, err = resume_main(repo, capsys, run_id, "--reviewed", "b")

    assert (code, err) == (0, "")
    # The specialist of a ended before the pause, so the resume does not spawn it again: it runs the gates of a.
    assert [ticket_of(call) for call in scripted.calls] == ["x", "b"]
    assert [ticket_of(call) for call in scripted.verifier_calls] == ["a", "x", "b"]
    events = read_journal_file(repo, run_id)
    (change,) = [e for e in events if e["event"] == "plan-change"]
    assert (change["added"], change["reviewed"]) == (["x"], ["b"])
    assert (events[-1]["event"], events[-1]["result"], events[-1]["built"]) == ("run-end", "built", ["a", "x", "b"])
    assert {"a.txt", "x.txt", "b.txt"} <= set(tree(repo, "run/demo"))


def test_watch_exits_with_the_code_of_a_pause_at_the_paused_event(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY_HEAD + ticket("a") + ticket("b"))
    pause_during_the_specialist_of(repo, capsys, scripted, "a")
    code, out, err = start(repo, capsys)
    assert code == RUN_PAUSED, err
    run_id = run_id_of(repo)

    code = delegate.main(["watch", run_id, "--repo", str(repo), "--raw", "--poll-seconds", "0.01", "--max-minutes", "1"])

    lines = capsys.readouterr().out.splitlines()
    assert code == WATCH_PAUSED
    assert " paused " in lines[-2] and lines[-1].startswith("position ")


def test_a_pause_of_a_run_that_ended_is_refused_and_writes_no_request(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, out, err = start(repo, capsys)
    assert (code, err) == (0, "")
    run_id = run_id_of(repo)

    code, out, err = pause(repo, capsys, run_id)

    assert code == 1 and f"run {run_id!r} has ended" in err
    assert not (journal_path(repo, run_id).parent / "pause-request").exists()


def test_a_pause_of_a_run_whose_process_stopped_is_refused_and_names_the_resume(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    def interrupt(request):
        raise KeyboardInterrupt

    scripted.during_verifier = interrupt
    code, _, _ = start(repo, capsys)
    assert code == 130  # Ctrl-C stops the run process (#109)
    run_id = run_id_of(repo)

    code, out, err = pause(repo, capsys, run_id)

    assert code == 1 and "no live process" in err and f"delegate run --resume {run_id}" in err
    assert not (journal_path(repo, run_id).parent / "pause-request").exists()


def test_a_pause_of_a_run_that_is_paused_is_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY_HEAD + ticket("a") + ticket("b"))
    pause_during_the_specialist_of(repo, capsys, scripted, "a")
    code, out, err = start(repo, capsys)
    assert code == RUN_PAUSED, err
    run_id = run_id_of(repo)

    code, out, err = pause(repo, capsys, run_id)

    assert code == 1 and "is paused" in err
    assert not (journal_path(repo, run_id).parent / "pause-request").exists()


def test_a_pause_of_an_unknown_run_is_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    code, out, err = pause(repo, capsys, "20990101T000000Z-0000")

    assert code == 1 and "20990101T000000Z-0000" in err
    assert git(repo, "branch", "--list", "run/demo") == ""
