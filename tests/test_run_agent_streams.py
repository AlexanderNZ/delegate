"""The journal names the event stream of each agent before the agent starts.

A case runs `delegate run` end to end with the scripted adapter. The adapter
reads the journal at the start of each call, so the case sees what a
`delegate status` in another process sees while the agent runs.
"""

import json
from pathlib import Path

import pytest

from delegate import adapters

from .support import ScriptedAdapter, make_repo, run_main


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def journal_now(repo: Path) -> list[dict[str, object]]:
    (journal,) = (repo / ".git" / "delegate" / "runs").glob("*/journal.jsonl")
    return [json.loads(line) for line in journal.read_text().splitlines()]


def record_the_newest_event(scripted, repo: Path) -> list[tuple[dict[str, object], Path]]:
    """Make the adapter keep the newest journal event and the stream of the request at the start of each call."""
    seen: list[tuple[dict[str, object], Path]] = []
    hook = lambda request: seen.append((journal_now(repo)[-1], request.event_stream))  # noqa: E731
    scripted.on_specialist = hook
    scripted.during_verifier = hook
    return seen


def test_the_specialist_the_verifier_and_the_fixup_specialist_each_have_their_stream_in_the_journal_before_they_start(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    repo = make_repo(tmp_path)
    seen = record_the_newest_event(scripted, repo)

    code, _, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    newest = [(event["event"], event["ticket"], event["role"], event["round"], event["event_stream"]) for event, _ in seen]
    assert newest == [
        ("agent-start", "a", "specialist", 0, str(seen[0][1])),
        ("agent-start", "a", "verifier", 0, str(seen[1][1])),
        ("agent-start", "a", "fix-up", 1, str(seen[2][1])),
        ("agent-start", "a", "verifier", 1, str(seen[3][1])),
    ]
    assert len({stream for _, stream in seen}) == 4


def test_each_stream_that_the_journal_names_at_the_start_holds_the_events_of_its_agent_after_the_run(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    repo = make_repo(tmp_path)

    code, _, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    events = journal_now(repo)
    started = [event["event_stream"] for event in events if event["event"] == "agent-start"]
    ended = [event["event_stream"] for event in events if event["event"] in ("adapter-result", "verify-result", "fixup-result")]
    assert started == ended
    assert all(Path(str(stream)).read_text() == '{"type":"result"}\n' for stream in started)


def test_a_continuation_gets_a_new_stream_and_keeps_the_stream_of_the_first_run(tmp_path, capsys, scripted):
    scripted.outcomes = [("capped", 0), ("finished", 0)]
    scripted.continuation_files = [{"second.txt": "second\n"}]
    repo = make_repo(tmp_path)

    code, _, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    first, second = [Path(str(e["event_stream"])) for e in journal_now(repo) if e["event"] == "agent-start" and e["role"] == "specialist"]
    assert first != second
    assert first.is_file() and second.is_file()
