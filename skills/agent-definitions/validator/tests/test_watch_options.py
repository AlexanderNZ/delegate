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
