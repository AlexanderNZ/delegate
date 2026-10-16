"""The helper that every harness adapter uses to read an event stream."""

from __future__ import annotations

import json
from pathlib import Path

from ..ports.harness import AdapterError


def read_events(stream: Path) -> list[dict[str, object]]:
    """The events of a line-delimited JSON stream, in order.

    A killed harness, or a harness that still writes, can leave half a line at
    the end, so a last line that is not JSON is dropped. A bad line anywhere
    else is not a harness event stream: raise AdapterError and name the line.
    A missing file raises AdapterError and names the path.
    """
    try:
        text = stream.read_text()
    except FileNotFoundError:
        raise AdapterError(f"event stream {stream} not found") from None
    events: list[dict[str, object]] = []
    lines = text.splitlines()
    for number, line in enumerate(lines, start=1):
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError as error:
            if number == len(lines):
                break
            raise AdapterError(f"{stream}: line {number} is not JSON: {error}") from None
    return events
