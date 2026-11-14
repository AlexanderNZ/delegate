"""The configuration file of the user and the record rule of the run index (#323).

The file is `$XDG_CONFIG_HOME/delegate/config.toml`; each test has its own empty `XDG_CONFIG_HOME` (`isolated_config_home`). Its key `index`
names a shared directory; `DELEGATE_INDEX`, when it is set, wins over it, and with neither the index is `index/` of the state root. Each command
that opens the index writes a record for each listed run of this machine that has none, or an older one. Each error of the file is one line
that names the file, the key and the expected form: `delegate runs` exits 2 with it. A case runs `delegate run` end to end against a real
temporary git repository with the scripted adapter, or writes journals in the state root of the case (`journals.write_run`).
"""

from __future__ import annotations

import json
import socket
from datetime import timedelta
from pathlib import Path

import pytest

from ..support import event_names, make_repo, read_journal, run_main
from ..test_run_multi import HEAD, ticket
from .journals import NOW, built, clock_time, run_end, runs, working, write_run

TWO_TICKETS = HEAD + ticket("a") + ticket("b", ("a",))


def config_file(config_home: Path, text: str) -> Path:
    """Write the configuration file of the user with `text`, and return its path."""
    path = config_home / "delegate" / "config.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def records(directory: Path) -> dict[str, dict[str, object]]:
    """The records of the index directory, by run id."""
    return {path.stem: json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))} if directory.is_dir() else {}


def test_with_index_in_the_configuration_file_and_no_delegate_index_the_run_writes_its_record_in_the_named_directory(
    tmp_path, capsys, scripted, machine, isolated_config_home,
):
    shared = tmp_path / "shared"
    config_file(isolated_config_home, f'index = "{shared}"\n')
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    run_id = read_journal(out)[0]["run_id"]
    assert list(records(shared)) == [run_id]
    assert records(shared)[run_id]["state"] == "built"
    assert records(machine / "index") == {}


def test_delegate_index_wins_over_the_index_of_the_configuration_file(tmp_path, capsys, scripted, machine, isolated_config_home, monkeypatch):
    from_file, from_variable = tmp_path / "from-file", tmp_path / "from-variable"
    config_file(isolated_config_home, f'index = "{from_file}"\n')
    monkeypatch.setenv("DELEGATE_INDEX", str(from_variable))
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    assert list(records(from_variable)) == [read_journal(out)[0]["run_id"]]
    assert records(from_file) == {}


def one_run_of_an_earlier_version(root: Path) -> str:
    """A run with no record: 1 of 2 tickets ended, a ticket runs now and its process holds the lock. It started at 13:40. Return its run id."""
    started = clock_time(13, 40)
    path = write_run(root, "alpha", "aaaa", started, [*built("1", started, clock_time(14, 0)), working("2", clock_time(14, 5))], lock=True)
    return path.parent.name


@pytest.mark.parametrize("argv", [(), ("--local",), ("--json",)])
def test_a_run_of_an_earlier_version_with_no_record_gets_its_record_when_delegate_runs_runs(machine, capsys, argv):
    run_id = one_run_of_an_earlier_version(machine)

    code, _, err = runs(capsys, *argv)

    assert (code, err) == (0, "")
    found = records(machine / "index")
    assert list(found) == [run_id]
    assert {key: found[run_id][key] for key in ("host", "repo", "started", "state", "tickets_ended", "tickets_total", "heard")} == {
        "host": "laptop", "repo": "alpha", "started": clock_time(13, 40).isoformat(), "state": "running", "tickets_ended": 1, "tickets_total": 2,
        "heard": NOW.isoformat(),
    }


def test_an_older_record_is_written_again_and_a_record_as_new_as_the_journal_is_not(machine, capsys):
    """The record says `running` 0 of 2, heard at 14:01; the journal has an event at 14:05. The command writes it again; a second command does not."""
    run_id = one_run_of_an_earlier_version(machine)
    index = machine / "index"
    index.mkdir()
    older = {
        "run_id": run_id, "host": "laptop", "repo": "alpha", "started": clock_time(13, 40).isoformat(), "state": "running", "tickets_ended": 0,
        "tickets_total": 2, "eta": None, "heard": clock_time(14, 1).isoformat(),
    }
    (index / f"{run_id}.json").write_text(json.dumps(older))

    runs(capsys, "--local")
    first = records(index)[run_id]
    (index / f"{run_id}.json").write_text(json.dumps({**first, "eta": "kept"}))
    runs(capsys, "--local")

    assert (first["tickets_ended"], first["heard"]) == (1, NOW.isoformat())
    assert records(index)[run_id]["eta"] == "kept"


def test_a_record_with_the_host_of_run_start_keeps_that_host_when_another_host_reads_and_a_second_command_writes_nothing(machine, capsys, clock):
    """#326: the host of `run-start` is `ci-box`; the reader is `laptop`. The record keeps `ci-box`, and the next command leaves the file as it is."""
    started = clock_time(13, 40)
    path = write_run(machine, "alpha", "aaaa", started, [*built("1", started, clock_time(14, 0)), working("2", clock_time(14, 5))], lock=True, host="ci-box")
    run_id = path.parent.name
    index = machine / "index"
    index.mkdir()
    engine = {
        "run_id": run_id, "host": "ci-box", "repo": "alpha", "started": started.isoformat(), "state": "running", "tickets_ended": 0,
        "tickets_total": 2, "eta": None, "heard": clock_time(14, 1).isoformat(),
    }
    (index / f"{run_id}.json").write_text(json.dumps(engine))

    runs(capsys, "--local")
    first = (index / f"{run_id}.json").read_text()
    clock.at = (NOW + timedelta(minutes=5)).timestamp()
    runs(capsys, "--local")

    written = json.loads(first)
    assert {key: value for key, value in written.items() if key not in ("eta", "heard")} == {
        key: value for key, value in {**engine, "tickets_ended": 1}.items() if key not in ("eta", "heard")
    }
    assert written["heard"] == NOW.isoformat()
    assert (index / f"{run_id}.json").read_text() == first


def test_a_run_with_the_host_of_run_start_and_no_record_gets_a_record_with_that_host(machine, capsys):
    started = clock_time(13, 40)
    path = write_run(machine, "alpha", "aaaa", started, [*built("1", started, clock_time(14, 0))], lock=True, host="ci-box")

    runs(capsys, "--local")

    assert records(machine / "index")[path.parent.name]["host"] == "ci-box"


def test_a_run_that_ended_before_the_window_gets_no_record(machine, capsys):
    """A run is listed when it ended in the last 6 hours; one that ended 8 hours ago is not listed, so it gets no record."""
    started = clock_time(6, 0)
    write_run(machine, "alpha", "old0", started, [*built("1", started, clock_time(6, 10)), run_end(clock_time(6, 20))], tickets=("1",))

    runs(capsys)

    assert records(machine / "index") == {}


def test_a_record_that_cannot_be_written_gives_one_warning_line_and_runs_goes_on(machine, capsys, tmp_path, monkeypatch):
    one_run_of_an_earlier_version(machine)
    blocked = tmp_path / "a-file"
    blocked.write_text("a file, so the index cannot make its directory\n")
    monkeypatch.setenv("DELEGATE_INDEX", str(blocked))

    code, out, err = runs(capsys, "--local")

    assert code == 0
    assert len(err.splitlines()) == 1
    assert err.startswith(f"delegate runs: run index {blocked}: cannot record run ")
    assert "aaaa" in out


@pytest.mark.parametrize(("text", "words"), [
    ('host = ["far"]\n', "unknown key 'host'; the keys are index = "),
    ('hosts = "far"\n', "hosts must be a list of host names, not 'far'; write hosts = [\"build-host\"]"),
    ("index = 3\n", 'index must be text, not 3; write index = "/an/absolute/directory"'),
    ('index = "relative/dir"\n', "index is 'relative/dir', which is not an absolute path"),
    ('hosts = ["laptop"]\n', "hosts has 'laptop', which is this machine; remove it"),
    ('hosts = ["localhost"]\n', "hosts has 'localhost', which is this machine"),
    ("hosts = [\n", "the file is not valid TOML: "),
])
def test_an_error_of_the_configuration_file_gives_one_line_that_names_the_file_and_exit_2(machine, capsys, isolated_config_home, text, words):
    path = config_file(isolated_config_home, text)

    code, out, err = runs(capsys)

    assert (code, out) == (2, "")
    assert len(err.splitlines()) == 1
    assert err.startswith(f"delegate runs: config {path}: ")
    assert words in err


def test_hosts_that_names_the_host_name_of_this_machine_is_an_error(machine, capsys, isolated_config_home, monkeypatch):
    monkeypatch.delenv("DELEGATE_HOST")
    config_file(isolated_config_home, f'hosts = ["{socket.gethostname().split(".", 1)[0].upper()}"]\n')

    code, _, err = runs(capsys)

    assert code == 2
    assert "which is this machine" in err


def test_a_configuration_file_that_cannot_be_read_gives_one_line_and_exit_2(machine, capsys, isolated_config_home):
    path = isolated_config_home / "delegate" / "config.toml"
    path.mkdir(parents=True)  # a directory in the place of the file

    code, out, err = runs(capsys, "--local")

    assert (code, out) == (2, "")
    assert err.startswith(f"delegate runs: config {path}: cannot read the file: ")
    assert len(err.splitlines()) == 1


def test_a_configuration_file_that_breaks_while_a_run_goes_on_does_not_stop_the_run(tmp_path, capsys, scripted, machine, isolated_config_home):
    repo = make_repo(tmp_path, TWO_TICKETS)
    scripted.on_specialist = lambda request: config_file(isolated_config_home, "not valid toml = = =\n")

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    events = read_journal(out)
    assert (code, err) == (0, "")
    assert (events[-1]["event"], events[-1]["result"]) == ("run-end", "built")
    assert "index-warning" not in event_names(events)


def test_the_header_line_names_the_configuration_file_the_index_and_the_hosts_that_it_read(
    machine, capsys, isolated_config_home, tmp_path, ssh, monkeypatch,
):
    monkeypatch.setenv("COLUMNS", "1000")
    shared = tmp_path / "shared"
    path = config_file(isolated_config_home, f'index = "{shared}"\nhosts = ["far", "box"]\n')

    code, out, err = runs(capsys)

    assert (code, err) == (0, "")
    assert out.splitlines()[0] == f"config {path} · index {shared} · hosts box, far"
