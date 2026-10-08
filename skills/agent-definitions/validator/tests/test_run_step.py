"""`delegate run <workflow>` builds a ticket through a harness adapter.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter is the
seam where a harness would be, so the engine runs end to end with no model.
"""

import json

import pytest

from agent_definitions import adapters, delegate

from .support import ScriptedAdapter, WORKFLOW, event_names, git, make_repo, read_journal, valid_report


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_ticket_branch_starts_from_the_base_branch_and_holds_the_scripted_commit(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    base_tip = git(repo, "rev-parse", "main").strip()
    # The checked-out branch differs from the base branch: the worktree must still start from main.
    git(repo, "checkout", "-q", "-b", "other")
    (repo / "other.txt").write_text("other\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "work on another branch")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    branches = git(repo, "branch", "--list", "--format=%(refname:short)").split()
    ticket_branch = next(b for b in branches if b not in ("main", "other"))
    log = git(repo, "log", "--format=%H %s", ticket_branch).splitlines()
    assert [line.split(" ", 1)[1] for line in log] == ["scripted work (python-specialist)", "seed"]
    assert log[1].split()[0] == base_tip
    assert git(repo, "show", f"{ticket_branch}:feature.txt") == "feature\n"
    assert "other.txt" not in git(repo, "ls-tree", "-r", "--name-only", ticket_branch)


def test_the_journal_records_the_run_the_step_and_the_adapter_result(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    events = read_journal(out)
    names = event_names(events)
    assert [n for n in names if n in ("run-start", "step-start", "adapter-result", "step-end", "run-end")] == [
        "run-start", "step-start", "adapter-result", "step-end", "run-end",
    ]
    result = next(e for e in events if e["event"] == "adapter-result")
    assert (result["ticket"], result["exit_status"], result["end_state"], result["session_id"]) == ("a", 0, "finished", "session-1")
    assert next(e for e in events if e["event"] == "step-end")["state"] == "built"
    assert next(e for e in events if e["event"] == "run-end")["result"] == "built"


def test_an_adapter_with_no_implementation_exits_1_names_it_and_creates_nothing(tmp_path, capsys):
    # `claude-code` is a valid adapter name, but nothing registers an implementation yet.
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    before = (git(repo, "branch", "--all"), git(repo, "worktree", "list"), git(repo, "status", "--short"))

    code, out, err = run(repo, capsys)

    assert (code, out) == (1, "")
    assert "claude-code" in err
    assert "Traceback" not in err
    assert (git(repo, "branch", "--all"), git(repo, "worktree", "list"), git(repo, "status", "--short")) == before


def test_a_valid_report_is_recorded_as_valid_after_the_adapter_result(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    events = read_journal(out)
    names = event_names(events)
    assert names.index("adapter-result") < names.index("report-validation") < names.index("step-end")
    validation = next(e for e in events if e["event"] == "report-validation")
    assert (validation["ticket"], validation["valid"], validation["reason"]) == ("a", True, None)


def test_a_missing_report_fails_the_step_and_exits_1_without_a_traceback(tmp_path, capsys, scripted):
    scripted.write_report = False
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    assert "ticket a" in err and "report" in err
    events = read_journal(out)
    validation = next(e for e in events if e["event"] == "report-validation")
    assert validation["valid"] is False
    assert "missing" in validation["reason"] and "a.specialist.json" in validation["reason"]
    step_end = next(e for e in events if e["event"] == "step-end")
    assert (step_end["state"], step_end["reason"]) == ("failed", validation["reason"])
    assert next(e for e in events if e["event"] == "run-end")["result"] == "failed"


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        pytest.param("not json at all", ["not valid JSON"], id="not-json"),
        pytest.param("[1, 2]", ["JSON object"], id="not-an-object"),
        pytest.param('{"ticket": "a"}', ["status", "missing"], id="fields-missing"),
        pytest.param(
            lambda request, head: json.dumps(valid_report("a", "b", head, status="done")),
            ["status", "'done'", "committed", "blocked", "partial"],
            id="status-outside-the-set",
        ),
        pytest.param(
            lambda request, head: json.dumps(valid_report("a", "b", head, gates_green="yes")),
            ["gates_green", "boolean"],
            id="gates-green-not-a-boolean",
        ),
        pytest.param(
            lambda request, head: json.dumps(valid_report("a", "b", head, commits="abc")),
            ["commits", "list of strings"],
            id="commits-not-a-list",
        ),
        pytest.param(
            lambda request, head: json.dumps(valid_report("zzz", "b", head)),
            ["ticket", "'zzz'", "'a'"],
            id="report-for-another-ticket",
        ),
    ],
)
def test_an_invalid_report_fails_the_step_and_the_reason_names_the_field(tmp_path, capsys, scripted, report, expected):
    scripted.report_text = report
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    validation = next(e for e in read_journal(out) if e["event"] == "report-validation")
    assert validation["valid"] is False
    for part in expected:
        assert part in validation["reason"]
        assert part in err


def gate_events(out):
    return [e for e in read_journal(out) if e["event"] == "gate-result"]


def test_the_engine_runs_the_gates_in_the_worktree_and_records_each_result(tmp_path, capsys, scripted):
    # `test -f feature.txt` is green only when the scripted commit is in the worktree the gate runs in.
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    names = event_names(read_journal(out))
    assert names.index("report-validation") < names.index("gate-result") < names.index("step-end")
    (gate,) = gate_events(out)
    assert (gate["ticket"], gate["command"], gate["exit_status"], gate["green"]) == ("a", "test -f feature.txt", 0, True)


def test_a_red_gate_is_recorded_red_when_the_report_claims_green(tmp_path, capsys, scripted):
    scripted.files = {"other.txt": "no feature file\n"}  # the gate looks for feature.txt
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    (gate,) = gate_events(out)
    assert (gate["command"], gate["exit_status"], gate["green"]) == ("test -f feature.txt", 1, False)
    step_end = next(e for e in read_journal(out) if e["event"] == "step-end")
    assert step_end["state"] == "failed"
    assert "test -f feature.txt" in step_end["reason"] and "red" in step_end["reason"]
    assert "test -f feature.txt" in err


def test_every_gate_runs_and_its_output_is_recorded_even_when_an_earlier_gate_is_red(tmp_path, capsys, scripted):
    workflow = WORKFLOW.replace('gates = ["test -f feature.txt"]', 'gates = ["echo first-gate-output; exit 3", "test -f feature.txt"]')
    repo = make_repo(tmp_path, workflow)

    code, out, err = run(repo, capsys)

    first, second = gate_events(out)
    assert (first["exit_status"], first["green"]) == (3, False)
    assert "first-gate-output" in first["output_tail"]
    assert (second["exit_status"], second["green"]) == (0, True)
    assert code == 1


@pytest.mark.parametrize("status", ["blocked", "partial"])
def test_a_report_that_is_not_committed_fails_the_step_before_the_gates_run(tmp_path, capsys, scripted, status):
    scripted.report_text = lambda request, head: json.dumps(
        valid_report("a", "b", head, status=status, gates_green=False, blocked_reason="the tool is missing")
    )
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert gate_events(out) == []
    step_end = next(e for e in read_journal(out) if e["event"] == "step-end")
    assert step_end["state"] == "failed"
    assert status in step_end["reason"] and "the tool is missing" in step_end["reason"]


def test_a_branch_with_no_commit_beyond_the_base_fails_the_step_even_when_the_report_says_committed(tmp_path, capsys, scripted):
    scripted.files = {}  # the scripted specialist commits nothing, but its report claims it did
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    step_end = next(e for e in read_journal(out) if e["event"] == "step-end")
    assert step_end["state"] == "failed"
    assert "no commit" in step_end["reason"] and "main" in step_end["reason"]
