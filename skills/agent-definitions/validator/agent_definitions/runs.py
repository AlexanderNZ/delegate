"""Find the journal of a run in a repository. `status` and `watch` share it.

The engine keeps one directory for each run in the state directory of the
repository. This module reads only the names of those directories and the first
line of each journal. It needs no adapter and no workflow file.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .engine import EngineError, state_directory

JOURNAL_NAME: str = "journal.jsonl"


class RunsError(Exception):
    """No journal can be found. The message names the repository or the run id."""


def _start_time(journal: Path) -> datetime:
    """The time of the first event of a journal. A journal with no complete first line ranks oldest."""
    with journal.open() as handle:
        line = handle.readline()
    if not line.endswith("\n"):
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(json.loads(line)["time"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise RunsError(f"journal {journal}: the first line is not an event with a time") from None


def journal_of(repo: Path, run_id: str | None) -> Path:
    """The journal file of the run `run_id`, or of the newest run when `run_id` is `None`.

    Raise RunsError when `repo` is not a git repository, when the run is
    unknown, or when the repository has no run.
    """
    try:
        _, state = state_directory(repo)
    except EngineError as error:
        raise RunsError(f"{repo} is not a git repository: {error}") from None
    runs = state / "runs"
    if run_id is not None:
        journal = runs / run_id / JOURNAL_NAME
        if not journal.is_file():
            raise RunsError(f"run {run_id!r} not found in {repo}: no journal at {journal}")
        return journal
    journals = sorted(runs.glob(f"*/{JOURNAL_NAME}")) if runs.is_dir() else []
    if not journals:
        raise RunsError(f"no run found in {repo}: no journal under {runs}")
    return max(journals, key=_start_time)
