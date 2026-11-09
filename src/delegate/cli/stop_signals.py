"""The stop signals of a command that Ctrl-C ends: SIGTERM and SIGHUP end it as Ctrl-C does.

`delegate run` (#178) and the live view of `delegate watch` (#280) use `stop_signals_as_ctrl_c`, so a signal runs the same
code as Ctrl-C: the engine releases its lock, and the live view shows the cursor again.
"""

from __future__ import annotations

import contextlib
import signal
import threading
from collections.abc import Iterator

# The signals that stop a run as Ctrl-C does.
STOP_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def stop_signals_as_ctrl_c() -> Iterator[None]:
    """For the block, SIGTERM and SIGHUP raise `KeyboardInterrupt` in the main thread, as SIGINT does. Restore the old handlers after.

    SIGINT also raises it when the process inherited SIGINT as ignored, as a run that a background shell started does
    (#198). With the default handler of Python, SIGINT is left as it is.

    Only the first signal raises: the engine ends the child process group and releases the lock while it handles the
    interrupt, and a second exception would cut that off. A thread other than the main thread cannot set a handler,
    and then the block runs with the handlers as they are.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    raised = False

    def handler(signum: int, frame: object) -> None:
        nonlocal raised
        if not raised:
            raised = True
            raise KeyboardInterrupt

    handled = list(STOP_SIGNALS)
    if signal.getsignal(signal.SIGINT) == signal.SIG_IGN:
        # A shell that is not interactive sets SIGINT to ignored for a background command, and each process of a run that
        # starts from it inherits that (#198). Python then installs no handler, and SIGINT does nothing. Handle it as the
        # others.
        handled.append(signal.SIGINT)
    old = {signum: signal.signal(signum, handler) for signum in handled}
    try:
        yield
    finally:
        for signum, previous in old.items():
            signal.signal(signum, previous)
