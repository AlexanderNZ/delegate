"""The name of this machine, as a run records it in `run-start` (#306).

The rule is in this one place. A command that needs the name of the machine calls `host_name`.
"""

import os
import socket

HOST_VARIABLE = "DELEGATE_HOST"


def host_name() -> str:
    """The name of this machine: `DELEGATE_HOST` when it is set and not empty, else the short host name.

    The short host name is the host name up to its first `.`.
    """
    named = os.environ.get(HOST_VARIABLE)
    if named:
        return named
    return socket.gethostname().split(".", 1)[0]
