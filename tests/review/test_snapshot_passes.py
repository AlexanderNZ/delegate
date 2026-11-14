"""`snapshot_at` and `snapshot_of` read the events of a run in a number of passes that does not grow with the tickets (#324).

The cost of a snapshot is a count, not a time, so no case is timed: the events are a list that counts each pass over
them (each `iter` of the list), and a case compares the passes for a journal of 2 tickets with the passes for a journal of
100 tickets. A pass for each ticket makes the second number larger than the first.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from delegate.run import status

from ..run_state.journals import built, ev, journal, started

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
JOURNAL_FILE = Path("/state/runs/20261114T090000Z-test/journal.jsonl")


class CountedEvents(list):
    """The events of a journal, with the number of passes that a reader made over them."""

    passes = 0

    def __iter__(self):
        self.passes += 1
        return super().__iter__()


def run_of(tickets: int) -> CountedEvents:
    """The journal of a run that built `tickets` tickets and runs one more: each ticket has a gate and a step."""
    names = tuple(f"t{number}" for number in range(tickets + 1))
    items = []
    for name in names[:-1]:
        items += [
            started(name), ev("gate-start", ticket=name, phase="ticket", command="make test", gate_log="/l", name="tests"),
            ev("gate-result", ticket=name, phase="ticket", command="make test", exit_code=0, passed=True), built(name),
        ]
    items.append(started(names[-1]))
    return CountedEvents(journal(*items, tickets=names))


def passes_of_snapshot_at(tickets: int) -> int:
    events = run_of(tickets)
    status.snapshot_at(events, JOURNAL_FILE, lambda adapter: None, NOW, live_process=True, pause_request=False)
    return events.passes


def test_snapshot_at_reads_the_events_in_as_many_passes_for_100_tickets_as_for_2():
    assert passes_of_snapshot_at(100) == passes_of_snapshot_at(2)


def test_snapshot_of_folds_the_journal_once(tmp_path):
    """The lock question and the snapshot share one fold: `snapshot_of` makes the passes of `snapshot_at`, and no more."""
    journal_file = tmp_path / "runs" / "20261114T090000Z-test" / "journal.jsonl"
    journal_file.parent.mkdir(parents=True)
    journal_file.touch()
    events = run_of(100)
    status.snapshot_of(events, journal_file, lambda adapter: None, NOW)
    assert events.passes == passes_of_snapshot_at(100)
