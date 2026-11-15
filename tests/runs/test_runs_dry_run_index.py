"""The setup check of `delegate run --dry-run` checks the configuration file and the run index, and a run with an error of the configuration does not start (#323).

The dry run prints the configuration file that the run reads, the index directory and where it comes from, and the result of a write probe of the
directory. An error of the configuration file, or an index directory that the process cannot write, is a problem of the setup check: the dry run
exits 1. `delegate run`, `--detach` and `--resume` make the same check of the configuration before they create a branch or a journal event, and exit 1
with the same line. After the start, a write of the index that fails adds `index-warning` (ADR 0008), and the run ends with its own result.
A case runs `delegate run` against a real temporary git repository with the scripted adapter.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from delegate.ports.harness import AdapterError

from ..support import HEAD, event_names, git, make_repo, read_journal, resume_main, run_main, ticket, ticket_of
from ..support_runs import config_file

TWO_TICKETS = HEAD + ticket("a") + ticket("b", ("a",))


def refs(repo: Path) -> str:
    return git(repo, "for-each-ref", "--format=%(refname) %(objectname)")


def test_the_dry_run_prints_the_config_file_the_index_directory_and_its_source_and_ok_for_the_write_probe(tmp_path, capsys, scripted, machine):
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[-3:] == [
        "config: none, the defaults apply", f"run index: {machine / 'index'} (from the default of the state root)", "run index write: ok",
    ]
    assert not (machine / "index").exists()  # the probe removes its file and the directory that it made


def test_the_dry_run_names_the_configuration_file_and_an_index_that_it_names(tmp_path, capsys, scripted, machine, isolated_config_home):
    shared = tmp_path / "shared"
    path = config_file(isolated_config_home, f'index = "{shared}"\nhosts = ["far"]\n')
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    assert (code, err) == (0, "")
    assert out.splitlines()[-3:] == [f"config: {path}", f"run index: {shared} (from the configuration file)", "run index write: ok"]


def test_an_index_directory_that_the_process_cannot_write_is_a_problem_of_the_dry_run(tmp_path, capsys, scripted, machine, monkeypatch):
    blocked = tmp_path / "a-file"
    blocked.write_text("a file, so the index cannot be a directory\n")
    monkeypatch.setenv("DELEGATE_INDEX", str(blocked))
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    assert code == 1
    assert out.splitlines()[-2:] == [f"run index: {blocked} (from DELEGATE_INDEX)", "run index write: failed"]
    assert err.startswith(f"delegate run: run index {blocked}: cannot write a file: ")


def test_an_unknown_key_makes_the_dry_run_exit_1(tmp_path, capsys, scripted, machine, isolated_config_home):
    path = config_file(isolated_config_home, 'host = ["far"]\n')
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    assert code == 1
    assert out.splitlines()[-1] == f"config: {path}"
    assert err.startswith(f"delegate run: config {path}: unknown key 'host'")


def test_an_unknown_key_makes_delegate_run_exit_1_with_no_new_branch_and_no_journal(tmp_path, capsys, scripted, machine, isolated_config_home):
    path = config_file(isolated_config_home, 'host = ["far"]\n')
    repo = make_repo(tmp_path, TWO_TICKETS)
    before = refs(repo)

    for detach in ((), ("--detach",)):
        code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), *detach)

        assert (code, out) == (1, "")
        assert err.splitlines() == [f"delegate run: config {path}: unknown key 'host'; the keys are index = \"/an/absolute/directory\" "
                                    "(text: the path of a shared directory); hosts = [\"build-host\"] (a list of the ssh names of the other hosts)"]
    assert refs(repo) == before
    assert list(machine.glob("*/runs/*")) == []


def test_an_unknown_key_makes_a_resume_exit_1_before_it_writes_resume(tmp_path, capsys, scripted, machine, isolated_config_home):
    repo = make_repo(tmp_path, TWO_TICKETS)

    def not_logged_in(request):
        if ticket_of(request) == "b":
            raise AdapterError("claude: not logged in")

    scripted.on_specialist = not_logged_in
    code, out, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 4
    run_id = read_journal(out)[0]["run_id"]
    (journal,) = machine.glob(f"*/runs/{run_id}/journal.jsonl")
    before, branches = journal.read_text(), refs(repo)
    scripted.on_specialist = None
    config_file(isolated_config_home, 'hosts = "far"\n')

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "hosts must be a list of host names" in err
    assert journal.read_text() == before
    assert refs(repo) == branches


def test_after_the_start_an_index_directory_that_goes_away_gives_index_warning_and_the_run_ends_with_its_own_result(
    tmp_path, capsys, scripted, machine, monkeypatch,
):
    shared = tmp_path / "shared"
    monkeypatch.setenv("DELEGATE_INDEX", str(shared))
    repo = make_repo(tmp_path, TWO_TICKETS)

    def take_the_index_away(request):
        if ticket_of(request) == "b" and shared.is_dir():
            shutil.rmtree(shared)
            shared.write_text("a file in the place of the directory\n")

    scripted.on_specialist = take_the_index_away

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    events = read_journal(out)
    assert (code, err) == (0, "")
    ended = [event for event in events if event["event"] != "index-warning"][-1]
    assert (ended["event"], ended["result"], ended["built"]) == ("run-end", "built", ["a", "b"])
    warnings = [event for event in events if event["event"] == "index-warning"]
    assert len(warnings) == 2  # the step-end of b and the run-end
    assert event_names(events)[-1] == "index-warning"
