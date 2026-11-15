"""The helper that every harness adapter uses to read an event stream."""

from __future__ import annotations

import copy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, TypeVar

from ..ports.harness import AdapterError

T = TypeVar("T")


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


# The count of first bytes of a stream that a `StreamFold` keeps, to know a stream that another file replaced.
HEAD_BYTES: int = 256


class _Kept:
    """What a `StreamFold` keeps of one stream: the fold of its complete lines, and where the fold stopped."""

    def __init__(self, state: object) -> None:
        self.state = state
        self.offset = 0  # the bytes of the stream that the state holds: the complete lines up to here
        self.lines = 0  # the lines in those bytes
        self.head = b""  # the first bytes of the stream, at most HEAD_BYTES


class StreamFold:
    """The fold of the events of each stream, kept between calls, so that a stream that grows is parsed only after its last read (#352).

    `empty()` makes the state of a stream with no event. `step(state, event, stream)` adds one event to the state in
    place. A call gives the state after all events of the stream: the same state as a fold of `read_events`, with the same
    errors. The fold keeps the byte offset after the last complete line and the state at that offset, and a call parses
    the bytes after the offset. A stream that is shorter than the offset, or whose first bytes changed, is read again
    from its start. The tail of the stream, a last line that the harness still writes, is not kept: a last line that is
    JSON counts, and the next call reads it again with the bytes that came after it. A call that raises forgets the
    stream, so the next call reads it from its start.
    """

    def __init__(self, empty: Callable[[], T], step: Callable[[T, dict[str, object], Path], None]) -> None:
        self._empty = empty
        self._step = step
        self._kept: dict[Path, _Kept] = {}

    def __call__(self, stream: Path) -> T:
        try:
            return self._fold(stream)
        except BaseException:
            self._kept.pop(stream, None)
            raise

    def _fold(self, stream: Path) -> T:
        try:
            file = stream.open("rb")
        except FileNotFoundError:
            raise AdapterError(f"event stream {stream} not found") from None
        with file:
            kept = self._kept.get(stream)
            if kept is not None and not self._continues(file, kept):
                kept = None
            if kept is None:
                kept = self._kept[stream] = _Kept(self._empty())
            file.seek(kept.offset)
            data = file.read()
        *complete, tail = data.split(b"\n")
        read = 0  # the bytes of `data` that the state holds after this call
        torn = False  # the last line of the stream is a line that is not JSON
        for index, line in enumerate(complete):
            try:
                event = json.loads(line.decode())  # a line that is no UTF-8 raises UnicodeDecodeError, as `read_text` does
            except json.JSONDecodeError as error:
                if index == len(complete) - 1 and not tail:
                    torn = True  # a torn last line: dropped, and read again at the next call
                    break
                raise AdapterError(f"{stream}: line {kept.lines + 1} is not JSON: {error}") from None
            self._step(kept.state, event, stream)  # type: ignore[arg-type]
            kept.lines += 1
            read += len(line) + 1
        kept.head = (kept.head + data[:read])[:HEAD_BYTES]
        kept.offset += read
        if torn or not tail:
            return kept.state  # type: ignore[return-value]
        try:
            event = json.loads(tail.decode())
        except ValueError:  # half a line, also half a character: not an event yet
            return kept.state  # type: ignore[return-value]
        state = copy.deepcopy(kept.state)  # the last line counts, but the kept state does not hold it
        self._step(state, event, stream)  # type: ignore[arg-type]
        return state  # type: ignore[return-value]

    @staticmethod
    def _continues(file: BinaryIO, kept: _Kept) -> bool:
        """True when the file is the stream that `kept` holds, grown: not shorter than the offset, with the same first bytes."""
        if os.fstat(file.fileno()).st_size < kept.offset:
            return False
        return file.read(len(kept.head)) == kept.head
