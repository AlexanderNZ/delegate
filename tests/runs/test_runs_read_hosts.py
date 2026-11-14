"""`delegate runs` reads the runs of the other hosts of the run index over ssh (#318).

For each other host of the index, the command runs `ssh -o BatchMode=yes -o ConnectTimeout=5 <host> delegate runs --local --json`. A host that gives a
document of `format` 1 shows its runs as live runs, in the sums. A host whose read fails, times out, or gives a document of a `format` that this version does not
know is `unreachable`, with its last-known runs from the index, as in #317, and the row under its row gives the reason (#323). The first line of the
output is the header line of the settings (#323); a case that does not test it reads the lines after it. The `ssh` of a case is a shim on `PATH` (`conftest.SshShim`). The index is a real
directory of the directory backend. The clock is `NOW`, 14:20.
"""

from __future__ import annotations

import time

import pytest

from delegate.run import fleet

from ..support import fake_clock, frames
from .journals import NOW, clock_time, runs
from .test_runs_hosts import index, record, this_machine, two_other_hosts
from .test_runs_watch import ctrl_c, watching


@pytest.fixture(autouse=True)
def table_width(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "120")


def document(host: str, *found: dict[str, object], format: int | None = 1) -> dict[str, object]:
    """The document of `delegate runs --local --json` of `host`: its fields, and the runs `found`. With `format` None, the document has no `format`."""
    fields = {"format": format, "host": host, "time": NOW.isoformat(), "window_seconds": 21600, "runs": list(found)}
    return {key: value for key, value in fields.items() if key != "format" or format is not None}


def reason_of(lines: list[str], host: str) -> str:
    """The text of the row under the row of `host`, after its rail: the reason that the command could not read the host (#323)."""
    row = next(number for number, line in enumerate(lines) if line.startswith(f"{host} "))
    return lines[row + 1].removeprefix("│").strip()


def run_of(
    host: str, repo: str, short: str, state: str, ended: int, total: int, eta: str | None = "16:05", tokens: tuple[int, int] = (20_000, 500_000),
    alert: str | None = None, do: str | None = None, trend: tuple[int, ...] = (0, 0, 1, 1, 1, 0),
) -> dict[str, object]:
    """One run of the document of a host. It started at 12:00, and it has not ended."""
    started = clock_time(12, 0)
    return {
        "run_id": f"{started.strftime('%Y%m%dT%H%M%SZ')}-{short}", "repo": repo, "host": host, "state": state, "started": started.isoformat(), "ended": None,
        "tickets_ended": ended, "tickets_total": total, "eta": clock_time(*map(int, eta.split(":"))).isoformat() if eta else None,
        "tokens_new": tokens[0], "tokens_cache": tokens[1], "alert": alert, "do": do, "trend": list(trend), "slip_seconds": None,
    }


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
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6, alert="⚠ idle 6m40s", do="watch")))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert "⚠ 1 in trouble" in lines[2]
    assert next(line for line in lines if "a41e" in line).split()[-5:] == ["▇▇▇", "⚠", "idle", "6m40s", "watch"]


def test_the_legend_line_for_another_host_shows_when_a_host_other_than_this_machine_has_a_do_word(machine, index, ssh, capsys):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6, alert="⚠ idle 6m40s", do="watch")))

    _, out, _ = runs(capsys)

    assert out.splitlines()[-2:] == ["", "watch  delegate watch <run>; on another host, put ssh <host> in front of each command"]


def test_the_legend_has_no_line_for_another_host_when_no_other_host_has_a_do_word(machine, index, ssh, capsys):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))

    _, out, _ = runs(capsys)

    assert "on another host" not in out


def test_a_host_that_waits_past_the_limit_is_unreachable_with_its_last_known_runs_and_the_command_ends_in_time(machine, index, ssh, capsys, monkeypatch):
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "running", 1, 6, 47)
    ssh.serves("build-host", document("build-host", run_of("build-host", "docs-site", "a41e", "running", 3, 6)))
    ssh.hangs("build-host")
    monkeypatch.setattr(fleet, "HOST_READ_SECONDS", 1.0)
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
    fake_clock().on_sleep = lambda seconds: ctrl_c()

    _, out, _ = watching(monkeypatch, capsys, columns=120)

    (frame,) = frames(out)
    assert frame[1].startswith("runs  2 of 2 reachable")
