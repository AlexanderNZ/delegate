"""The title of the terminal: the text that the tab of the terminal shows while a `delegate` command runs.

A terminal names a tab by its foreground process when the program sets no title, and the process of `delegate` is
Python. So when standard output is a terminal, a command sets the title with the OSC 2 sequence
(`ESC ] 2 ; <title> BEL`). `delegate watch` puts the progress of the run in it, and `delegate run` the short run id.
When standard output is not a terminal, or `DELEGATE_NO_TITLE` is `1`, no sequence is written.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import TextIO

# The variable of the environment that turns the title off when it is `1`.
NO_TITLE_VARIABLE: str = "DELEGATE_NO_TITLE"

# The text between two parts of a title.
SEPARATOR: str = " · "

# A control character. A title holds none, so a BEL in a run id cannot end the sequence early.
CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")


def short_run_id(run_id: str) -> str:
    """The short form of a run id: the random part after the last `-`, as `ab12` of `20261016T030000Z-ab12`.

    A run id with no `-` is its own short form.
    """
    return run_id.rsplit("-", 1)[-1] or run_id


class Title:
    """The title of the terminal that is standard output. `set` writes a title only when it differs from the last one."""

    def __init__(self, out: TextIO, environ: Mapping[str, str] | None = None) -> None:
        environ = os.environ if environ is None else environ
        self._out = out
        self.enabled = out.isatty() and environ.get(NO_TITLE_VARIABLE) != "1"
        self._last: str | None = None

    def set(self, text: str) -> None:
        text = CONTROL_CHARACTER.sub("", text)
        if not self.enabled or text == self._last:
            return
        self._last = text
        self._out.write(f"\x1b]2;{text}\x07")
        self._out.flush()
