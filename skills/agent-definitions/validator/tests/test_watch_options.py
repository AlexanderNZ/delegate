"""`delegate watch --until`, `--stall-minutes`, `--max-minutes` and `--from`: the early exits, each with its own exit code.

A case writes the journal of a run with the journal class of the engine and calls
`delegate.main(["watch", ...])`. A stall depends on the age of the files, so a
case sets the modification time of the journal and of an event stream with
`os.utime`, which is the real boundary of the clock for a file.
"""

import os
import time

import pytest

from agent_definitions import delegate

from .support import bare_repo, run_end, run_start, step_end, step_start, write_journal

POLL = ["--poll-seconds", "0.02"]


def watch(repo, capsys, *extra):
    code = delegate.main(["watch", "r1", "--repo", str(repo), *POLL, *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def verdict(ticket, value, findings=(), round_number=0):
    return "verdict", {
        "ticket": ticket, "round": round_number, "mode": "full", "verdict": value, "findings": list(findings),
        "unverified": [], "report": "/r.json",
    }


def printed_kinds(out: str) -> list[str]:
    return [line.split()[2] for line in out.splitlines() if line.split()[:1] and line.split()[0].isdigit()]


def test_by_default_watch_follows_the_run_past_a_verdict_to_its_end(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), verdict("a", "ACCEPT"), step_end("a"), run_end("built", ["a"]))

    code, out, _ = watch(repo, capsys)

    assert code == 0
    assert printed_kinds(out) == ["run-start", "step-start", "verdict", "step-end", "run-end"]


def test_until_verdict_exits_4_at_the_first_verdict_and_names_the_ticket_the_verdict_and_the_findings(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(
        repo, "r1", run_start("r1", "a"), step_start("a"),
        verdict("a", "REJECT", ["feature.txt is not a CSV file.", "no test"]), step_end("a"), run_end("built", ["a"]),
    )

    code, out, err = watch(repo, capsys, "--until", "verdict")

    assert (code, err) == (4, "")
    assert printed_kinds(out) == ["run-start", "step-start", "verdict"]
    assert "watch: verdict: ticket a round 0: REJECT: feature.txt is not a CSV file.; no test" in out.splitlines()


def test_until_accepts_only_verdict(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"))

    with pytest.raises(SystemExit) as stop:
        watch(repo, capsys, "--until", "end")

    assert stop.value.code == 2


def age(path, minutes):
    """Set the modification time of the file to `minutes` minutes ago."""
    then = time.time() - minutes * 60
    os.utime(path, (then, then))


def adapter_result(ticket, stream):
    return "adapter-result", {"ticket": ticket, "exit_status": 0, "end_state": "finished", "session_id": None, "event_stream": str(stream)}


def test_stall_minutes_exits_5_when_the_journal_has_not_changed_that_long_and_names_the_last_event(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))
    age(path, 10)

    code, out, err = watch(repo, capsys, "--stall-minutes", "5")

    assert (code, err) == (5, "")
    last = [line for line in out.splitlines() if line.startswith("watch: stall")]
    assert len(last) == 1
    assert last[0].startswith("watch: stall: the journal and the event streams have not changed for 5 minutes; last event: 2 ")
    assert last[0].endswith(" step-start ticket=a stack=python branch=run/demo-a worktree=/w/a base_commit=" + "0" * 40 + " agent=python-specialist tier=strong model=m")


def test_a_journal_that_changed_inside_the_window_is_not_a_stall(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"))
    age(path, 2)

    code, out, _ = watch(repo, capsys, "--stall-minutes", "5", "--max-minutes", "0.01")

    assert code == 6
    assert "watch: stall" not in out


def test_an_event_stream_that_changed_inside_the_window_keeps_a_run_with_an_old_journal_from_stalling(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    stream = tmp_path / "specialist.stream.jsonl"
    stream.write_text('{"type":"assistant"}\n')
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), adapter_result("a", stream))
    age(path, 10)

    code, out, _ = watch(repo, capsys, "--stall-minutes", "5", "--max-minutes", "0.01")

    assert code == 6
    assert "watch: stall" not in out


def test_a_run_that_ended_is_not_a_stall_whatever_the_age_of_its_journal(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    path = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), step_end("a"), run_end("built", ["a"]))
    age(path, 600)

    code, _, _ = watch(repo, capsys, "--stall-minutes", "5")

    assert code == 0


@pytest.mark.parametrize("option", ["--stall-minutes", "--max-minutes"])
@pytest.mark.parametrize("value", ["0", "-1"])
def test_the_minute_options_refuse_a_value_that_is_not_greater_than_0(tmp_path, capsys, option, value):
    repo = bare_repo(tmp_path)
    write_journal(repo, "r1", run_start("r1", "a"))

    with pytest.raises(SystemExit) as stop:
        watch(repo, capsys, option, value)

    assert stop.value.code == 2
    assert option in capsys.readouterr().err
