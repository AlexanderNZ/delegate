"""The status module is split by concern, and no part holds 700 lines or more (#335).

`run/status.py` held the snapshot of a run, the table of the live view, and the picker of a run. The snapshot stays in
`status`, its values are in `status_model`, the table is in `status_table`, and the plan links and the picker are in
`status_picker`. The split moves code and changes no output, so the cases here check the size of each part, and that
each part still draws what it drew.
"""

from __future__ import annotations

from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path

import pytest

from delegate.run import status

from ..run_state.journals import journal, started

RUN_DIR = Path(status.__file__).parent
PARTS = ("status", "status_model", "status_table", "status_picker")
NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)
JOURNAL_FILE = Path("/state/runs/20261114T090000Z-test/journal.jsonl")


@pytest.mark.parametrize("part", PARTS)
def test_each_part_of_the_status_code_has_fewer_than_700_lines(part):
    lines = (RUN_DIR / f"{part}.py").read_text(encoding="utf-8").splitlines()

    assert len(lines) < 700


def a_running_run() -> list[dict[str, object]]:
    return journal(started("a"), tickets=("a", "b"))


def test_the_table_of_the_live_view_is_drawn_by_the_table_module():
    table = import_module("delegate.run.status_table")
    picker = import_module("delegate.run.status_picker")
    events = a_running_run()
    snap = status.snapshot_at(events, JOURNAL_FILE, lambda adapter: None, NOW, live_process=True, pause_request=False)

    lines = table.view_lines(snap, picker.plan_links(events), 100)

    assert " ".join(lines[0].split()) == "run test running ░░░░░░░░░░░░░░░░ 0% 0/2 ended 2h59m used ETA -"
    assert [" ".join(line.split()) for line in lines[2:]] == [
        "# plan state phase after used", "1 ▶ a running specialist ● 2h59m", "gates ○", "verifier ○", "fix-up ○", "2 · b pending",
    ]


def test_the_picker_of_a_run_is_drawn_by_the_picker_module():
    picker = import_module("delegate.run.status_picker")
    events = a_running_run()
    snap = status.snapshot_at(events, JOURNAL_FILE, lambda adapter: None, NOW, live_process=True, pause_request=False)
    run = picker.ActiveRun(JOURNAL_FILE, snap, "run/demo", NOW, 3)

    assert picker.choices_line([run]) == f"1 runs are active in this repository; give a run id: {snap.run_id} (run/demo, running)"
    assert picker.picker_lines([run], NOW)[0] == "1 runs are active in this repository"

