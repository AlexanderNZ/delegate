"""The settings of this machine for a command: the configuration file of the user, the state root, and the run index (#323).

`delegate run`, `delegate runs` and `delegate clean` read them here, in one way. The configuration file is
`delegate.run.user_config`, and the index directory comes from `delegate.adapters.run_index.chosen_index`: `DELEGATE_INDEX`,
else the key `index` of the file, else `index/` in the state root.

`index_check` is the part of the setup check of `delegate run --dry-run` for the run index: the file that the run reads,
the index directory and where it comes from, and a write probe of the directory.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..adapters.run_index import IndexChoice, chosen_index
from ..run.runs import state_root
from ..run.user_config import UserConfig, load_user_config


@dataclass(frozen=True)
class MachineSettings:
    """The configuration file of the user, the state root of this machine, and the run index that a command uses."""

    config: UserConfig
    root: Path
    index: IndexChoice


def machine_settings() -> MachineSettings:
    """The settings of this process. Raise UserConfigError for a configuration file that cannot be used, and RunsError for a state root that cannot be used."""
    root = state_root()
    config = load_user_config(os.environ)
    return MachineSettings(config, root, chosen_index(os.environ, config, root))


def config_text(config: UserConfig) -> str:
    """The configuration file that a command read, for a line of output: its path, or `none, the defaults apply`."""
    return str(config.path) if config.path is not None else "none, the defaults apply"


def index_check(settings: MachineSettings) -> tuple[list[str], list[str]]:
    """The lines of the setup check of the dry run for the run index, and its problems.

    The lines are the configuration file, the index directory with where it comes from, and the result of the write probe:
    the check writes a temporary file in the index directory and removes it, and it removes each directory that it made for the probe.
    A directory that cannot be written is a problem.
    """
    directory = settings.index.directory
    lines = [f"config: {config_text(settings.config)}", f"run index: {directory} (from {settings.index.source})"]
    made = [path for path in (directory, *directory.parents) if not path.exists()]  # the directories that the probe makes, deepest first
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".delegate-probe-", suffix=".tmp"):
            pass
    except OSError as error:
        reason = getattr(error, "strerror", None) or str(error)
        return [*lines, "run index write: failed"], [f"run index {directory}: cannot write a file: {reason}"]
    finally:
        for path in made:  # a dry run leaves nothing: the run makes the directories when it writes its first record
            try:
                path.rmdir()
            except OSError:
                break  # not made here, or not empty: it stays, and so does each directory over it
    return [*lines, "run index write: ok"], []
