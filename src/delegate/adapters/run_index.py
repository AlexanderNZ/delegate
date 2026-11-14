"""The directory backend of the run index port (`delegate.ports.index`, ADR 0008).

The backend takes a directory and keeps one JSON file for each run in it, named `<run id>.json`. It writes the record to a temporary file in the same
directory and renames it, so a reader never sees half a file. The backend reads and writes files only. The choice of the directory is policy, and it is
in `delegate.cli.machine_settings` (#333).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

from ..ports.index import RunIndexError, RunRecord

# The suffix of the file of a record. A temporary file starts with a dot, and a reader skips it.
SUFFIX: str = ".json"


class DirectoryRunIndex:
    """The run index in the directory `directory`."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def _path(self, run_id: str) -> Path:
        if not run_id or run_id.startswith(".") or os.sep in run_id or (os.altsep and os.altsep in run_id):
            raise RunIndexError(f"run index {self.directory}: {run_id!r} is not a run id that names a file")
        return self.directory / f"{run_id}{SUFFIX}"

    def record(self, record: RunRecord) -> None:
        path = self._path(record.run_id)
        temporary = self.directory / f".{record.run_id}.{os.getpid()}.tmp"
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(asdict(record)) + "\n")
            os.replace(temporary, path)
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # the write failed already; the error that the caller sees is the first one
            raise RunIndexError(f"run index {self.directory}: cannot record run {record.run_id}: {error}") from None

    def list_records(self) -> list[RunRecord]:
        try:
            paths = sorted(self.directory.glob(f"[!.]*{SUFFIX}"))
        except OSError as error:
            raise RunIndexError(f"run index {self.directory}: cannot list the runs: {error}") from None
        records: list[RunRecord] = []
        for path in paths:
            try:
                records.append(RunRecord(**json.loads(path.read_text())))
            except (OSError, ValueError, TypeError):
                continue  # a file that is gone, or is not a record of this version, is not a record
        return records

    def forget(self, run_id: str) -> None:
        try:
            self._path(run_id).unlink(missing_ok=True)
        except OSError as error:
            raise RunIndexError(f"run index {self.directory}: cannot forget run {run_id}: {error}") from None
