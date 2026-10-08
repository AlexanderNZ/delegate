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


def tier_file(tmp_path, columns=("claude-code", "opencode")):
    """A tier table whose model names tell the column and the tier apart."""
    lines = ['[effort]\nlevels = ["low"]\n\n[effort.opencode]\nvariants = ["high"]\n']
    for tier in ("strong", "standard", "cheap", "verifier"):
        lines.append(f"[tiers.{tier}]")
        lines.extend(f'{column} = "{column}-{tier}-model"' for column in columns)
        lines.append("")
    lines.append("[allowed-models]")
    lines.extend(f"{column} = []" for column in columns)
    lines.append("\n[budget]\nclaude-code-description-chars = 1\n")
    path = tmp_path / "tiers.toml"
    path.write_text("\n".join(lines))
    return path


@pytest.mark.parametrize(
    ("column", "mode", "override", "expected"),
    [
        pytest.param("claude-code", "assure", None, "claude-code-strong-model", id="claude-code-column-assure-is-strong"),
        pytest.param("opencode", "assure", None, "opencode-strong-model", id="opencode-column-assure-is-strong"),
        pytest.param("opencode", "economy", None, "opencode-standard-model", id="economy-is-standard"),
        pytest.param("opencode", "economy", "cheap", "opencode-cheap-model", id="override-by-tier-name"),
    ],
)
def test_the_adapter_receives_the_model_from_its_own_tier_column(tmp_path, capsys, scripted, column, mode, override, expected):
    scripted.tier_column = column
    workflow = WORKFLOW.replace('mode = "assure"', f'mode = "{mode}"')
    if override:
        workflow = workflow.replace("[stacks.python]", f'[tier-overrides]\nspecialist = "{override}"\n\n[stacks.python]')
    repo = make_repo(tmp_path, workflow)

    code, out, err = run(repo, capsys, "--tiers", str(tier_file(tmp_path)))

    assert (code, err) == (0, "")
    assert [call.model for call in scripted.calls] == [expected]
    step_start = next(e for e in read_journal(out) if e["event"] == "step-start")
    assert step_start["model"] == expected


def test_a_tier_table_without_the_column_of_the_adapter_exits_1_and_names_the_column(tmp_path, capsys, scripted):
    scripted.tier_column = "cursor"
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys, "--tiers", str(tier_file(tmp_path)))

    assert (code, out) == (1, "")
    assert "'cursor'" in err and "strong" in err
    assert "Traceback" not in err
    assert scripted.calls == []


def brief_sections(prompt):
    """The prompt as a map from section heading to section body."""
    parts = prompt.split("\n## ")
    return {part.split("\n", 1)[0].removeprefix("## "): part.split("\n", 1)[1] for part in parts if "\n" in part}


def test_the_brief_holds_the_ticket_the_file_boundary_the_gates_and_the_report_path(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    (call,) = scripted.calls
    sections = brief_sections(call.prompt)
    assert "ticket a" in sections["Task"].lower() and "The export command writes a CSV file." in sections["Task"]
    boundary = sections["File boundary"]
    assert str(call.cwd) in boundary
    assert "LICENSE" in boundary and ".github/workflows/*" in boundary
    assert "push" in boundary
    assert "test -f feature.txt" in sections["Gates"]
    report = sections["Report"]
    assert str(call.report_path) in report
    for name in ("ticket", "status", "branch", "head_sha", "commits", "gates_green", "summary"):
        assert f"`{name}`" in report


def test_the_brief_gives_the_text_of_a_ticket_file_and_states_when_no_path_is_reserved(tmp_path, capsys, scripted):
    workflow = WORKFLOW.replace('text = "The export command writes a CSV file."', 'text-file = "ticket-a.md"').replace(
        'hotspots = ["LICENSE", ".github/workflows/*"]', "hotspots = []"
    )
    repo = make_repo(tmp_path, workflow)
    (repo / "ticket-a.md").write_text("The export command reads from a pipe.\n")

    code, out, err = run(repo, capsys)

    (call,) = scripted.calls
    sections = brief_sections(call.prompt)
    assert "The export command reads from a pipe." in sections["Task"]
    assert "no path is reserved" in sections["File boundary"].lower()


@pytest.mark.parametrize(("end_state", "exit_status"), [("failed", 1), ("capped", 0), ("finished", 2)])
def test_a_specialist_that_ends_failed_capped_or_with_a_bad_exit_status_fails_the_step_and_the_report_is_not_trusted(
    tmp_path, capsys, scripted, end_state, exit_status
):
    scripted.end_state = end_state
    scripted.exit_status = exit_status  # the report on disk is valid, but the run did not finish
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    events = read_journal(out)
    result = next(e for e in events if e["event"] == "adapter-result")
    assert (result["end_state"], result["exit_status"]) == (end_state, exit_status)
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed"
    assert end_state in step_end["reason"] and str(exit_status) in step_end["reason"]
    assert "gate-result" not in event_names(events)


TWO_TICKETS = WORKFLOW + '''
[[tickets]]
id = "b"
text = "The export command writes a header row."
stack = "python"
blocked-by = ["a"]
'''


def test_tickets_are_built_in_dependency_order_each_from_the_base_branch(tmp_path, capsys, scripted):
    # The file lists the dependent ticket b first. The plan must still build a first.
    head, a_ticket, b_ticket = WORKFLOW.split("[[tickets]]")[0], WORKFLOW.split("[[tickets]]")[1], TWO_TICKETS.split("[[tickets]]")[2]
    repo = make_repo(tmp_path, head + "[[tickets]]" + b_ticket + "[[tickets]]" + a_ticket)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    started = [e["ticket"] for e in events if e["event"] == "step-start"]
    assert started == ["a", "b"]
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["result"], run_end["built"]) == ("built", ["a", "b"])
    bases = {e["base_commit"] for e in events if e["event"] == "step-start"}
    assert bases == {git(repo, "rev-parse", "main").strip()}


def test_a_failed_step_ends_the_run_and_no_later_ticket_starts(tmp_path, capsys, scripted):
    scripted.end_state = "failed"
    scripted.exit_status = 1
    repo = make_repo(tmp_path, TWO_TICKETS)

    code, out, err = run(repo, capsys)

    assert code == 1
    events = read_journal(out)
    assert [e["ticket"] for e in events if e["event"] == "step-start"] == ["a"]
    run_end = next(e for e in events if e["event"] == "run-end")
    assert (run_end["result"], run_end["built"], run_end["failed"]) == ("failed", [], ["a"])
    assert len(scripted.calls) == 1


def test_a_base_branch_that_does_not_exist_exits_1_names_it_and_creates_nothing(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, WORKFLOW.replace('base-branch = "main"', 'base-branch = "trunk"'))

    code, out, err = run(repo, capsys)

    assert (code, out) == (1, "")
    assert "'trunk'" in err and "Traceback" not in err
    assert not (repo / ".git" / "delegate").exists()
    assert scripted.calls == []


def test_a_ticket_branch_that_exists_stops_the_run_before_it_starts(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    assert run(repo, capsys)[0] == 0
    calls_before = len(scripted.calls)
    runs_before = sorted(path.name for path in (repo / ".git" / "delegate" / "runs").iterdir())

    code, out, err = run(repo, capsys)  # the same workflow again: the branch of ticket a is taken

    assert (code, out) == (1, "")
    assert "run/demo-a" in err and "Traceback" not in err
    assert len(scripted.calls) == calls_before
    assert sorted(path.name for path in (repo / ".git" / "delegate" / "runs").iterdir()) == runs_before
