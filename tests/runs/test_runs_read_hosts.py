"""`delegate runs` reads the runs of the other hosts of the run index over ssh (#318).

For each other host of the index, the command runs `ssh -o BatchMode=yes -o ConnectTimeout=5 <host> delegate runs --local --json`. A host that gives a
document of `format` 1 shows its runs as live runs, in the sums. A host whose read fails, times out, or gives a document of a `format` that this version does not
know is `unreachable`, with its last-known runs from the index, as in #317, and the row under its row gives the reason (#323). The first line of the
output is the header line of the settings (#323); a case that does not test it reads the lines after it. The `ssh` of a case is a shim on `PATH` (`conftest.SshShim`). The index is a real
directory of the directory backend. The clock is `NOW`, 14:20.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time

import pytest

from delegate.run import fleet_hosts

from ..support import Terminal, fake_clock, frames
from .journals import clock_time, runs
from ..support_runs import every_kind, failed_start, index, record, this_machine, two_other_hosts, ctrl_c, watching, document, reason_of, run_of


@pytest.fixture(autouse=True)
def table_width(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "120")


def group_is_gone(group: int, seconds: float = 1.0) -> bool:
    """True when no process of the process group `group` is alive. A process that a signal ended can stay a zombie until its parent, or init, collects it,
    so the check waits up to `seconds` for that. A group that holds only zombies gives PermissionError on macOS."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            os.killpg(group, 0)
        except (ProcessLookupError, PermissionError):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.02)


def row_of(lines: list[str], start: str) -> list[str]:
    """The cells of the first line that starts with `start`, or that holds it as a cell."""
    return next(line for line in lines if line.startswith(start) or f" {start} " in line).split()


def test_a_host_that_gives_its_document_shows_its_runs_as_live_runs_and_they_are_in_the_sums(machine, index, ssh, capsys):
    """This machine ends 1 of 2 tickets. `build-host` gives a run that ends 3 of 6: the sums are 4 of 8, 2 of 2 hosts are reachable, and the tokens add up.

    The index holds an older record of the same run with other numbers (running 1 of 6); the document is newer, so the row has the numbers of the document.
    """
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[1:] == [
        "runs  2 of 2 reachable  ████████░░░░░░░░ 50%  4/8 ended  ETA 16:05 · 1 with no ETA  32k new · 840k cache",
        "12:00–14:20  trend: tickets ended an hour, last 6h · all   ▇▇▇▇ steady",
        "",
        "host        repo       run   state    ended  ETA    slip  new  cache  trend   alert  do",
        "laptop                                1/2                 12k   340k",
        "│           alpha      aaaa  running  1/2    -            12k   340k       ▇",
        "",
        "build-host                            3/6    16:05        20k   500k",
        "│           docs-site  a41e  running  3/6    16:05        20k   500k    ▇▇▇",
    ]
    assert ssh.calls == [["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "build-host", "delegate", "runs", "--local", "--json"]]


def test_the_document_of_a_host_that_gives_an_ended_run_shows_it_in_the_group_ended_with_its_host_and_out_of_the_sums(machine, index, ssh, capsys):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ended = run_of("build-host", "docs-site", "e2b1", "built", 6, 6, eta=None, do="merge")
    ended["ended"] = clock_time(13, 10).isoformat()
    ssh.serves("build-host", document("build-host", ended))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[1].startswith("runs  1 of 1 reachable") and "1/2 ended" in lines[1]
    assert next(line for line in lines if "e2b1" in line).split()[:5] == ["build-host", "docs-site", "e2b1", "built", "6/6"]
    assert lines[-2:] == ["", "merge  git merge --ff-only <run branch>; on another host, put ssh <host> in front of each command"]


def test_the_alert_of_a_run_of_another_host_shows_and_counts_in_trouble(machine, index, ssh, capsys):
    """The document has an alert `⚠ idle 6m40s` and the word `watch`: the row shows both, the second fleet line counts one run in trouble."""
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6, alert="⚠ idle 6m40s", idle_seconds=400, do="watch")))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert "⚠ 1 in trouble" in lines[2]
    assert next(line for line in lines if "a41e" in line).split()[-5:] == ["▇▇▇", "⚠", "idle", "6m40s", "watch"]


def test_the_legend_line_for_another_host_shows_when_a_host_other_than_this_machine_has_a_do_word(machine, index, ssh, capsys):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6, alert="⚠ idle 6m40s", idle_seconds=400, do="watch")))

    _, out, _ = runs(capsys)

    assert out.splitlines()[-2:] == ["", "watch  delegate watch <run>; on another host, put ssh <host> in front of each command"]


def test_the_legend_has_no_line_for_another_host_when_no_other_host_has_a_do_word(machine, index, ssh, capsys):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))

    _, out, _ = runs(capsys)

    assert next(line for line in out.splitlines() if "a41e" in line).split()[1:3] == ["docs-site", "a41e"]
    assert "on another host" not in out


def test_a_host_that_waits_past_the_limit_is_unreachable_with_its_last_known_runs_and_the_command_ends_in_time(machine, index, ssh, capsys, monkeypatch):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))
    ssh.hangs("build-host")
    monkeypatch.setattr(fleet_hosts, "HOST_READ_SECONDS", 1.0)
    started = time.monotonic()

    code, out, err = runs(capsys)

    assert time.monotonic() - started < 10  # the shim waits 30 seconds
    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[1].startswith("runs  1 of 2 reachable")
    assert next(line for line in lines if line.startswith("build-host")).split() == ["build-host", "unreachable", "heard", "47m", "ago", "check", "host"]
    assert "no answer from build-host in 1 seconds" in lines[lines.index(next(line for line in lines if line.startswith("build-host"))) + 1]
    assert next(line for line in lines if "a41e" in line).split() == ["│", "docs-site", "a41e", "running", "1/6"]


def test_a_host_that_gives_a_document_with_a_later_format_is_unreachable_with_its_last_known_runs_and_the_reason(machine, index, ssh, capsys):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6), format=2))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[1].startswith("runs  1 of 2 reachable")
    assert next(line for line in lines if line.startswith("build-host")).split() == ["build-host", "unreachable", "heard", "47m", "ago", "check", "host"]
    assert reason_of(lines, "build-host") == "delegate on build-host is newer than this one (format 2); update delegate on this machine"
    assert next(line for line in lines if "a41e" in line).split() == ["│", "docs-site", "a41e", "running", "1/6"]


@pytest.mark.parametrize("found", [0, None])
def test_a_host_that_gives_a_document_with_an_earlier_format_or_none_is_unreachable_as_older_than_1_22_0(machine, index, ssh, capsys, found):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    answer = document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6), format=found)
    ssh.serves("build-host", answer)

    _, out, _ = runs(capsys)

    assert reason_of(out.splitlines(), "build-host") == "delegate on build-host is older than 1.22.0"


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"format": 1}', '{"format": 1, "runs": [{"run_id": "x"}]}', '{"format": true, "runs": []}'])
def test_a_host_that_prints_a_document_that_this_version_cannot_read_is_unreachable(machine, index, ssh, capsys, text):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.prints("build-host", text)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert next(line for line in out.splitlines() if line.startswith("build-host")).split()[1] == "unreachable"


@pytest.mark.parametrize("status", [255, 1, 127])
def test_a_host_whose_ssh_exits_with_a_failure_is_unreachable_also_when_it_printed_a_document(machine, index, ssh, capsys, status):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))
    ssh.exits("build-host", status)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[1].startswith("runs  1 of 2 reachable")
    assert next(line for line in lines if line.startswith("build-host")).split() == ["build-host", "unreachable", "heard", "47m", "ago", "check", "host"]


def test_a_host_that_is_not_served_is_unreachable_as_ssh_that_cannot_connect_is(machine, index, ssh, capsys):
    this_machine(machine)
    two_other_hosts(index)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[1].startswith("runs  1 of 3 reachable")
    assert len(ssh.calls) == 2


@pytest.mark.parametrize(("status", "said", "reason"), [
    (255, "debug1: some line\nfar@build-host: Permission denied (publickey).\n", "ssh cannot reach build-host: far@build-host: Permission denied (publickey)."),
    (255, "", "ssh cannot reach build-host"),
    (127, "zsh:1: command not found: delegate\n", "build-host has no delegate command on its PATH: zsh:1: command not found: delegate"),
    (2, "delegate: unknown subcommand 'runs'; expected one of: render, validate\n", "delegate on build-host is older than 1.22.0"),
    (2, "delegate runs: config /home/far/.config/delegate/config.toml: unknown key 'host'\n",
     "delegate runs on build-host exited with status 2: delegate runs: config /home/far/.config/delegate/config.toml: unknown key 'host'"),
])
def test_the_row_under_an_unreachable_host_gives_the_reason_from_the_exit_status_and_the_last_line_of_stderr(
    machine, index, ssh, capsys, monkeypatch, status, said, reason,
):
    """#323: the row of an unreachable host says why. The reason of a remote `delegate` with no `runs` command is `delegate on <host> is older than 1.22.0`."""
    monkeypatch.setenv("COLUMNS", "1000")  # a line longer than the terminal is cut, and the longest reason here is longer than 120 columns
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.says("build-host", said)
    ssh.exits("build-host", status)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert reason_of(out.splitlines(), "build-host") == reason


def test_one_host_that_is_read_and_one_that_is_not_show_in_the_same_view(machine, index, ssh, capsys):
    this_machine(machine)
    two_other_hosts(index)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[1].startswith("runs  2 of 3 reachable")
    assert next(line for line in lines if line.startswith("ci-runner")).split() == ["ci-runner", "unreachable", "heard", "3h05m", "ago", "check", "host"]
    assert next(line for line in lines if line.startswith("build-host")).split()[1:3] == ["3/6", "16:05"]


def test_the_hosts_are_read_at_the_same_time(machine, index, ssh, capsys):
    """Each of the two shims waits 1 second before it prints. Read one after the other, the first would end before the second starts."""
    this_machine(machine)
    two_other_hosts(index)
    for name in ("build-host", "ci-runner"):
        ssh.serves(name, document(name))
        ssh.delays(name, 1)

    code, _, err = runs(capsys)

    assert (code, err) == (0, "")
    assert [entry.split()[0] for entry in ssh.order] == ["start", "start", "end", "end"]


def test_local_and_json_start_no_ssh(machine, index, ssh, capsys):
    this_machine(machine)
    two_other_hosts(index)

    runs(capsys, "--local")
    runs(capsys, "--json")

    assert ssh.calls == []


def test_no_index_starts_no_ssh(machine, ssh, capsys):
    this_machine(machine)

    runs(capsys)

    assert ssh.calls == []


def test_watch_reads_the_other_hosts_at_most_each_30_seconds(machine, index, ssh, monkeypatch, capsys):
    """The view redraws each second. Redraws 1 to 30 (seconds 0 to 29) read the host once; the 31st redraw (second 30) reads it again."""
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))
    monkeypatch.setattr(fleet_hosts, "HOST_WAIT_SECONDS", 10.0)  # each read is in the frame that starts it, also on a busy machine (#328)
    waits: list[float] = []
    seen: list[int] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        seen.append(len(ssh.calls))
        if len(waits) == 31:
            ctrl_c()

    fake_clock().on_sleep = sleep

    watching(monkeypatch, capsys)

    assert seen[:30] == [1] * 30
    assert seen[30] == 2


def test_watch_shows_a_host_that_was_read_in_the_frame(machine, index, ssh, monkeypatch, capsys):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))
    monkeypatch.setattr(fleet_hosts, "HOST_WAIT_SECONDS", 10.0)  # the read is in the first frame, also on a busy machine (#328)
    fake_clock().on_sleep = lambda seconds: ctrl_c()

    _, out, _ = watching(monkeypatch, capsys, columns=120)

    (frame,) = frames(out)
    assert frame[1].startswith("runs  2 of 2 reachable")


def test_a_byte_that_is_not_utf_8_on_the_stderr_of_ssh_gives_the_reason_with_a_replacement_character(machine, index, ssh, capsys, monkeypatch):
    """#328: a banner of `ssh` with the byte 0xff made the reason a decode error of Python, not `ssh cannot reach <host>: ...`."""
    monkeypatch.setenv("COLUMNS", "1000")
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.says("build-host", b"\xff banner of build-host\n")
    ssh.exits("build-host", 255)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert reason_of(out.splitlines(), "build-host") == "ssh cannot reach build-host: \ufffd banner of build-host"


def test_watch_draws_the_first_frame_at_once_with_the_last_answer_of_a_host_whose_read_hangs(machine, index, ssh, capsys, monkeypatch, clock):
    """#328: the view waited for the slowest host before it drew. The host answered 3 of 6 two minutes ago, and its index record says 1 of 6.

    Now its read hangs. The first frame comes in less than one second, with the last answer and its age. Ctrl-C after the frame ends the `ssh` of the read.
    """
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))
    runs(capsys)  # the store of last answers keeps the answer
    ssh.hangs("build-host")
    clock.at += 120
    drawn: list[float] = []

    def sleep(seconds: float) -> None:
        drawn.append(time.monotonic())
        ctrl_c()

    fake_clock().on_sleep = sleep
    started = time.monotonic()

    code, out, err = watching(monkeypatch, capsys, columns=120)

    assert drawn[0] - started < 1  # the shim waits 30 seconds, and the time limit of a read is 10 seconds
    (frame,) = frames(out)
    assert row_of(frame, "build-host") == ["build-host", "unreachable", "heard", "2m", "ago", "check", "host"]
    assert reason_of(frame, "build-host") == "no answer from build-host yet"
    assert row_of(frame, "a41e") == ["│", "docs-site", "a41e", "running", "3/6"]
    assert (code, err) == (130, "delegate: interrupted\n")
    assert len(ssh.pids) == 2 and group_is_gone(ssh.pids[1])


def test_watch_draws_the_last_answer_until_the_new_answer_comes_and_then_the_new_answer(machine, index, ssh, capsys, monkeypatch):
    """The host answers after 1.5 seconds. The first frames have its last-known run from the index (1 of 6); a later frame has its answer (3 of 6), live."""
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))
    ssh.delays("build-host", 1.5)
    terminal = Terminal()
    deadline = time.monotonic() + 10

    def sleep(seconds: float) -> None:
        if "2 of 2 reachable" in terminal.getvalue() or time.monotonic() > deadline:
            ctrl_c()
        time.sleep(0.1)  # a real wait: the read of the host goes on in the background

    fake_clock().on_sleep = sleep

    watching(monkeypatch, capsys, columns=120, out=terminal)

    shown = frames(terminal.getvalue())
    assert row_of(shown[0], "build-host")[:2] == ["build-host", "unreachable"]
    assert row_of(shown[0], "a41e") == ["│", "docs-site", "a41e", "running", "1/6"]
    assert shown[-1][1].startswith("runs  2 of 2 reachable")
    assert row_of(shown[-1], "a41e")[:6] == ["│", "docs-site", "a41e", "running", "3/6", "16:05"]
    assert len(ssh.calls) == 1


# Run `delegate runs` as a terminal runs it: Ctrl-C raises KeyboardInterrupt, also when the process that starts it ignores SIGINT.
ON_A_TERMINAL = (
    "import signal, sys\n"
    "signal.signal(signal.SIGINT, signal.default_int_handler)\n"
    "from delegate.cli.delegate import main\n"
    "sys.exit(main(sys.argv[1:]))\n"
)


def test_sigint_during_a_read_that_hangs_exits_130_in_less_than_one_second_and_no_ssh_of_the_read_stays_alive(machine, index, ssh):
    """#328: the main thread waited for each read until its time limit: `delegate runs` exited 130 nine seconds after SIGINT."""
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.hangs("build-host")
    process = subprocess.Popen(
        [sys.executable, "-c", ON_A_TERMINAL, "runs"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 20
    while not ssh.pids and time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.02)
    assert ssh.pids, "the read of build-host did not start"
    time.sleep(0.2)  # the shim waits in `sleep 30`

    sent = time.monotonic()
    process.send_signal(signal.SIGINT)
    _, err = process.communicate(timeout=30)
    took = time.monotonic() - sent

    assert (process.returncode, err.decode()) == (130, "delegate: interrupted\n")
    assert took < 1
    assert group_is_gone(ssh.pids[0])


def rest_of(out: str, short: str) -> list[str]:
    """The cells of the row of a run after its `run` cell: the same for the run of this machine and for the run of another host."""
    line = next(line for line in out.splitlines() if re.search(rf"\s{short}\s", line))
    return line.split(None, 3)[3].split()


def test_the_reader_shows_the_same_alert_and_do_word_from_the_fields_as_the_host_that_wrote_them(machine, index, ssh, tmp_path, monkeypatch, capsys):
    """A host has an idle agent, a fix-up round, a run that is paused, a run that failed at start, and a run that works with nothing wrong. Its document is the one that `--local --json` writes."""
    every_kind(machine)
    failed_start(machine)
    _, written, _ = runs(capsys)
    _, text, _ = runs(capsys, "--local", "--json")
    found = json.loads(text)
    found["host"] = "build-host"
    for run in found["runs"]:
        run["host"] = "build-host"
        run.pop("alert")  # the reader takes the values from the fields: it does not need the text
    elsewhere = tmp_path / "other-root"
    elsewhere.mkdir()
    monkeypatch.setenv("DELEGATE_STATE_ROOT", str(elsewhere))
    record(index, "build-host", "alpha", "iiii", "running", 0, 2, 47)
    ssh.serves("build-host", found)

    code, read, err = runs(capsys)

    shorts = ("iiii", "ffff", "pppp", "llll", "bbbb", "rrrr", "ssss")
    assert (code, err) == (0, "")
    for short in shorts:
        assert rest_of(read, short) == rest_of(written, short), short
    assert "⚠ idle 10m00s" in read and "fix-up 1/2" in read and "failed at start" in read


def test_a_document_whose_run_has_no_alert_fields_is_a_document_that_this_version_cannot_read(machine, index, ssh, capsys):
    """The reader takes the alert from the fields and never from the text, so a run with only the text of the alert is not read."""
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    old = run_of("build-host", "docs-site", "a41e", "running", 3, 6, alert="⚠ idle 6m40s", do="watch")
    for name in ("idle_seconds", "fixup_round", "fixup_limit", "failed_start"):
        del old[name]
    ssh.serves("build-host", document("build-host", old))

    code, out, err = runs(capsys)

    lines = out.splitlines()
    assert (code, err) == (0, "")
    assert reason_of(lines, "build-host") == "build-host gave a document that this version of delegate cannot read"


@pytest.mark.parametrize(("text", "reason"), [
    ("", "build-host gave no JSON document"),
    ("not json", "build-host gave no JSON document"),
    ("[]", "build-host gave a document that is not an object"),
    ('{"format": 1}', "build-host gave a document that this version of delegate cannot read"),
    ('{"format": 1, "runs": [{"run_id": "x"}]}', "build-host gave a document that this version of delegate cannot read"),
    ('{"format": true, "runs": []}', "delegate on build-host is older than 1.22.0"),
])
def test_a_document_that_this_version_cannot_read_gives_the_exact_reason_of_its_kind_under_the_row_of_the_host(machine, index, ssh, capsys, text, reason):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.prints("build-host", text)

    _, out, _ = runs(capsys)

    assert reason_of(out.splitlines(), "build-host") == reason


def test_an_ssh_that_cannot_start_gives_the_reason_ssh_cannot_start_with_the_error_of_the_system(machine, index, ssh, capsys, tmp_path, monkeypatch):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    nowhere = tmp_path / "no-command-here"
    nowhere.mkdir()
    monkeypatch.setenv("PATH", str(nowhere))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert reason_of(out.splitlines(), "build-host") == "ssh cannot start: No such file or directory"
