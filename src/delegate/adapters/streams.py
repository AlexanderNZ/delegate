"""The helpers that every harness adapter uses to capture and read an event stream."""

from __future__ import annotations

import json
from pathlib import Path

from ..ports.harness import AdapterError


def stream_path(report_path: Path) -> Path:
    """The file for the event stream: beside the report, and never over an earlier stream."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    path = report_path.parent / f"{report_path.stem}.stream.jsonl"
    number = 1
    while path.exists():
        number += 1
        path = report_path.parent / f"{report_path.stem}.stream-{number}.jsonl"
    return path


def read_events(stream: Path) -> list[dict[str, object]]:
    """The events of a line-delimited JSON stream, in order.

    A killed harness can leave half a line at the end, so a last line that is
    not JSON is dropped. A bad line anywhere else is not a harness event stream:
    raise AdapterError and name the line.
    """
    events: list[dict[str, object]] = []
    lines = stream.read_text().splitlines()
    for number, line in enumerate(lines, start=1):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as error:
            if number == len(lines):
                break
            raise AdapterError(f"{stream}: line {number} is not JSON: {error}") from None
    return events
