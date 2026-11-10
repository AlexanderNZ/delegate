"""A run checks at its start that the verifier of each stack permits each gate of the stack (#296).

The dry run asks the verifier guard about each gate (`Adapter.permits`). A real run did not: a verifier that cannot run a
gate failed late, in its session, and the run showed a REJECT, not the cause. Now the engine asks, after the seal and
before the first agent session, with the same text as the dry run. A gate that the guard does not permit stops the run
with no `step-start`, and the journal holds one event for the stop, so `delegate status` shows the cause.

Each case calls `delegate.main` against a real temporary git repository. The scripted adapter takes the place of the
harness.
"""

from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, event_names, make_repo, read_journal, resume_main, run_main

pytestmark = pytest.mark.usefixtures("git_identity", "home")

GATE = "test -f feature.txt"
REASON = "the guard has no rule for this command"


def write_agents(repo: Path) -> None:
    directory = repo / ".claude" / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("python-specialist", "python-verifier"):
        (directory / f"{name}.md").write_text(f"---\nname: {name}\n---\nThe rules of {name}.\n")
    (repo / ".git" / "info" / "exclude").write_text(".claude/\n")


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(checks_setup=True)
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def start(repo: Path, capsys) -> tuple[int, str, str, list[dict[str, object]]]:
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return code, out, err, read_journal(out)


def test_a_run_whose_verifier_does_not_permit_a_gate_stops_before_the_first_agent_session_with_the_message_of_the_dry_run(
    tmp_path, scripted, capsys
):
    repo = make_repo(tmp_path)
    write_agents(repo)
    scripted.denials = {GATE: REASON}
    dry_code, dry_out, dry_err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    code, out, err, events = start(repo, capsys)

    assert dry_code == 1
    assert code == 1
    assert dry_err.strip() in err  # the message of the dry run, word for word
    assert f"the gate {GATE!r}" in err and REASON in err and "stack python" in err
    assert scripted.calls == [] and scripted.verifier_calls == []
    assert "step-start" not in event_names(events) and "agent-start" not in event_names(events)
    assert event_names(events).count("setup-problem") == 1


def test_a_run_whose_verifier_permits_each_gate_goes_on(tmp_path, scripted, capsys):
    repo = make_repo(tmp_path)
    write_agents(repo)

    code, out, err, events = start(repo, capsys)

    assert (code, err) == (0, "")
    assert "setup-problem" not in event_names(events)
    assert len(scripted.calls) == 1 and len(scripted.verifier_calls) == 1


def test_an_adapter_with_no_check_of_the_setup_skips_the_check(tmp_path, scripted, capsys):
    repo = make_repo(tmp_path)
    write_agents(repo)
    scripted.checks_setup = False
    scripted.denials = {GATE: REASON}

    code, out, err, events = start(repo, capsys)

    assert (code, err) == (0, "")
    assert len(scripted.calls) == 1


def test_status_of_a_run_that_the_setup_check_stopped_shows_the_cause_and_the_resume_checks_again(tmp_path, scripted, capsys):
    repo = make_repo(tmp_path)
    write_agents(repo)
    scripted.denials = {GATE: REASON}
    code, out, err, events = start(repo, capsys)
    run_id = str(events[0]["run_id"])

    status_code = delegate.main(["status", "--repo", str(repo), run_id])
    status_out = capsys.readouterr().out
    resume_code, resume_out, resume_err = resume_main(repo, capsys, run_id)

    assert status_code == 0
    assert "stopped" in status_out.splitlines()[0]
    assert f"the gate {GATE!r}" in status_out and REASON in status_out
    assert resume_code == 1 and REASON in resume_err
    assert scripted.calls == []

    scripted.denials = {}
    fixed_code, fixed_out, fixed_err = resume_main(repo, capsys, run_id)

    assert (fixed_code, fixed_err) == (0, "")
    assert len(scripted.calls) == 1
