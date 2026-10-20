"""The environment that the engine gives each process of a run: each agent and each gate command.

It is the environment of the engine, with one change. When the engine imports
its own `delegate` package through `PYTHONPATH` (a frozen engine, as in a run of
the kit on its own repository), the entry of that package does not go to the
agents and the gates. Otherwise, a test in a worktree or in a proof copy can
import the code of the engine, not the code under review (#154). The other
entries stay. When no entry stays, `PYTHONPATH` is not set.
"""

from __future__ import annotations

import os
from pathlib import Path

# The directory that holds the `delegate` package of the engine: `<src>` of `<src>/delegate/adapters/environment.py`.
ENGINE_SOURCE: Path = Path(__file__).resolve().parents[2]


def _is_engine_source(entry: str) -> bool:
    """True when the `PYTHONPATH` entry names the directory of the engine package. An empty entry is not that directory."""
    return bool(entry) and Path(entry).resolve() == ENGINE_SOURCE


def process_environment() -> dict[str, str]:
    """A copy of the environment of the engine, with no `PYTHONPATH` entry for the engine package."""
    environment = dict(os.environ)
    if "PYTHONPATH" not in environment:
        return environment
    entries = [entry for entry in environment["PYTHONPATH"].split(os.pathsep) if not _is_engine_source(entry)]
    if entries:
        environment["PYTHONPATH"] = os.pathsep.join(entries)
    else:
        del environment["PYTHONPATH"]
    return environment
