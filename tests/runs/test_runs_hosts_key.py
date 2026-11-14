"""The key `hosts` of the configuration file: the other hosts that `delegate runs` reads on this machine (#323).

`delegate runs` reads each host of `hosts` over ssh, as it reads the hosts of the run index (#318), with no `DELEGATE_INDEX`. The viewer keeps the newest
answer of each host in the store of last answers of its state root; when the host is unreachable later, its rows are the runs of that answer, dim, with
`heard <duration> ago`, and the row of the host says `unreachable` and gives the reason. The `ssh` of a case is a shim on `PATH` (`conftest.SshShim`).
The clock is `NOW`, 14:20; a case moves it on with the `clock` fixture.
"""

from __future__ import annotations

import json

import pytest

from .journals import NOW, runs
from .test_runs_config import config_file
from .test_runs_hosts import this_machine
from .test_runs_read_hosts import document, reason_of, run_of


@pytest.fixture(autouse=True)
def table_width(monkeypatch) -> None:
    monkeypatch.setenv("COLUMNS", "120")


@pytest.fixture
def far(isolated_config_home):
    """The configuration file of the viewer names one other host, `far`."""
    return config_file(isolated_config_home, 'hosts = ["far"]\n')


def test_with_hosts_far_and_no_delegate_index_runs_lists_the_runs_of_far_in_their_own_group(machine, far, ssh, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "1000")  # the header line holds the path of the configuration file; the table is as wide as at 120 columns
    this_machine(machine)
    ssh.serves("far", document("far", run_of("far", "docs-site", "a41e", "running", 3, 6)))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[1:] == [
        "runs  2 of 2 reachable  ████████░░░░░░░░ 50%  4/8 ended  ETA 16:05 · 1 with no ETA  32k new · 840k cache",
        "12:00–14:20  trend: tickets ended an hour, last 6h · all   ▇▇▇▇ steady",
        "",
        "host    repo       run   state    ended  ETA    slip  new  cache  trend   alert  do",
        "laptop                            1/2                 12k   340k",
        "│       alpha      aaaa  running  1/2    -            12k   340k       ▇",
        "",
        "far                               3/6    16:05        20k   500k",
        "│       docs-site  a41e  running  3/6    16:05        20k   500k    ▇▇▇",
    ]
    assert ssh.calls == [["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "far", "delegate", "runs", "--local", "--json"]]
    assert out.splitlines()[0].endswith(" · the index of this machine only · hosts far")


def test_far_unreachable_after_one_good_read_shows_its_runs_dim_with_heard_ago_and_the_row_unreachable(machine, far, ssh, capsys, clock):
    this_machine(machine)
    ssh.serves("far", document("far", run_of("far", "docs-site", "a41e", "running", 3, 6)))
    runs(capsys)
    ssh.exits("far", 255)
    ssh.says("far", "ssh: connect to host far port 22: Connection refused\n")
    clock.at = NOW.timestamp() + 12 * 60

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[1].startswith("runs  1 of 2 reachable")
    assert next(line for line in lines if line.startswith("far ")).split() == ["far", "unreachable", "heard", "12m", "ago", "check", "host"]
    assert reason_of(lines, "far") == "ssh cannot reach far: ssh: connect to host far port 22: Connection refused"
    assert next(line for line in lines if "a41e" in line).split() == ["│", "docs-site", "a41e", "running", "3/6"]
    assert len(ssh.calls) == 2


def test_a_host_of_hosts_with_no_last_answer_has_only_its_unreachable_row_and_the_reason(machine, far, ssh, capsys):
    this_machine(machine)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    at = lines.index(next(line for line in lines if line.startswith("far ")))
    assert lines[at].split() == ["far", "unreachable", "check", "host"]
    assert [line.split() for line in lines[at + 1:at + 3]] == [["│", "ssh", "cannot", "reach", "far"], []]


def test_a_remote_delegate_with_no_runs_command_is_unreachable_as_older_than_1_22_0(machine, far, ssh, capsys):
    this_machine(machine)
    ssh.says("far", "delegate: unknown subcommand 'runs'; expected one of: render, validate, bootstrap, run, status, watch\n")
    ssh.exits("far", 2)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert reason_of(out.splitlines(), "far") == "delegate on far is older than 1.22.0"


def test_local_starts_no_ssh_and_shows_no_host_of_hosts(machine, far, ssh, capsys):
    this_machine(machine)
    ssh.serves("far", document("far", run_of("far", "docs-site", "a41e", "running", 3, 6)))

    code, out, err = runs(capsys, "--local")

    assert (code, err) == (0, "")
    assert ssh.calls == []
    assert "far" not in out.splitlines()[1:] and "a41e" not in out


def test_a_host_that_the_index_names_and_hosts_names_is_read_once(machine, far, ssh, capsys, tmp_path, monkeypatch):
    from delegate.adapters.run_index import DirectoryRunIndex

    from .test_runs_hosts import record

    shared = tmp_path / "shared"
    monkeypatch.setenv("DELEGATE_INDEX", str(shared))
    record(DirectoryRunIndex(shared), "far", "docs-site", "a41e", "running", 1, 6, 47)
    this_machine(machine)
    ssh.serves("far", document("far", run_of("far", "docs-site", "a41e", "running", 3, 6)))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert len(ssh.calls) == 1
    assert [line.split()[0] for line in out.splitlines() if line.startswith("far ")] == ["far"]


def test_the_store_of_last_answers_is_one_file_for_each_host_in_the_state_root(machine, far, ssh, capsys):
    ssh.serves("far", document("far", run_of("far", "docs-site", "a41e", "running", 3, 6)))

    runs(capsys)

    assert sorted(path.name for path in (machine / "last-answers").iterdir()) == ["far.json"]


def test_a_corrupt_file_of_the_store_gives_no_last_known_run_and_no_error_and_the_next_good_read_replaces_it(machine, far, ssh, capsys):
    this_machine(machine)
    ssh.serves("far", document("far", run_of("far", "docs-site", "a41e", "running", 3, 6)))
    runs(capsys)
    stored = machine / "last-answers" / "far.json"
    stored.write_text("{this is not json\n")
    ssh.exits("far", 255)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert next(line for line in lines if line.startswith("far ")).split() == ["far", "unreachable", "check", "host"]
    assert "a41e" not in out
    (ssh.directory / "far.code").unlink()  # the host is reachable again
    runs(capsys)
    assert json.loads(stored.read_text())["document"]["host"] == "far"


def test_a_file_of_the_store_that_cannot_be_read_gives_no_last_known_run_and_no_error(machine, far, ssh, capsys):
    this_machine(machine)
    ssh.serves("far", document("far", run_of("far", "docs-site", "a41e", "running", 3, 6)))
    runs(capsys)
    stored = machine / "last-answers" / "far.json"
    stored.chmod(0)
    ssh.exits("far", 255)

    try:
        code, out, err = runs(capsys)
    finally:
        stored.chmod(0o644)

    assert (code, err) == (0, "")
    assert next(line for line in out.splitlines() if line.startswith("far ")).split() == ["far", "unreachable", "check", "host"]
    assert "a41e" not in out


def test_a_write_of_the_store_that_fails_keeps_the_older_file_and_the_read_still_shows_the_runs_of_the_host(machine, far, ssh, capsys):
    this_machine(machine)
    store = machine / "last-answers"
    store.write_text("a file in the place of the directory, so the store cannot write\n")
    ssh.serves("far", document("far", run_of("far", "docs-site", "a41e", "running", 3, 6)))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert next(line for line in out.splitlines() if "a41e" in line).split() == ["│", "docs-site", "a41e", "running", "3/6", "16:05", "20k", "500k", "▇▇▇"]
    assert store.read_text() == "a file in the place of the directory, so the store cannot write\n"
