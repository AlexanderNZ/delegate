"""The record of the configuration of each stack: `run-start` holds it, and a resume refuses a change of it (#289).

A resume reads the workflow file again. Without the record, a person could remove a hotspot, a gate or a `prepare`
command from a stack, and resume: the journal would not show under which rules a ticket was accepted. `run-start`
records the configuration of each stack, `ticket-start` and `keep-worktrees` in the field `configuration`. A resume
compares the workflow file with the newest record, and refuses a change before it writes to the journal.

Each case runs `delegate.main` against a real temporary git repository with the scripted adapter. The run pauses at
the request of `delegate pause`, which a second shell would give, while the specialist of ticket a works.
"""

import json

import pytest

from delegate.cli import delegate

from .support import journal_path, make_repo, read_journal_file, resume_main, run_main, state_dir, ticket_of
from .test_run_multi import HEAD, ticket

RUN_PAUSED = 3

WORKFLOW = HEAD + ticket("a") + ticket("b", ("a",))


def paused_run(repo, capsys, scripted) -> str:
    """Start the run of `workflow.toml`, pause it while the specialist of ticket a works, and return the run id."""

    def request(request):
        if ticket_of(request) == "a" and scripted.on_specialist is not None:
            scripted.on_specialist = None
            (run_dir,) = (state_dir(repo) / "runs").iterdir()
            assert delegate.main(["pause", run_dir.name, "--repo", str(repo)]) == 0

    scripted.on_specialist = request
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == RUN_PAUSED, err
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    return run_dir.name


def change_workflow(repo, old: str, new: str) -> None:
    path = repo / "workflow.toml"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_run_start_records_the_configuration_of_each_stack_and_the_top_level_fields(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)

    run_id = paused_run(repo, capsys, scripted)

    record = read_journal_file(repo, run_id)[0]["configuration"]
    assert (record["ticket_start"], record["keep_worktrees"]) == ("run-branch", True)
    python = record["stacks"]["python"]
    assert (python["specialist"], python["verifier"]) == ("python-specialist", "python-verifier")
    assert python["gates"] == ["ls *.txt"]
    assert python["hotspots"] == ["LICENSE", ".github/workflows/*"]
    assert (python["gate_timeout"], python["agent_timeout"]) == (600, 600)
    assert (python["prepare"], python["mcp_servers"], python["specialist_notes"]) == ([], [], None)


@pytest.mark.parametrize(
    ("old", "new", "field"),
    [
        pytest.param('hotspots = ["LICENSE", ".github/workflows/*"]', 'hotspots = ["LICENSE"]', "hotspots", id="hotspot-removed"),
        pytest.param('gates = ["ls *.txt"]', 'gates = ["true"]', "gates", id="gate-changed"),
        pytest.param("gate-timeout = 600", 'gate-timeout = 600\nprepare = ["true"]', "prepare", id="prepare-added"),
        pytest.param("gate-timeout = 600", 'gate-timeout = 600\nverifier-notes = "Accept it."', "verifier_notes", id="notes-added"),
        pytest.param("agent-timeout = 600", "agent-timeout = 900", "agent_timeout", id="timeout-changed"),
    ],
)
def test_a_resume_with_a_changed_stack_is_refused_and_the_journal_does_not_change(tmp_path, capsys, scripted, old, new, field):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)
    before = journal_path(repo, run_id).read_bytes()
    change_workflow(repo, old, new)

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "stack 'python'" in err and field in err, err
    assert journal_path(repo, run_id).read_bytes() == before


def test_a_resume_with_a_changed_top_level_field_is_refused(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)
    before = journal_path(repo, run_id).read_bytes()
    change_workflow(repo, "keep-worktrees = true", "keep-worktrees = false")

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "keep_worktrees" in err and "stack 'python'" not in err, err
    assert journal_path(repo, run_id).read_bytes() == before


def test_the_dry_run_of_a_resume_with_a_removed_hotspot_prints_the_difference_and_exits_1(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)
    before = journal_path(repo, run_id).read_bytes()
    change_workflow(repo, 'hotspots = ["LICENSE", ".github/workflows/*"]', 'hotspots = ["LICENSE"]')

    code, out, err = resume_main(repo, capsys, run_id, "--dry-run")

    assert code == 1
    assert "stack 'python': hotspots" in err, err
    assert journal_path(repo, run_id).read_bytes() == before


def test_a_resume_with_the_same_configuration_goes_on_and_writes_no_new_record(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    (resumed,) = [e for e in events if e["event"] == "resume"]
    assert "configuration" not in resumed
    assert (events[-1]["event"], events[-1]["result"], events[-1]["built"]) == ("run-end", "built", ["a", "b"])


def test_a_journal_of_an_earlier_version_with_no_record_resumes_and_the_resume_event_holds_the_record(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)
    path = journal_path(repo, run_id)
    first, rest = path.read_text().split("\n", 1)
    run_start = json.loads(first)
    del run_start["configuration"]
    path.write_text(json.dumps(run_start) + "\n" + rest)
    # The record that the resume writes is the configuration of the file now: a removed hotspot is not refused.
    change_workflow(repo, 'hotspots = ["LICENSE", ".github/workflows/*"]', 'hotspots = ["LICENSE"]')

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    (resumed,) = [e for e in events if e["event"] == "resume"]
    assert resumed["configuration"]["stacks"]["python"]["hotspots"] == ["LICENSE"]
    assert resumed["configuration"]["keep_worktrees"] is True
    assert events[-1]["event"] == "run-end"


def test_a_later_resume_compares_with_the_record_of_the_resume_event(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW + ticket("c", ("b",)))
    run_id = paused_run(repo, capsys, scripted)
    path = journal_path(repo, run_id)
    first, rest = path.read_text().split("\n", 1)
    run_start = json.loads(first)
    del run_start["configuration"]
    path.write_text(json.dumps(run_start) + "\n" + rest)

    def pause_at_b(request):
        if ticket_of(request) == "b":
            scripted.on_specialist = None
            assert delegate.main(["pause", run_id, "--repo", str(repo)]) == 0

    scripted.on_specialist = pause_at_b
    code, out, err = resume_main(repo, capsys, run_id)
    assert code == RUN_PAUSED, err
    before = path.read_bytes()
    change_workflow(repo, 'gates = ["ls *.txt"]', 'gates = ["true"]')

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "stack 'python': gates" in err, err
    assert path.read_bytes() == before
