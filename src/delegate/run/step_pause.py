"""The pause point of a step boundary: the place where a pause request stops the run (#341).

The request is a file in the directory of the run (see `delegate.run.pause`). The engine and the run of a gate
(`delegate.run.gate_run`) call `pause_point` between two pieces of work, so a pause never cuts off an agent or a gate.
"""

from __future__ import annotations

import json

from .journal import Journal
from .state_location import PAUSE_REQUEST_NAME


class Paused(Exception):
    """A pause request stopped the run at a step boundary. The journal holds `paused`. The message tells where."""


def pause_point(journal: Journal, ticket: str, before: str, where: str, **fields: object) -> None:
    """A step boundary: stop the run here when the directory of the run holds a pause request. Do nothing when it holds none.

    Write `pause-requested` with the time of the request, and `paused` with the
    ticket, the work that does not start (`before`, one of the `PAUSED_BEFORE_`
    values) and `fields`. Remove the request, and raise `Paused` with `where`,
    the text that tells where the run stopped. The engine calls it only between
    two pieces of work, so a pause never cuts off an agent or a gate.
    """
    request = journal.path.parent / PAUSE_REQUEST_NAME
    try:
        text = request.read_text()
    except FileNotFoundError:
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    requested_at = data.get("time") if isinstance(data, dict) else None
    journal.append("pause-requested", ticket=ticket, requested_at=requested_at)
    journal.append("paused", ticket=ticket, before=before, **fields)
    request.unlink(missing_ok=True)
    raise Paused(where)
