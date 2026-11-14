"""The journal calls the observers that it got at its start, and the engine gives the observers of its caller to the journal (#332).

The journal is append-only: nothing sets a field of a journal from outside. A run gets its observers as a parameter of
`run_workflow`, and each observer gets the journal and the name of each event after the journal wrote it. Each case uses a real
journal file and a real temporary git repository with the scripted adapter.
"""

from __future__ import annotations

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.adapters.shell import ShellGateRunner
from delegate.run.engine import run_workflow
from delegate.run.journal import Journal, read_events
from delegate.run.workflow import load_workflow
from delegate.shared.tiers import load_tiers

from ..support import ScriptedAdapter, WORKFLOW, make_repo


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def test_a_journal_calls_each_observer_that_it_got_at_its_start_after_each_event_that_it_wrote(tmp_path):
    first: list[tuple[str, list[str]]] = []
    second: list[str] = []
    journal = Journal(
        tmp_path / "journal.jsonl",
        observers=[lambda j, event: first.append((event, [e["event"] for e in j.events])), lambda j, event: second.append(event)],
    )

    journal.append("run-start", run_id="r")
    journal.append("step-start", ticket="a")

    assert first == [("run-start", ["run-start"]), ("step-start", ["run-start", "step-start"])]
    assert second == ["run-start", "step-start"]


def test_a_reopened_journal_calls_the_observers_that_it_got_at_its_reopen(tmp_path):
    path = tmp_path / "journal.jsonl"
    Journal(path).append("run-start", run_id="r")
    seen: list[str] = []

    journal, _ = Journal.reopen(path, observers=[lambda j, event: seen.append(event)])
    journal.append("resume", run_id="r")

    assert seen == ["resume"]


def test_a_journal_that_got_no_observer_appends_events_and_calls_nothing(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")

    journal.append("run-start", run_id="r")

    assert [e["event"] for e in read_events(journal.path)] == ["run-start"]


def test_a_run_gives_the_observers_of_its_caller_the_name_of_each_event_that_its_journal_wrote(tmp_path, scripted):
    repo = make_repo(tmp_path, WORKFLOW)
    tiers = load_tiers()
    path = repo / "workflow.toml"
    workflow = load_workflow(path, tiers, adapters.registered_names())
    seen: list[str] = []

    result = run_workflow(
        workflow, path, repo, tiers, scripted, GitVersionControl(), ShellGateRunner(), observers=[lambda journal, event: seen.append(event)],
    )

    written = [e["event"] for e in read_events(result.journal)]
    assert seen == written
    assert written[0] == "run-start" and written[-1] == "run-end"
