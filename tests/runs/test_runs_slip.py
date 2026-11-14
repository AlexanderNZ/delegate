"""`delegate runs`: the column `slip`, how much later the ETA of a run is now than it was 2 hours ago (#311).

The clock of each case is `NOW`, 14:20, so the ETA of 2 hours ago is the ETA at 12:20. A case writes the journal of one
run with `journals.write_run`, calls `main(argv)`, and reads the table and `--json`. The ETA of a run is its time plus
the mean time of its tickets that ended, times the tickets that did not end, minus the time that the running ticket has
used (`delegate status`). Each case gives the arithmetic of its two ETAs in its text, worked by hand from that rule.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from .journals import built, clock_time, runs, working, write_run


def at(hours: int, minutes: int, seconds: int = 0) -> datetime:
    return clock_time(hours, minutes) + timedelta(seconds=seconds)


def run_of(root, started: datetime, tickets: int, events: list) -> None:
    """The journal of a run with `tickets` tickets that works now (a process holds its lock), with its events in the order of their times."""
    write_run(root, "alpha", "aaaa", started, sorted(events, key=lambda event: event[0]), tickets=tuple(str(n) for n in range(1, tickets + 1)), lock=True)


def slip_of(capsys) -> object:
    code, out, err = runs(capsys, "--json")
    assert (code, err) == (0, "")
    return json.loads(out)["runs"][0]["slip_seconds"]


def row(capsys) -> str:
    code, out, err = runs(capsys)
    assert (code, err) == (0, "")
    return out.splitlines()[-1]


def moved_55_minutes(root) -> None:
    """Four tickets from 10:00. Ticket 1 takes 60m, ticket 2 takes 59m30s, ticket 3 starts at 12:00 and takes 101m, ticket 4 starts when ticket 3 ends.

    At 12:20 the mean of the 2 tickets that ended is 59m45s, the 2 tickets that did not end give 119m30s, and ticket 3 used
    20m: the ETA is 12:20 + 99m30s = 13:59:30. At 14:20 ticket 3 ended at 13:41, so the mean of the 3 is 73m30s, 1 ticket did not
    end, and ticket 4 used 39m: the ETA is 14:20 + 34m30s = 14:54:30. The ETA moved 55m later.
    """
    run_of(root, at(10, 0), 4, [
        *built("1", at(10, 0), at(11, 0)), *built("2", at(11, 0), at(11, 59, 30)),
        *built("3", at(12, 0), at(13, 41)), working("4", at(13, 41)),
    ])


def test_an_eta_that_moved_55_minutes_later_in_the_last_2_hours_shows_plus_55m(machine, capsys):
    moved_55_minutes(machine)

    line = row(capsys)

    assert line.split()[:7] == ["│", "alpha", "aaaa", "running", "3/4", "14:54", "+55m"]
    assert slip_of(capsys) == 3300


def test_a_slip_of_20_minutes_is_blank_in_the_table_and_null_in_json(machine, capsys):
    """Five tickets from 10:00, each of 60m except ticket 3 of 76m. At 12:20 the mean is 60m, 3 tickets did not end, and ticket 3 used
    20m: the ETA is 12:20 + 160m = 15:00. At 14:20 the mean of the 4 that ended is 64m, ticket 5 (from 14:16) used 4m, and 1 ticket
    did not end: the ETA is 14:20 + 60m = 15:20. The slip is 20m, less than 30m."""
    run_of(machine, at(10, 0), 5, [
        *built("1", at(10, 0), at(11, 0)), *built("2", at(11, 0), at(12, 0)), *built("3", at(12, 0), at(13, 16)),
        *built("4", at(13, 16), at(14, 16)), working("5", at(14, 16)),
    ])

    line = row(capsys)

    assert "15:20" in line and "+" not in line
    assert slip_of(capsys) is None


def test_a_run_that_started_1_hour_ago_has_a_blank_slip_cell(machine, capsys):
    """Four tickets from 13:20: ticket 1 takes 30m, ticket 2 runs from 13:50. The mean is 30m, 3 tickets did not end, and ticket 2 used 30m: the ETA is 14:20 + (90m - 30m) = 15:20.
    2 hours ago the run did not exist."""
    run_of(machine, at(13, 20), 4, [*built("1", at(13, 20), at(13, 50)), working("2", at(13, 50))])

    line = row(capsys)

    assert "15:20" in line and "+" not in line
    assert slip_of(capsys) is None


def test_a_run_with_no_eta_two_hours_ago_has_a_blank_slip_cell(machine, capsys):
    """Ticket 1 started at 10:00 and ended at 12:30, after the 2 hours ago. At 12:20 no ticket had ended, so the run had no ETA."""
    run_of(machine, at(10, 0), 3, [*built("1", at(10, 0), at(12, 30)), working("2", at(12, 30))])

    assert slip_of(capsys) is None
    assert "+" not in row(capsys)


def test_a_run_that_is_not_running_has_a_blank_slip_cell(machine, capsys):
    """The run of the 55 minutes case with no process that holds its lock is stopped, and a stopped run has no ETA."""
    started = at(10, 0)
    write_run(machine, "alpha", "aaaa", started, sorted([
        *built("1", at(10, 0), at(11, 0)), *built("2", at(11, 0), at(11, 59, 30)),
        *built("3", at(12, 0), at(13, 41)), working("4", at(13, 41)),
    ], key=lambda event: event[0]), tickets=("1", "2", "3", "4"))

    assert slip_of(capsys) is None
    assert "+" not in row(capsys)


def test_a_slip_of_exactly_30_minutes_shows_plus_30m(machine, capsys):
    """Four tickets from 10:00. Tickets 1 and 2 take 60m each, ticket 3 starts at 12:00 and takes 82m30s, ticket 4 starts when it ends.

    At 12:20 the mean is 60m, 2 tickets did not end, and ticket 3 used 20m: the ETA is 12:20 + 100m = 14:00. At 14:20 the mean of
    the 3 is 67m30s, 1 ticket did not end, and ticket 4 (from 13:22:30) used 57m30s: the ETA is 14:20 + 10m = 14:30.
    """
    run_of(machine, at(10, 0), 4, [
        *built("1", at(10, 0), at(11, 0)), *built("2", at(11, 0), at(12, 0)),
        *built("3", at(12, 0), at(13, 22, 30)), working("4", at(13, 22, 30)),
    ])

    assert row(capsys).split()[:7] == ["│", "alpha", "aaaa", "running", "3/4", "14:30", "+30m"]
    assert slip_of(capsys) == 1800


def test_a_slip_of_an_hour_or_more_shows_hours_and_minutes(machine, capsys):
    """As the 55 minutes case, with ticket 3 taking 117m30s (to 13:57:30): the mean of the 3 is 79m, ticket 4 used 22m30s, and the ETA is
    14:20 + 56m30s = 15:16:30. The ETA of 12:20 is 13:59:30, so the slip is 77m, which is 1h17m."""
    run_of(machine, at(10, 0), 4, [
        *built("1", at(10, 0), at(11, 0)), *built("2", at(11, 0), at(11, 59, 30)),
        *built("3", at(12, 0), at(13, 57, 30)), working("4", at(13, 57, 30)),
    ])

    assert row(capsys).split()[:7] == ["│", "alpha", "aaaa", "running", "3/4", "15:16", "+1h17m"]
    assert slip_of(capsys) == 4620
