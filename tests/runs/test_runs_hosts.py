"""`delegate runs` with the run index: the runs of the other hosts (#317).

With `DELEGATE_INDEX` set, the view has one group for each other host of the index. The `ssh` of each case (`conftest.SshShim`) reaches no host
that the case does not serve, so each such host is `unreachable`: its row says so, the row under it gives the reason (#323), its runs are the
last-known values of their records, `alert` is `heard <duration> ago` and `do` is `check host`. The first line of the output is the header line of
the settings (#323); a case that does not test it reads the lines after it. The expected lines come from the frames W8 and E15 of the design
(`design/docs/components.md`, C24–C27) and the keys `W8.*` of the copy deck, and the sums of the numbers of the case.
The index is a real directory of the directory backend. The clock is `NOW`, 14:20.
"""

from __future__ import annotations

import json

import pytest

from delegate.adapters.run_index import DirectoryRunIndex
from delegate.cli import delegate

from ..support import HEAD, make_repo, read_journal, run_main, ticket
from .journals import clock_time, run_end, runs
from ..support_runs import index, record, this_machine, two_other_hosts


@pytest.fixture(autouse=True)
def table_width(monkeypatch) -> None:
    """120 columns: the table fits with each column, and the bar and the legend show."""
    monkeypatch.setenv("COLUMNS", "120")


def test_an_index_with_the_records_of_two_other_hosts_shows_two_more_groups_that_are_unreachable_with_the_last_known_values_and_heard(machine, index, capsys):
    this_machine(machine)
    two_other_hosts(index)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[1:] == [
        "runs  1 of 3 reachable  ████████░░░░░░░░ 50%  1/2 ended  ETA - · 1 with no ETA  12k new · 340k cache",
        "13:40–14:20  trend: tickets ended an hour, last 6h · all      ▇ speeding up",
        "",
        "host        repo         run   state        ended  ETA  slip  new  cache  trend   alert            do",
        "laptop                                      1/2               12k   340k",
        "│           alpha        aaaa  running      1/2    -          12k   340k       ▇",
        "",
        "build-host                     unreachable                                        heard 47m ago    check host",
        "│                              ssh cannot reach build-host",
        "│           docs-site    a41e  running      3/6",
        "",
        "ci-runner                      unreachable                                        heard 3h05m ago  check host",
        "│                              ssh cannot reach ci-runner",
        "│           api-service  5d02  running      3/10",
        "",
        "check host  ssh <host>; on another host, put ssh <host> in front of each command",
    ]


def test_the_legend_has_check_host_with_its_command_and_the_line_for_the_other_hosts(machine, index, capsys):
    this_machine(machine)
    two_other_hosts(index)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[-2:] == [
        "",
        "check host  ssh <host>; on another host, put ssh <host> in front of each command",
    ]


def test_the_fleet_sums_leave_out_the_unreachable_runs_and_count_them_in_the_total(machine, index, capsys):
    """This machine ends 1 of 2 tickets. The runs of the other hosts end 3 of 6, 3 of 10, and a stopped run ends 4 of 8: with them the sum would be 11/26, 42%.

    The stopped run is not in trouble and the early start (09:00) is not the start of the fleet, as the runs are out of the sums.
    """
    this_machine(machine)
    two_other_hosts(index)
    record(index, "ci-runner", "web-app", "77b0", "stopped", 4, 8, 185, started=clock_time(8, 0))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[1:3] == [
        "runs  1 of 4 reachable  ████████░░░░░░░░ 50%  1/2 ended  ETA - · 1 with no ETA  12k new · 340k cache",
        "13:40–14:20  trend: tickets ended an hour, last 6h · all      ▇ speeding up",
    ]


def test_the_row_of_an_unreachable_host_has_a_name_a_state_an_alert_a_command_and_no_totals(machine, index, capsys):
    this_machine(machine)
    two_other_hosts(index)

    _, out, _ = runs(capsys)

    host_row = next(line for line in out.splitlines() if line.startswith("build-host"))
    assert host_row.split() == ["build-host", "unreachable", "heard", "47m", "ago", "check", "host"]


def test_a_record_of_a_run_that_ended_before_the_window_is_not_listed_and_one_in_the_window_is(machine, index, capsys):
    """The window is 6 hours. A run that ended 7 hours ago (heard 420 minutes ago) is out. A run that ended 2 hours ago is under its host with its state."""
    this_machine(machine)
    record(index, "build-host", "docs-site", "a41e", "built", 6, 6, 420, started=clock_time(5, 0, days_back=1))
    record(index, "build-host", "web-app", "e2b1", "failed", 3, 5, 120, started=clock_time(9, 0))

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert [line.split() for line in lines if "docs-site" in line] == []
    assert [line.split() for line in lines if "web-app" in line] == [["│", "web-app", "e2b1", "failed", "3/5"]]
    assert lines[1] == "runs  1 of 1 reachable  ████████░░░░░░░░ 50%  1/2 ended  ETA - · 1 with no ETA  12k new · 340k cache"


def test_the_records_of_this_machine_in_the_index_are_not_used_and_its_runs_come_from_its_journals(machine, index, capsys):
    """The index holds a record of a run of `laptop` that has no journal, and a record of the journal run with other numbers. Neither shows."""
    this_machine(machine)
    record(index, "laptop", "alpha", "aaaa", "running", 2, 2, 1, started=clock_time(13, 40))
    record(index, "laptop", "ghost", "9999", "running", 5, 9, 1)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert [line.split() for line in out.splitlines() if line.startswith("│")] == [["│", "alpha", "aaaa", "running", "1/2", "-", "12k", "340k", "▇"]]
    assert "ghost" not in out and "unreachable" not in out


def test_local_limits_the_view_to_this_machine_and_reads_no_other_host(machine, index, capsys, monkeypatch):
    this_machine(machine)
    monkeypatch.setenv("COLUMNS", "1000")  # the header line holds the path of the index
    two_other_hosts(index)

    code, out, err = runs(capsys, "--local")

    assert (code, err) == (0, "")
    assert out.splitlines()[0].endswith(" · --local: no other host is read")
    assert out.splitlines()[1:] == [
        "runs  1 of 1 reachable  ████████░░░░░░░░ 50%  1/2 ended  ETA - · 1 with no ETA  12k new · 340k cache",
        "13:40–14:20  trend: tickets ended an hour, last 6h · all      ▇ speeding up",
        "",
        "host    repo   run   state    ended  ETA  slip  new  cache  trend   alert  do",
        "laptop                        1/2               12k   340k",
        "│       alpha  aaaa  running  1/2    -          12k   340k       ▇",
    ]


def test_json_describes_this_machine_only_and_is_the_same_with_and_without_the_index(machine, index, capsys, monkeypatch):
    this_machine(machine)
    two_other_hosts(index)

    code, with_index, err = runs(capsys, "--json")
    monkeypatch.delenv("DELEGATE_INDEX")
    _, without_index, _ = runs(capsys, "--json")

    assert (code, err) == (0, "")
    assert json.loads(with_index) == json.loads(without_index)
    assert [run["host"] for run in json.loads(with_index)["runs"]] == ["laptop"]


def test_the_header_line_names_a_shared_index_from_delegate_index_and_the_index_of_this_machine_only_without_it(machine, index, capsys, monkeypatch):
    """#323: the header line names the index: the shared directory, or `the index of this machine only` when no setting names one."""
    this_machine(machine)
    monkeypatch.setenv("COLUMNS", "1000")

    _, with_index, _ = runs(capsys)
    monkeypatch.delenv("DELEGATE_INDEX")
    _, without_index, _ = runs(capsys)

    assert with_index.splitlines()[0] == f"no configuration file · index {index.directory} · no other host"
    assert without_index.splitlines()[0] == "no configuration file · the index of this machine only · no other host"


def test_an_empty_view_with_an_index_counts_the_hosts_of_the_index_and_shows_the_row_of_each_host_that_it_cannot_read(machine, index, capsys):
    """Two other hosts in the index, each with a run that ended 8 hours ago, and no host answers: no run is listed, there are 3 hosts,
    and each host that the command could not read has its row and its reason (#323), with no run under it."""
    record(index, "build-host", "docs-site", "a41e", "built", 6, 6, 480)
    record(index, "ci-runner", "api-service", "5d02", "failed", 3, 10, 500)

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[1:4] == ["runs  0 of 3 hosts have an active run", "no run ended in the last 6h", ""]
    assert [line.split() for line in lines[5:]] == [
        ["build-host", "unreachable", "heard", "8h00m", "ago", "check", "host"], ["│", "ssh", "cannot", "reach", "build-host"], [],
        ["ci-runner", "unreachable", "heard", "8h20m", "ago", "check", "host"], ["│", "ssh", "cannot", "reach", "ci-runner"], [],
        ["check", "host", "ssh", "<host>;", "on", "another", "host,", "put", "ssh", "<host>", "in", "front", "of", "each", "command"],
    ]


def test_a_run_with_the_index_set_that_ended_ends_with_its_record_and_delegate_clean_removes_the_record(tmp_path, capsys, monkeypatch, scripted):
    """A real run in a real git repository writes its record at its end. `delegate clean` of the run forgets the record."""
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = read_journal(out)[0]["run_id"]
    assert (code, err) == (0, "")
    assert [path.name for path in directory.iterdir()] == [f"{run_id}.json"]

    code = delegate.main(["clean", run_id, "--repo", str(repo)])
    captured = capsys.readouterr()

    assert (code, captured.err) == (0, "")
    assert list(directory.iterdir()) == []


def test_delegate_clean_of_a_run_keeps_the_record_of_another_run(tmp_path, capsys, monkeypatch, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    kept = record(DirectoryRunIndex(directory), "build-host", "docs-site", "a41e", "running", 3, 6, 47)
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = read_journal(out)[0]["run_id"]

    code = delegate.main(["clean", run_id, "--repo", str(repo)])
    capsys.readouterr()

    assert code == 0
    assert [path.name for path in directory.iterdir()] == [f"{kept.run_id}.json"]


def test_a_dry_run_of_delegate_clean_keeps_the_record(tmp_path, capsys, monkeypatch, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    directory = tmp_path / "shared-index"
    monkeypatch.setenv("DELEGATE_INDEX", str(directory))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    run_id = read_journal(out)[0]["run_id"]

    code = delegate.main(["clean", run_id, "--dry-run", "--repo", str(repo)])
    capsys.readouterr()

    assert code == 0
    assert [path.name for path in directory.iterdir()] == [f"{run_id}.json"]


def test_the_timeline_has_a_dim_lane_for_the_run_of_an_unreachable_host_with_unreachable_and_heard(machine, index, capsys):
    """Frame W9: the lane of such a run is its life up to the time that the host was heard from, the column of now, then `ended`, `state` and `alert`. It has no ETA."""
    this_machine(machine)
    two_other_hosts(index)

    code, out, err = runs(capsys, "--timeline")

    assert (code, err) == (0, "")
    lines = out.splitlines()
    docs_site = next(line for line in lines if "docs-site" in line)
    api_service = next(line for line in lines if "api-service" in line)
    assert docs_site.split()[:3] == ["build-host", "docs-site", "a41e"]
    assert docs_site.split()[4:] == ["┊", "3/6", "unreachable", "heard", "47m", "ago"]
    assert set(docs_site.split()[3]) == {"─"}
    assert api_service.split()[:3] == ["ci-runner", "api-service", "5d02"]
    assert api_service.split()[5:] == ["3/10", "unreachable", "heard", "3h05m", "ago"]
    assert lines[1] == "runs  1 of 3 reachable  ████████░░░░░░░░ 50%  1/2 ended  ETA - · 1 with no ETA  12k new · 340k cache"
