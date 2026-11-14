"""The error of the run context that a command reports: a run that cannot start or go on.

It has its own module so that a view of the run context can name the error with no import of the engine (#331).
"""

from __future__ import annotations


class EngineError(Exception):
    """The run cannot start or go on. The message names the input at fault."""
