"""A specialist that ends capped or failed, or whose gates are red, continues in the same worktree.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter ends
each call in the state that the test gives, so the engine runs end to end with
no model.
"""

import pytest

from delegate import adapters, delegate

from .support import ScriptedAdapter, WORKFLOW, event_names, git, make_repo, read_journal


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


@pytest.mark.parametrize(("end_state", "exit_status"), [("capped", 0), ("failed", 1)])
def test_a_capped_or_failed_specialist_gets_a_second_invocation_in_the_same_worktree_and_keeps_its_first_commit(
    tmp_path, capsys, scripted, end_state, exit_status
):
    scripted.outcomes = [(end_state, exit_status), ("finished", 0)]
    scripted.continuation_files = [{"second.txt": "second\n"}]
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (first,) = scripted.calls
    (second,) = scripted.continuation_calls
    assert second.cwd == first.cwd
    branch = next(b for b in git(repo, "branch", "--format=%(refname:short)").split() if b.startswith("run/demo-"))
    # Oldest first: seed, the commit of the capped run, the commit of the continuation.
    shas = git(repo, "log", "--reverse", "--format=%H", branch).split()
    assert len(shas) == 3
    assert git(repo, "show", f"{shas[1]}:feature.txt") == "feature\n"
    assert git(repo, "show", f"{branch}:second.txt") == "second\n"
    assert git(repo, "show", f"{branch}:feature.txt") == "feature\n"


# The gate prints its reason in upper case, so the output is not a substring of the command in the brief.
GATE_THAT_SAYS_WHY = 'gates = ["test -f feature.txt || { echo feature.txt is absent | tr a-z A-Z; exit 1; }"]'
RED_GATE_WORKFLOW = WORKFLOW.replace('gates = ["test -f feature.txt"]', GATE_THAT_SAYS_WHY)


def branch_of(repo):
    return next(b for b in git(repo, "branch", "--format=%(refname:short)").split() if b.startswith("run/demo-"))


def test_a_specialist_with_red_gates_continues_in_the_same_worktree_and_the_journal_shows_red_then_green(tmp_path, capsys, scripted):
    scripted.files = {"other.txt": "no feature file\n"}  # the gate looks for feature.txt
    scripted.continuation_files = [{"feature.txt": "feature\n"}]
    repo = make_repo(tmp_path, RED_GATE_WORKFLOW)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (first,) = scripted.calls
    (second,) = scripted.continuation_calls
    assert second.cwd == first.cwd
    gates = [e for e in read_journal(out) if e["event"] == "gate-result" and e["phase"] == "build"]
    assert [g["green"] for g in gates] == [False, True]
    branch = branch_of(repo)
    assert git(repo, "show", f"{branch}:other.txt") == "no feature file\n"
    assert git(repo, "show", f"{branch}:feature.txt") == "feature\n"


def test_the_continuation_brief_of_an_adapter_without_resume_lists_the_commits_so_far_and_the_output_of_the_red_gates(tmp_path, capsys, scripted):
    scripted.files = {"other.txt": "no feature file\n"}
    scripted.continuation_files = [{"feature.txt": "feature\n"}]
    repo = make_repo(tmp_path, RED_GATE_WORKFLOW)

    code, out, err = run(repo, capsys)

    (second,) = scripted.continuation_calls
    first_commit = git(repo, "log", "--reverse", "--format=%H", f"main..{branch_of(repo)}").split()[0]
    assert second.resume_session is None
    assert first_commit in second.prompt
    assert "FEATURE.TXT IS ABSENT" in second.prompt
    # A new agent has no context: the brief holds the task and the report path again.
    assert "The export command writes a CSV file." in second.prompt
    assert str(second.report_path) in second.prompt


def test_an_adapter_that_supports_resume_receives_the_session_id_of_the_latest_result_and_a_brief_with_the_continuation_only(
    tmp_path, capsys, scripted
):
    scripted.supports_resume = True
    scripted.outcomes = [("capped", 0), ("capped", 0), ("finished", 0)]
    scripted.session_ids = ["sess-first", "sess-second", "sess-third"]
    scripted.continuation_files = [{"second.txt": "second\n"}, {"third.txt": "third\n"}]
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (first,) = scripted.calls
    second, third = scripted.continuation_calls
    assert first.resume_session is None
    assert second.resume_session == "sess-first"
    assert third.resume_session == "sess-second"
    first_commit = git(repo, "log", "--reverse", "--format=%H", f"main..{branch_of(repo)}").split()[0]
    # The resumed session keeps its first brief, so the prompt holds the continuation and not the full brief again.
    assert "## Continuation" in second.prompt and first_commit in second.prompt
    assert "## File boundary" not in second.prompt and "## Task" not in second.prompt
    continuations = [e for e in read_journal(out) if e["event"] == "continuation"]
    assert [(e["count"], e["mode"], e["resume_session"]) for e in continuations] == [
        (1, "resume", "sess-first"), (2, "resume", "sess-second"),
    ]


def test_an_adapter_that_supports_resume_gets_a_new_agent_when_the_harness_gave_no_session_id(tmp_path, capsys, scripted):
    scripted.supports_resume = True
    scripted.session_id = None
    scripted.outcomes = [("capped", 0), ("finished", 0)]
    scripted.continuation_files = [{"second.txt": "second\n"}]
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (second,) = scripted.continuation_calls
    assert second.resume_session is None
    assert "## File boundary" in second.prompt and "The export command writes a CSV file." in second.prompt
    (continuation,) = [e for e in read_journal(out) if e["event"] == "continuation"]
    assert (continuation["mode"], continuation["resume_session"]) == ("brief", None)


def limit_events(out):
    return [e for e in read_journal(out) if e["event"] == "continuation-limit"]


def test_in_assure_mode_a_specialist_that_always_caps_is_continued_twice_then_the_step_fails_with_the_count_in_the_journal(
    tmp_path, capsys, scripted
):
    scripted.end_state = "capped"
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert "Traceback" not in err
    assert len(scripted.calls) + len(scripted.continuation_calls) == 3  # the first run and two continuations
    assert scripted.verifier_calls == []
    (limit,) = limit_events(out)
    assert (limit["ticket"], limit["count"], limit["limit"], limit["trigger"]) == ("a", 2, 2, "capped")
    step_end = next(e for e in read_journal(out) if e["event"] == "step-end")
    assert step_end["state"] == "failed"
    assert "capped" in step_end["reason"] and "continuation limit of 2" in step_end["reason"]
    assert "continuation limit of 2" in err
    # The commit of the first run stays on the branch for the coordinator.
    assert git(repo, "show", f"{branch_of(repo)}:feature.txt") == "feature\n"


def test_in_economy_mode_the_limit_is_one_continuation(tmp_path, capsys, scripted):
    scripted.end_state = "failed"
    scripted.exit_status = 1
    repo = make_repo(tmp_path, WORKFLOW.replace('mode = "assure"', 'mode = "economy"'))

    code, out, err = run(repo, capsys)

    assert code == 1
    assert len(scripted.calls) + len(scripted.continuation_calls) == 2
    (limit,) = limit_events(out)
    assert (limit["count"], limit["limit"], limit["trigger"]) == (1, 1, "failed")
    assert "continuation limit of 1" in err


def test_gates_that_stay_red_after_the_limit_fail_the_step_with_the_red_gate_in_the_reason(tmp_path, capsys, scripted):
    scripted.files = {"other.txt": "no feature file\n"}  # the gate looks for feature.txt, and no continuation adds it
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert len(scripted.continuation_calls) == 2
    (limit,) = limit_events(out)
    assert (limit["count"], limit["trigger"]) == (2, "gates-red")
    step_end = next(e for e in read_journal(out) if e["event"] == "step-end")
    assert "test -f feature.txt" in step_end["reason"] and "continuation limit of 2" in step_end["reason"]


def test_one_count_covers_a_cap_and_a_red_gate(tmp_path, capsys, scripted):
    scripted.files = {"other.txt": "no feature file\n"}
    scripted.outcomes = [("capped", 0), ("finished", 0)]  # the cap, then red gates on both later runs
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert len(scripted.continuation_calls) == 2
    triggers = [e["trigger"] for e in read_journal(out) if e["event"] == "continuation"]
    assert triggers == ["capped", "gates-red"]
    assert limit_events(out)[0]["count"] == 2


def test_a_missing_report_fails_the_step_and_the_specialist_is_not_continued(tmp_path, capsys, scripted):
    scripted.write_report = False
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert code == 1
    assert scripted.continuation_calls == []
    assert "continuation" not in event_names(read_journal(out))


def test_a_continuation_uses_the_agent_and_the_model_of_the_first_run(tmp_path, capsys, scripted):
    scripted.outcomes = [("capped", 0), ("finished", 0)]
    scripted.continuation_files = [{"second.txt": "second\n"}]
    repo = make_repo(tmp_path)

    run(repo, capsys)

    (first,) = scripted.calls
    (second,) = scripted.continuation_calls
    assert (second.agent, second.model, second.report_path) == (first.agent, first.model, first.report_path)
