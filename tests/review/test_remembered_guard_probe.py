"""A denied guard probe is kept on the machine, so that a resume or a dry run does not start a model session again (#346).

The guard probe (#219) starts one harness session for each verifier at each start, each resume and each dry run. Its result
depends only on the sealed agent and on the harness. So the engine keeps a denied probe in the state root, by the sha256 of
the seal of the verifier and by the version of the harness, for 24 hours. A probe with the same key in that time starts no
session: the journal records `denied` with the source `remembered` and the time of the kept result.

Each case calls `delegate.main` against a real temporary git repository. The scripted adapter, or a stand-in for the `claude`
command, takes the place of the harness. The `clock` fixture gives the wall time of the commands.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from delegate import adapters

from .. import claude_fake
from ..support import (
    WORKFLOW, ScriptedAdapter, event_names, make_repo, read_journal, read_journal_file, resume_main, run_main, write_project_agents,
)

pytestmark = pytest.mark.usefixtures("git_identity", "home")

# The wall time of the first probe of each case, and its text in the journal.
FIRST = datetime(2026, 11, 15, tzinfo=timezone.utc).timestamp()
FIRST_TEXT = "2026-11-15T00:00:00+00:00"
HOUR = 3600.0


def write_agents(repo: Path, verifier_rules: str = "The rules of python-verifier.") -> None:
    directory = repo / ".claude" / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "python-specialist.md").write_text("---\nname: python-specialist\n---\nThe rules of python-specialist.\n")
    (directory / "python-verifier.md").write_text(f"---\nname: python-verifier\n---\n{verifier_rules}\n")
    (repo / ".git" / "info" / "exclude").write_text(".claude/\n")


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(probes_guard=True, checks_setup=True, version="2.1.300")
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def repo_of(tmp_path: Path) -> Path:
    repo = make_repo(tmp_path, WORKFLOW)
    write_agents(repo)
    return repo


def dry_run(repo: Path, capsys) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")


def start(repo: Path, capsys) -> tuple[int, str, list[dict[str, object]]]:
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return code, err, read_journal(out)


def probes(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [e for e in events if e["event"] == "guard-probe"]


@pytest.mark.parametrize("later", [60.0, 23 * HOUR], ids=["one minute later", "23 hours later"])
def test_a_dry_run_and_then_a_run_with_the_same_seal_and_harness_start_one_probe_session_and_the_run_records_remembered(
    tmp_path, scripted, clock, capsys, later
):
    repo = repo_of(tmp_path)
    clock.at = FIRST
    assert dry_run(repo, capsys)[0] == 0
    clock.at = FIRST + later

    code, err, events = start(repo, capsys)

    assert (code, err) == (0, "")
    assert len(scripted.probe_calls) == 1
    (probe,) = probes(events)
    assert (probe["agent"], probe["result"], probe["source"], probe["kept_time"]) == ("python-verifier", "denied", "remembered", FIRST_TEXT)
    assert len(scripted.calls) == 1 and len(scripted.verifier_calls) == 1


def change_the_seal(repo: Path, scripted: ScriptedAdapter, clock) -> None:
    write_agents(repo, "The new rules of python-verifier.")


def change_the_harness(repo: Path, scripted: ScriptedAdapter, clock) -> None:
    scripted.version = "2.1.301"


def wait_25_hours(repo: Path, scripted: ScriptedAdapter, clock) -> None:
    clock.at = FIRST + 25 * HOUR


def no_harness_version(repo: Path, scripted: ScriptedAdapter, clock) -> None:
    scripted.version = None


@pytest.mark.parametrize(
    "change", [change_the_seal, change_the_harness, wait_25_hours, no_harness_version],
    ids=["a changed seal", "a changed harness version", "a kept result of 25 hours ago", "an adapter with no harness version"],
)
def test_a_changed_seal_a_changed_harness_version_and_a_kept_result_of_25_hours_ago_each_start_a_probe(tmp_path, scripted, clock, capsys, change):
    repo = repo_of(tmp_path)
    clock.at = FIRST
    if change is no_harness_version:
        change(repo, scripted, clock)
    assert dry_run(repo, capsys)[0] == 0
    clock.at = FIRST + 60
    change(repo, scripted, clock)

    code, err, events = start(repo, capsys)

    assert (code, err) == (0, "")
    assert len(scripted.probe_calls) == 2
    (probe,) = probes(events)
    assert probe["result"] == "denied" and "source" not in probe and Path(str(probe["event_stream"])).is_file()


@pytest.mark.parametrize("result", ["ran", "inconclusive"])
def test_a_probe_that_is_not_denied_is_not_kept(tmp_path, scripted, clock, capsys, result):
    repo = repo_of(tmp_path)
    clock.at = FIRST
    scripted.probe_outcomes = {"python-verifier": result}
    assert dry_run(repo, capsys)[0] == 1
    scripted.probe_outcomes = {}
    sessions = len(scripted.probe_calls)  # an inconclusive probe with no call of the command runs a second try (#299)

    code, err, events = start(repo, capsys)

    assert (code, err) == (0, "")
    assert len(scripted.probe_calls) == sessions + 1
    (probe,) = probes(events)
    assert probe["result"] == "denied" and "source" not in probe


def test_a_resume_that_seals_again_remembers_the_denied_probe_of_the_start_and_starts_no_probe_session(tmp_path, scripted, clock, capsys):
    repo = repo_of(tmp_path)
    clock.at = FIRST

    def pause(request) -> None:
        (request.report_path.parent.parent / "pause-request").write_text('{"time": "2026-11-15T00:00:00+00:00", "pid": 1}\n')

    scripted.on_specialist = pause
    code, err, events = start(repo, capsys)
    assert event_names(events)[-1] == "paused", (code, err)
    scripted.on_specialist = None
    run_id = str(events[0]["run_id"])
    clock.at = FIRST + HOUR

    code, out, err = resume_main(repo, capsys, run_id, "--agents-changed")

    assert code == 0, err
    assert len(scripted.probe_calls) == 1
    after = probes(read_journal_file(repo, run_id))
    assert [(p["result"], p.get("source"), p.get("kept_time")) for p in after] == [("denied", None, None), ("denied", "remembered", FIRST_TEXT)]


def test_a_second_dry_run_names_the_kept_result_and_starts_no_probe_session(tmp_path, scripted, clock, capsys):
    repo = repo_of(tmp_path)
    clock.at = FIRST
    assert dry_run(repo, capsys)[0] == 0
    clock.at = FIRST + 60

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    assert f"stack python: guard probe denied (remembered from {FIRST_TEXT})" in out.splitlines()
    assert len(scripted.probe_calls) == 1


def test_the_claude_code_adapter_keeps_a_denied_probe_until_a_new_claude_gives_a_new_version(tmp_path, monkeypatch, clock, capsys):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    write_project_agents(repo)
    clock.at = FIRST

    first = dry_run(repo, capsys)
    second = dry_run(repo, capsys)
    # An update of Claude Code replaces the `claude` file, and the new file gives a new version.
    executable = tmp_path / "fake-bin" / "claude"
    executable.write_text(executable.read_text() + "# the updated harness\n")
    monkeypatch.setenv("FAKE_HARNESS_VERSION", "2.1.288 (Claude Code)")
    third = dry_run(repo, capsys)

    assert [code for code, _, _ in (first, second, third)] == [0, 0, 0]
    assert "stack python: guard probe denied" in first[1].splitlines()
    assert f"stack python: guard probe denied (remembered from {FIRST_TEXT})" in second[1].splitlines()
    assert "stack python: guard probe denied" in third[1].splitlines()
    assert len(fake.probe_calls()) == 2
