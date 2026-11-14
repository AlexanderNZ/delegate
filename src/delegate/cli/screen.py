"""The terminal that a live view draws on, in place (#280, #282): the frame of `delegate watch` and of `delegate runs --watch` (#313).

The first frame hides the cursor and clears the screen, and is drawn in full. A later frame is one synchronized update (`SYNC_BEGIN`
and `SYNC_END`): it writes only the lines that changed, and nothing for a frame that is the frame before. A new size of the
terminal draws the full frame again. The command shows the cursor again with `Screen.restore` at each end of its view. The screen is
never an alternate screen, so the last frame stays in the scrollback.
"""

from __future__ import annotations

import shutil
from typing import TextIO

# The terminal control sequences of the live view: clear the screen, move the cursor to the top left, clear to the end
# of the line, and clear the screen below the cursor.
CLEAR_SCREEN: str = "\x1b[2J"
HOME: str = "\x1b[H"
CLEAR_LINE_END: str = "\x1b[K"
CLEAR_DOWN: str = "\x1b[J"

# The terminal control sequences that hide and show the cursor. The live view hides the cursor with its first frame, so the
# reader does not see it move down the screen, and the command shows it on each end of the watch (#280). Ctrl-C can come
# while a frame is written, so the command writes SHOW_CURSOR after the last frame.
HIDE_CURSOR: str = "\x1b[?25l"
SHOW_CURSOR: str = "\x1b[?25h"

# The terminal control sequences that begin and end a synchronized update (#280): the terminal shows the whole frame at once,
# also when a frame comes in parts, as over SSH. A terminal that does not know them ignores them.
SYNC_BEGIN: str = "\x1b[?2026h"
SYNC_END: str = "\x1b[?2026l"


class Screen:
    """The terminal that the live view draws on. The first frame hides the cursor and clears the screen, and is drawn in
    full. A later frame is one synchronized update: it writes only the lines that changed, and nothing when no line
    changed (#282). A new size of the terminal draws the full frame again, from the top left."""

    def __init__(self, out: TextIO) -> None:
        self._out = out
        self._drawn = False
        self._lines: list[str] = []  # the lines of the frame that was drawn last
        self._size = (0, 0)  # the size of the terminal at that draw

    def size(self) -> tuple[int, int]:
        """The columns and the lines of the terminal. The lines are 0 when the terminal does not give its size: the live
        view then does not fit the height (#144)."""
        size = shutil.get_terminal_size(fallback=(80, 0))
        return size.columns, size.lines

    def draw(self, lines: list[str]) -> None:
        """Draw the frame. The same frame writes nothing. With the size of the terminal of the last draw, only the lines
        that changed are written (#282). The first frame, a new size, and a frame that does not leave a line free under
        it (a last frame that scrolls, or a terminal that gives no height) are drawn in full."""
        size, last_size = self.size(), self._size
        # A write that is cut (Ctrl-C) leaves the screen in a state that is not known: the next draw is a full frame.
        self._size = (0, 0)
        if self._drawn and size == last_size and 0 < len(lines) < size[1] and len(self._lines) < size[1]:
            if lines != self._lines:
                self._write_changes(lines)
        else:
            self._write_frame(lines)
        self._lines, self._size = list(lines), size

    def _write_frame(self, lines: list[str]) -> None:
        start = SYNC_BEGIN + HOME if self._drawn else HIDE_CURSOR + SYNC_BEGIN + CLEAR_SCREEN + HOME
        self._drawn = True
        # The screen below the frame is cleared before the last newline, so a line printed after the frame starts clean.
        self._out.write(start + "\n".join(f"{line}{CLEAR_LINE_END}" for line in lines) + CLEAR_DOWN + "\n" + SYNC_END)
        self._out.flush()

    def _write_changes(self, lines: list[str]) -> None:
        """Move to each changed line, write it, and clear to its end. The cursor then goes to the line after the frame, as
        a full frame leaves it, and a frame with fewer lines clears the screen from there."""
        old = self._lines
        changed = [
            f"\x1b[{number};1H{line}{CLEAR_LINE_END}"
            for number, line in enumerate(lines, start=1)
            if number > len(old) or line != old[number - 1]
        ]
        below = f"\x1b[{len(lines) + 1};1H" + (CLEAR_DOWN if len(lines) < len(old) else "")
        self._out.write(SYNC_BEGIN + "".join(changed) + below + SYNC_END)
        self._out.flush()

    def restore(self) -> None:
        """Show the cursor again, when a frame hid it. Each end of the watch calls this."""
        if self._drawn:
            self._out.write(SHOW_CURSOR)
            self._out.flush()
