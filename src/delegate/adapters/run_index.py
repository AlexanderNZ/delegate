"""The directory backend of the run index port (`delegate.ports.index`, ADR 0008).

`chosen_index` gives the directory (#323): `DELEGATE_INDEX` when it is set and not empty, else the key `index` of the
configuration file of the user (`delegate.run.user_config`), else `index/` in the state root of this machine. So the index
is always on, and each run registers itself with no setting. The backend keeps one JSON file for each run, named `<run id>.json`. It writes the
record to a temporary file in the same directory and renames it, so a reader never sees half a file. The backend reads
and writes files only.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from ..ports.index import RunIndexError, RunRecord
from ..run.user_config import UserConfig

INDEX_VARIABLE: str = "DELEGATE_INDEX"

# The name of the default index directory in the state root of this machine.
DEFAULT_NAME: str = "index"

# Where the index directory comes from, as the dry run and the header line of `delegate runs` name it.
FROM_VARIABLE: str = INDEX_VARIABLE
FROM_FILE: str = "the configuration file"
FROM_DEFAULT: str = "the default of the state root"

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


@dataclass(frozen=True)
class IndexChoice:
    """The run index of this process, and where its directory comes from: `FROM_VARIABLE`, `FROM_FILE` or `FROM_DEFAULT`."""

    index: DirectoryRunIndex
    source: str

    @property
    def directory(self) -> Path:
        return self.index.directory

    @property
    def shared(self) -> bool:
        """True when a setting names the directory, so other hosts can share it; False for the index of this machine only."""
        return self.source != FROM_DEFAULT


def chosen_index(environment: Mapping[str, str], config: UserConfig, state_root: Path) -> IndexChoice:
    """The run index of a process: `DELEGATE_INDEX` when it is set and not empty, else the key `index` of `config`, else `index/` in `state_root`."""
    named = environment.get(INDEX_VARIABLE)
    if named:
        return IndexChoice(DirectoryRunIndex(Path(named).expanduser()), FROM_VARIABLE)
    if config.index is not None:
        return IndexChoice(DirectoryRunIndex(config.index), FROM_FILE)
    return IndexChoice(DirectoryRunIndex(state_root / DEFAULT_NAME), FROM_DEFAULT)
