"""The run reference documents the journal, the specialist and verdict reports and the adapter interface.

The reference is outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

import json
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate
from delegate.run.domain import IDLE_TIMEOUT_SECONDS
from delegate.run.reports import (
    SPECIALIST_OPTIONAL, SPECIALIST_REQUIRED, SPECIALIST_STATUSES, VERIFIER_REQUIRED, VERIFIER_VERDICTS,
)

from .support import ScriptedAdapter, make_repo, read_journal, valid_report
from .pages import repository_root

ROOT = repository_root(Path(__file__))
REFERENCE = ROOT / "docs" / "reference" / "run.md"

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


@outside_the_package
@pytest.mark.parametrize(
    "name",
    [
        *SPECIALIST_REQUIRED, *SPECIALIST_OPTIONAL, *SPECIALIST_STATUSES,
        *VERIFIER_REQUIRED, *VERIFIER_VERDICTS, "criterion", "evidence",  # the verdict report
        "agent", "model", "prompt", "cwd", "report_path",  # the request of an adapter
        "exit_status", "end_state", "session_id", "event_stream",  # its result
        "finished", "failed", "capped", "tier_column",
    ],
)
def test_the_reference_names_each_report_field_and_adapter_field(name):
    assert f"`{name}`" in REFERENCE.read_text()


@outside_the_package
@pytest.mark.parametrize("verdicts", [pytest.param([], id="first-pass-accept"), pytest.param(["REJECT", "ACCEPT"], id="fixup-round")])
def test_the_reference_lists_every_event_of_a_real_journal_with_each_of_its_fields(tmp_path, capsys, verdicts):
    adapters.register("scripted", ScriptedAdapter(verdict_sequence=verdicts, verdict_findings=["feature.txt is not a CSV file."] if verdicts else None))
    try:
        repo = make_repo(tmp_path)
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    events = read_journal(capsys.readouterr().out)
    assert code == 0
    assert ("fixup-start" in [e["event"] for e in events]) == bool(verdicts)
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    for event in events:
        row = rows[f"`{event['event']}`"]
        for field in event:
            if field not in ("seq", "time", "event"):
                assert f"`{field}`" in row, (event["event"], field)


@outside_the_package
def test_the_reference_lists_the_skip_and_the_conflict_events_of_a_multi_ticket_run_with_each_of_their_fields(tmp_path, capsys, monkeypatch):
    from .test_run_multi import HEAD, ticket

    for key, value in (("NAME", "Scratch"), ("EMAIL", "scratch@example.invalid")):
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
    # Ticket a fails, b is skipped; c and d write the same file, so the rebase of d conflicts.
    scripted = ScriptedAdapter(
        files_by_ticket={"c": {"shared.txt": "c\n"}, "d": {"shared.txt": "d\n"}}, failing_tickets={"a"}
    )
    adapters.register("scripted", scripted)
    try:
        repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c") + ticket("d"))
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    events = read_journal(capsys.readouterr().out)
    assert {"skip", "rebase"} <= {e["event"] for e in events}
    assert {e["result"] for e in events if e["event"] == "rebase"} == {"rebased", "conflict"}
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    for event in events:
        row = rows[f"`{event['event']}`"]
        for field in event:
            if field not in ("seq", "time", "event"):
                assert f"`{field}`" in row, (event["event"], field)


@outside_the_package
@pytest.mark.parametrize("name", ["resume_session", "supports_resume", "continuation", "continuation-limit", "crashed: <exception type>: <message>"])
def test_the_reference_names_the_resume_field_and_the_continuation_events(name):
    assert f"`{name}`" in REFERENCE.read_text()


@outside_the_package
@pytest.mark.parametrize("name", ["idle_timeout", "report_valid", "ended_idle", "idle-timeout"])
def test_the_reference_names_the_idle_fields_of_the_adapter_interface_and_the_idle_event(name):
    assert f"`{name}`" in REFERENCE.read_text()


@outside_the_package
def test_the_reference_states_the_idle_timeout_of_the_engine():
    assert f"{IDLE_TIMEOUT_SECONDS} seconds" in REFERENCE.read_text()


@outside_the_package
def test_the_reference_lists_the_idle_timeout_event_of_a_real_journal_with_each_of_its_fields(tmp_path, capsys):
    adapters.register("scripted", ScriptedAdapter(idle_roles={"specialist"}))
    try:
        repo = make_repo(tmp_path)
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    events = read_journal(capsys.readouterr().out)
    assert code == 0
    (idle,) = [e for e in events if e["event"] == "idle-timeout"]
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    for field in idle:
        if field not in ("seq", "time", "event"):
            assert f"`{field}`" in rows["`idle-timeout`"], field


@outside_the_package
def test_the_reference_lists_the_continuation_events_of_a_real_journal_with_each_of_their_fields(tmp_path, capsys):
    # A resumed continuation, then a second one that reaches the limit of the mode.
    scripted = ScriptedAdapter(supports_resume=True, outcomes=[("capped", 0)], session_ids=["sess-1", "sess-2", "sess-3"])
    adapters.register("scripted", scripted)
    try:
        repo = make_repo(tmp_path)
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    events = read_journal(capsys.readouterr().out)
    assert {"continuation", "continuation-limit"} <= {e["event"] for e in events}
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    for event in events:
        row = rows[f"`{event['event']}`"]
        for field in event:
            if field not in ("seq", "time", "event"):
                assert f"`{field}`" in row, (event["event"], field)


@outside_the_package
def test_the_reference_names_the_trigger_of_a_continuation_for_a_report_that_is_not_valid(tmp_path, capsys):
    # The specialist commits its work, and each run writes a report with a field of the wrong type.
    scripted = ScriptedAdapter(report_text=lambda request, head: json.dumps(valid_report("a", "b", head, gates_green="yes")))
    adapters.register("scripted", scripted)
    try:
        repo = make_repo(tmp_path)
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    triggers = {e["trigger"] for e in read_journal(capsys.readouterr().out) if e["event"] in ("continuation", "continuation-limit")}
    assert triggers == {"report-invalid"}
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    assert "`report-invalid`" in rows["`continuation`"]


@outside_the_package
def test_the_reference_names_the_trigger_of_a_continuation_for_a_missing_report(tmp_path, capsys):
    # The specialist commits its work, and no run writes a report.
    scripted = ScriptedAdapter(write_report=False)
    adapters.register("scripted", scripted)
    try:
        repo = make_repo(tmp_path)
        delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    triggers = {e["trigger"] for e in read_journal(capsys.readouterr().out) if e["event"] in ("continuation", "continuation-limit")}
    assert triggers == {"report-missing"}
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    assert "`report-missing`" in rows["`continuation`"]


@outside_the_package
def test_the_reference_lists_the_resume_event_of_a_real_resumed_journal_with_each_of_its_fields(tmp_path, capsys, git_identity):
    from .support import kill_a_run, read_journal_file, resume_main

    adapters.register("scripted", ScriptedAdapter(files_by_ticket={"a": {"a.txt": "a\n"}, "b": {"b.txt": "b\n"}}))
    try:
        from .test_run_multi import HEAD, ticket

        repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))
        run_id, _ = kill_a_run(repo, 3)
        code, _, _ = resume_main(repo, capsys, run_id, "--break-lock")
    finally:
        adapters.unregister("scripted")
    assert code == 0
    events = read_journal_file(repo, run_id)
    assert "resume" in [e["event"] for e in events]
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    for event in events:
        for name in event:
            if name not in ("seq", "time", "event"):
                assert f"`{name}`" in rows[f"`{event['event']}`"], (event["event"], name)


@outside_the_package
def test_the_reference_lists_the_retry_event_of_a_real_retried_journal_with_each_of_its_fields(tmp_path, capsys, git_identity):
    from .support import read_journal_file, resume_main, run_main
    from .test_run_multi import HEAD, ticket

    # The first verifier ends with no report, so the step fails; the retry runs that verifier again.
    adapter = ScriptedAdapter(write_verifier_report=False)
    adapters.register("scripted", adapter)
    try:
        repo = make_repo(tmp_path, HEAD + ticket("a"))
        code, out, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
        run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
        adapter.write_verifier_report = True
        retried, _, _ = resume_main(repo, capsys, run_id, "--retry", "a")
    finally:
        adapters.unregister("scripted")
    assert (code, retried) == (1, 0)
    events = read_journal_file(repo, run_id)
    assert "retry" in [e["event"] for e in events]
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    for event in events:
        for name in event:
            if name not in ("seq", "time", "event"):
                assert f"`{name}`" in rows[f"`{event['event']}`"], (event["event"], name)


@outside_the_package
def test_the_reference_lists_the_lock_broken_event_of_a_real_journal_with_each_of_its_fields(tmp_path, capsys, git_identity):
    from .support import kill_a_run, read_journal_file, resume_main

    adapters.register("scripted", ScriptedAdapter(files_by_ticket={"a": {"a.txt": "a\n"}}))
    try:
        from .test_run_multi import HEAD, ticket

        repo = make_repo(tmp_path, HEAD + ticket("a"))
        run_id, _ = kill_a_run(repo, 1)
        code, _, _ = resume_main(repo, capsys, run_id, "--break-lock")
    finally:
        adapters.unregister("scripted")
    assert code == 0
    events = read_journal_file(repo, run_id)
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    broken = [e for e in events if e["event"] == "lock-broken"]
    assert len(broken) == 1
    for name in broken[0]:
        if name not in ("seq", "time", "event"):
            assert f"`{name}`" in rows["`lock-broken`"], name


@outside_the_package
def test_the_reference_lists_the_invariant_violation_event_of_a_real_halted_journal_with_each_of_its_fields(tmp_path, capsys, git_identity):
    scripted = ScriptedAdapter()
    adapters.register("scripted", scripted)
    try:
        repo = make_repo(tmp_path)

        def write_in_the_real_worktree(request):
            (run_dir,) = (repo / ".git" / "delegate" / "worktrees").iterdir()
            (run_dir / "a" / "oops.txt").write_text("by the verifier\n")

        scripted.during_verifier = write_in_the_real_worktree
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    events = read_journal(capsys.readouterr().out)
    assert code == 1
    assert "invariant-violation" in [e["event"] for e in events]
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    for event in events:
        row = rows[f"`{event['event']}`"]
        for field in event:
            if field not in ("seq", "time", "event"):
                assert f"`{field}`" in row, (event["event"], field)
    assert "invariant violation: the verifier changed the real worktree" in REFERENCE.read_text()
