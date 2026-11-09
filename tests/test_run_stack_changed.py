"""`delegate run --resume --stack-changed` records a deliberate change of a stack at a resume (#290).

A resume refuses a changed configuration of a stack (#289). A coordinator sometimes must change a stack in a run: a
gate that is wrong, a timeout that is too short. `--stack-changed` lets the resume go on, and the journal shows the
change: a `stack-change` event with the stack, and the old and the new value of each changed field. A later resume
compares with the newest record, so it needs no flag.

Each case runs `delegate.main` against a real temporary git repository with the scripted adapter. The run pauses at
the request of `delegate pause`, which a second shell would give, while the specialist of ticket a works.
"""

import pytest

from delegate.cli import delegate

from .support import journal_path, make_repo, read_journal_file, resume_main, run_main, ticket_of
from .test_run_configuration_record import RUN_PAUSED, WORKFLOW, change_workflow, paused_run
from .test_run_multi import ticket

GATES = ('gates = ["ls *.txt"]', 'gates = ["true"]')
THREE = WORKFLOW + ticket("c", ("b",))


def pause_at(scripted, repo, run_id: str, ticket_id: str) -> None:
    """Pause the run at the step of `ticket_id`, as `delegate pause` in a second shell does."""

    def request(request):
        if ticket_of(request) == ticket_id:
            scripted.on_specialist = None
            assert delegate.main(["pause", run_id, "--repo", str(repo)]) == 0

    scripted.on_specialist = request


def test_a_resume_with_a_changed_gate_and_the_flag_goes_on_records_the_old_and_the_new_gates_and_a_second_resume_needs_no_flag(
    tmp_path, capsys, scripted
):
    repo = make_repo(tmp_path, THREE)
    run_id = paused_run(repo, capsys, scripted)
    change_workflow(repo, *GATES)
    pause_at(scripted, repo, run_id, "b")

    code, out, err = resume_main(repo, capsys, run_id, "--stack-changed")

    assert code == RUN_PAUSED, err
    events = read_journal_file(repo, run_id)
    (change,) = [e for e in events if e["event"] == "stack-change"]
    assert change["stack"] == "python"
    assert change["changes"] == {"gates": {"old": ["ls *.txt"], "new": ["true"]}}
    names = [e["event"] for e in events]
    assert names.index("stack-change") + 1 == names.index("resume")

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    assert [e["event"] for e in events].count("stack-change") == 1
    assert events[-1]["event"] == "run-end" and events[-1]["built"] == ["a", "b", "c"]


def test_a_resume_with_a_changed_gate_and_no_flag_is_refused_and_the_message_names_the_flag(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)
    before = journal_path(repo, run_id).read_bytes()
    change_workflow(repo, *GATES)

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "stack 'python': gates" in err and "--stack-changed" in err, err
    assert journal_path(repo, run_id).read_bytes() == before


def test_a_later_resume_compares_with_the_stack_change_and_refuses_a_new_change_of_the_stack(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id = paused_run(repo, capsys, scripted)
    change_workflow(repo, *GATES)
    pause_at(scripted, repo, run_id, "b")
    code, out, err = resume_main(repo, capsys, run_id, "--stack-changed")
    assert code == RUN_PAUSED, err
    before = journal_path(repo, run_id).read_bytes()
    change_workflow(repo, "agent-timeout = 600", "agent-timeout = 900")

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 1
    assert "stack 'python': agent_timeout" in err and "gates" not in err, err
    assert journal_path(repo, run_id).read_bytes() == before

    code, out, err = resume_main(repo, capsys, run_id, "--stack-changed")

    assert (code, err) == (0, "")
    second = [e for e in read_journal_file(repo, run_id) if e["event"] == "stack-change"][-1]
    assert second["changes"] == {"agent_timeout": {"old": 600, "new": 900}}


def test_the_flag_with_no_change_of_a_stack_is_refused_and_the_journal_does_not_change(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)
    before = journal_path(repo, run_id).read_bytes()

    code, out, err = resume_main(repo, capsys, run_id, "--stack-changed")

    assert code == 1
    assert "--stack-changed" in err and "no stack" in err, err
    assert journal_path(repo, run_id).read_bytes() == before


def test_the_dry_run_with_the_flag_prints_the_difference_says_that_the_resume_records_it_and_exits_0(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)
    before = journal_path(repo, run_id).read_bytes()
    change_workflow(repo, *GATES)

    code, out, err = resume_main(repo, capsys, run_id, "--dry-run", "--stack-changed")

    assert (code, err) == (0, "")
    assert "stack 'python': gates" in out
    assert 'was ["ls *.txt"], now ["true"]' in out
    assert "the resume will record the change in a stack-change event" in out
    assert journal_path(repo, run_id).read_bytes() == before


def test_the_dry_run_with_the_flag_and_no_change_of_a_stack_exits_1(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)

    code, out, err = resume_main(repo, capsys, run_id, "--dry-run", "--stack-changed")

    assert code == 1
    assert "no stack" in err, err


def test_a_top_level_change_is_refused_also_with_the_flag(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    run_id = paused_run(repo, capsys, scripted)
    before = journal_path(repo, run_id).read_bytes()
    change_workflow(repo, *GATES)
    change_workflow(repo, "keep-worktrees = true", "keep-worktrees = false")

    code, out, err = resume_main(repo, capsys, run_id, "--stack-changed")

    assert code == 1
    assert "keep_worktrees" in err, err
    assert journal_path(repo, run_id).read_bytes() == before


def test_the_flag_without_resume_is_a_usage_error(tmp_path, capsys):
    repo = make_repo(tmp_path, WORKFLOW)

    with pytest.raises(SystemExit) as stop:
        run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--stack-changed")

    assert stop.value.code == 2
    assert "--stack-changed needs --resume" in capsys.readouterr().err


def test_delegate_status_shows_one_line_that_names_the_stack_and_the_fields(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    run_id = paused_run(repo, capsys, scripted)
    change_workflow(repo, *GATES)
    change_workflow(repo, "agent-timeout = 600", "agent-timeout = 900")
    pause_at(scripted, repo, run_id, "b")
    code, out, err = resume_main(repo, capsys, run_id, "--stack-changed")
    assert code == RUN_PAUSED, err

    assert delegate.main(["status", run_id, "--repo", str(repo)]) == 0

    lines = [line for line in capsys.readouterr().out.splitlines() if "stack python changed" in line]
    assert lines == ["stack python changed: gates, agent_timeout"]


def test_delegate_watch_shows_one_feed_line_that_names_the_stack_and_the_fields(tmp_path, monkeypatch):
    from .support import bare_repo, frames
    from .test_watch_live import Run, ticket_a_is_built, watch

    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    ticket_a_is_built(run)
    run.add(3700, "stack-change", stack="python", changes={"gates": {"old": ["a"], "new": ["b"]}, "gate_timeout": {"old": 600, "new": 900}})
    run.add(3700, "resume", run_id="r1", built=["a"], failed=[], skipped=[], open=None)
    run.add(3800, "run-end", result="built", built=["a"], failed=[], skipped=[])

    code, out = watch(repo, monkeypatch)

    (last,) = frames(out)
    assert [line[7:] for line in last if "changed" in line] == ["stack python changed: gates, gate_timeout"]
