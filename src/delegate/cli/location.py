"""The lookup of the state location for a command, with the print of its note (#331).

`delegate.run.state_location.locate_state` writes nothing. When `DELEGATE_STATE_ROOT` and the file `state-root` name two
directories, the result holds one warning line, its `note`. The driver of each command looks the location up here, so the
command prints the note one time, and it passes the location on to the use case.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ..ports.vcs import VersionControl
from ..run.errors import EngineError
from ..run.state_location import StateLocation, locate_state


def report_location(repo: Path, vcs: VersionControl) -> StateLocation | None:
    """The location of the repository that holds `repo`, with its note printed on standard error. None when the lookup fails.

    A lookup that fails prints nothing: the use case that gets None looks up again, and it raises the error with its own message.
    """
    try:
        location = locate_state(repo, vcs)
    except EngineError:
        return None
    if location.note is not None:
        print(location.note, file=sys.stderr)
    return location
