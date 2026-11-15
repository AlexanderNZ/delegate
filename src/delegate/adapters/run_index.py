"""The directory backend of the run index port (`delegate.ports.index`, ADR 0008).

The backend takes a directory and keeps one JSON file for each run in it, named `<run id>.json`. It writes the record to a temporary file in the same
directory and renames it, so a reader never sees half a file. The backend reads and writes files only. The choice of the directory is policy, and it is
in `delegate.cli.machine_settings` (#333).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from ..ports.index import RunIndexError, RunRecord

# The suffix of the file of a record. A temporary file starts with a dot, and a reader skips it.
SUFFIX: str = ".json"

# A directory whose change time is nearer than this to the time of a listing can change again in the same tick of the file system, so the next call lists it again (#350).
SETTLE_NANOSECONDS: int = 1_000_000_000

# What a call keeps of a file between two listings: its inode, its modification time and its size.
_FileSignature = tuple[int, int, int]


def _signature(entry: os.DirEntry[str]) -> _FileSignature | None:
    """The inode, the modification time and the size of a directory entry, or None when the file is gone."""
    try:
        stat = entry.stat()
    except OSError:
        return None
    return stat.st_ino, stat.st_mtime_ns, stat.st_size


class DirectoryRunIndex:
    """The run index in the directory `directory`."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        # The last listing (#350): the inode and the change time of the directory, the time of the listing, and the records. And the records by file name, with the signature of each file.
        self._listing: tuple[tuple[int, int], int, list[RunRecord]] | None = None
        self._files: dict[str, tuple[_FileSignature, RunRecord | None]] = {}

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
            stat = temporary.stat()  # the rename keeps the inode, the modification time and the size
            os.replace(temporary, path)
            self._files[path.name] = ((stat.st_ino, stat.st_mtime_ns, stat.st_size), record)  # a later listing does not read the file that this call wrote
        except OSError as error:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # the write failed already; the error that the caller sees is the first one
            raise RunIndexError(f"run index {self.directory}: cannot record run {record.run_id}: {error}") from None

    def list_records(self) -> list[RunRecord]:
        """The records of the directory. A call that finds the directory as the call before left it (#350) reads nothing; a call that finds it changed
        lists it and reads only the files that are new or changed: a record file is read again when its inode, its modification time or its size changed.
        """
        try:
            stat = self.directory.stat()
        except FileNotFoundError:
            self._files, self._listing = {}, None
            return []
        except OSError as error:
            raise RunIndexError(f"run index {self.directory}: cannot list the runs: {error}") from None
        signature = (stat.st_ino, stat.st_mtime_ns)
        if self._listing is not None and self._listing[0] == signature and self._listing[1] - stat.st_mtime_ns > SETTLE_NANOSECONDS:
            return list(self._listing[2])
        listed_at = time.time_ns()
        try:
            with os.scandir(self.directory) as entries:
                found = sorted(
                    (entry for entry in entries if not entry.name.startswith(".") and entry.name.endswith(SUFFIX)), key=lambda entry: entry.name,
                )
                signatures = {entry.name: _signature(entry) for entry in found}
        except (FileNotFoundError, NotADirectoryError):
            self._files, self._listing = {}, None
            return []  # no directory holds no record; a write says what is wrong with the path
        except OSError as error:
            raise RunIndexError(f"run index {self.directory}: cannot list the runs: {error}") from None
        files: dict[str, tuple[_FileSignature, RunRecord | None]] = {}
        records: list[RunRecord] = []
        for name, seen in signatures.items():
            kept = self._files.get(name)
            if seen is None:
                continue  # a file that is gone
            if kept is not None and kept[0] == seen:
                record = kept[1]
            else:
                try:
                    record = RunRecord(**json.loads((self.directory / name).read_text()))
                except (OSError, ValueError, TypeError):
                    record = None  # a file that is gone, or is not a record of this version, is not a record
            files[name] = (seen, record)
            if record is not None:
                records.append(record)
        self._files, self._listing = files, (signature, listed_at, records)
        return list(records)

    def forget(self, run_id: str) -> None:
        try:
            self._path(run_id).unlink(missing_ok=True)
        except OSError as error:
            raise RunIndexError(f"run index {self.directory}: cannot forget run {run_id}: {error}") from None
