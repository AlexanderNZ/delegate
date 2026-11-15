"""The adapters read only the new bytes of the stream of a live agent (#352).

A stream that grows gives the same totals and the same time of the last activity as a full read, and the adapter parses
only the bytes after the last read. The tests show that with a stream whose old bytes are damaged after the first read:
a full read would raise on them, so a result that has no error and the right totals means that the adapter did not read them again.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from delegate import adapters
from delegate.ports.harness import Activity, AdapterError

# Longer than the first bytes that the adapters compare, so that a change after them is not a change of the start.
PADDING = "p" * 600


@dataclass(frozen=True)
class Harness:
    name: str
    start: str  # the first event: it holds the padding
    event: Callable[[int], str]  # the event of step n, a line with no newline
    tokens: Callable[[int], tuple[int, int]]  # the (new, cache) tokens of the event of step n
    time: Callable[[int], datetime]  # the time of the event of step n


def claude_event(step: int) -> str:
    usage = {"input_tokens": step, "cache_creation_input_tokens": 0, "output_tokens": 2 * step, "cache_read_input_tokens": 100 * step}
    return json.dumps({
        "type": "assistant", "message": {"id": f"msg_{step}", "usage": usage}, "timestamp": f"2026-10-08T19:55:{step:02d}.000Z",
    })


def opencode_event(step: int) -> str:
    tokens = {"input": step, "output": 2 * step, "reasoning": 0, "cache": {"write": 0, "read": 100 * step}}
    return json.dumps({"type": "step_finish", "timestamp": (1791489300 + step) * 1000, "part": {"tokens": tokens}})


def at(step: int) -> datetime:
    return datetime(2026, 10, 8, 19, 55, step, tzinfo=timezone.utc)


CLAUDE = Harness(
    "claude-code", json.dumps({"type": "system", "subtype": "init", "note": PADDING}), claude_event,
    lambda step: (3 * step, 100 * step), at,
)
OPENCODE = Harness(
    "opencode", json.dumps({"type": "step_start", "note": PADDING}), opencode_event,
    lambda step: (3 * step, 100 * step), lambda step: datetime.fromtimestamp(1791489300 + step, timezone.utc),
)
HARNESSES = [pytest.param(CLAUDE, id="claude-code"), pytest.param(OPENCODE, id="opencode")]


def stream_of(harness: Harness, steps: list[int]) -> str:
    return "".join(line + "\n" for line in [harness.start] + [harness.event(step) for step in steps])


def expected(harness: Harness, steps: list[int]) -> Activity:
    """The totals of the steps, worked out by hand: each step n adds 3n new tokens and 100n cache reads."""
    return Activity(sum(harness.tokens(s)[0] for s in steps), sum(harness.tokens(s)[1] for s in steps), harness.time(steps[-1]))


def damage_line(stream: Path, number: int) -> None:
    """Overwrite the line `number` (1 is the first) with `x` bytes of the same length, so the file keeps its size and its start."""
    lines = stream.read_bytes().split(b"\n")
    lines[number - 1] = b"x" * len(lines[number - 1])
    stream.write_bytes(b"\n".join(lines))


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_stream_that_grows_by_one_event_is_parsed_for_that_event_only_and_gives_the_totals_of_a_full_read(tmp_path, harness):
    adapter = adapters.get(harness.name)
    live = tmp_path / "live.jsonl"
    live.write_text(stream_of(harness, [1, 2]))
    assert adapter.activity(live) == expected(harness, [1, 2])

    damage_line(live, 2)  # an old event that a full read would meet again, and could not parse
    with live.open("a") as out:
        out.write(harness.event(3) + "\n")

    grown = adapter.activity(live)

    clean = tmp_path / "clean.jsonl"
    clean.write_text(stream_of(harness, [1, 2, 3]))
    assert grown == adapters.get(harness.name).activity(clean)  # a fresh adapter reads the clean file in full
    assert grown == expected(harness, [1, 2, 3])


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_stream_that_is_replaced_by_a_shorter_file_is_read_again_from_the_start(tmp_path, harness):
    adapter = adapters.get(harness.name)
    live = tmp_path / "live.jsonl"
    live.write_text(stream_of(harness, [1, 2, 3, 4]))
    assert adapter.activity(live) == expected(harness, [1, 2, 3, 4])

    live.write_text(stream_of(harness, [5]))

    assert adapter.activity(live) == expected(harness, [5])


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_stream_that_is_replaced_by_a_longer_file_with_another_start_is_read_again_from_the_start(tmp_path, harness):
    adapter = adapters.get(harness.name)
    live = tmp_path / "live.jsonl"
    live.write_text(stream_of(harness, [1]))
    assert adapter.activity(live) == expected(harness, [1])

    live.write_text(stream_of(harness, [6, 7, 8]).replace(PADDING, "q" * 600))

    assert adapter.activity(live) == expected(harness, [6, 7, 8])


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_half_line_at_the_end_counts_when_it_is_complete_json_and_is_read_whole_when_the_rest_comes(tmp_path, harness):
    adapter = adapters.get(harness.name)
    live = tmp_path / "live.jsonl"
    line = harness.event(2)
    live.write_text(stream_of(harness, [1]) + line[:20])
    assert adapter.activity(live) == expected(harness, [1])  # half a line is not an event

    live.write_text(stream_of(harness, [1]) + line)  # the whole line, with no newline yet
    assert adapter.activity(live) == expected(harness, [1, 2])

    live.write_text(stream_of(harness, [1, 2]) + harness.event(3)[:30])  # the newline came, and half of the next line
    assert adapter.activity(live) == expected(harness, [1, 2])

    live.write_text(stream_of(harness, [1, 2, 3]))
    assert adapter.activity(live) == expected(harness, [1, 2, 3])


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_bad_line_that_is_no_longer_the_last_line_raises_and_names_the_line(tmp_path, harness):
    adapter = adapters.get(harness.name)
    live = tmp_path / "live.jsonl"
    live.write_text(stream_of(harness, [1]) + "this is not json\n")
    assert adapter.activity(live) == expected(harness, [1])  # the last line may be torn: it is dropped

    with live.open("a") as out:
        out.write(harness.event(2) + "\n")

    with pytest.raises(AdapterError, match="line 3 is not JSON"):
        adapter.activity(live)


@pytest.mark.parametrize("harness", HARNESSES)
def test_a_stream_that_is_removed_raises_and_names_the_path_also_after_a_read(tmp_path, harness):
    adapter = adapters.get(harness.name)
    live = tmp_path / "live.jsonl"
    live.write_text(stream_of(harness, [1]))
    adapter.activity(live)
    live.unlink()

    with pytest.raises(AdapterError, match="not found"):
        adapter.activity(live)


def test_the_time_of_a_heartbeat_at_the_end_of_a_claude_stream_follows_the_file_as_the_stream_grows(tmp_path):
    adapter = adapters.get("claude-code")
    live = tmp_path / "live.jsonl"
    heartbeat = json.dumps({"type": "tool_progress"}) + "\n"
    live.write_text(stream_of(CLAUDE, [1]) + heartbeat)
    os.utime(live, (1_700_000_000, 1_700_000_000))
    assert adapter.activity(live).last_event == datetime.fromtimestamp(1_700_000_000, timezone.utc)

    with live.open("a") as out:
        out.write(claude_event(2) + "\n")  # an event with a time ends the stream: its time wins over the file
    assert adapter.activity(live).last_event == at(2)

    with live.open("a") as out:
        out.write(heartbeat)
    os.utime(live, (1_700_000_500, 1_700_000_500))
    assert adapter.activity(live).last_event == datetime.fromtimestamp(1_700_000_500, timezone.utc)
