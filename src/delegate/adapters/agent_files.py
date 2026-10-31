"""Where a harness finds the agent file of an agent, and the seal of that file (#205, #216).

The project directory comes first, then the user directory. The engine hashes the file that an adapter names here, at the
start of a run and before each agent session. The user directory is outside the repository, so a change of a user-level
agent file is in scope too.

A seal is a copy of the agent file in a directory that the caller names. Each adapter seals by this copy today, and the
handle of the session is the agent name. The functions here hold what the adapters share: the search, the copy, and the
read of the frontmatter of a sealed file.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from ..definitions.validate import split_frontmatter
from ..ports.harness import SealedAgent, SealedFile


def agent_search_paths(agent: str, directory: Path, project: Path, user: Path) -> tuple[Path, ...]:
    """The paths where the harness looks for `<agent>.md`, in order: under `directory / project`, then under `Path.home() / user`.

    The home directory is the one that the process environment gives.
    """
    return tuple(candidate / f"{agent}.md" for candidate in (directory / project, Path.home() / user))


def find_agent_file(agent: str, directory: Path, project: Path, user: Path) -> Path | None:
    """The first of the search paths that is a file, or None when none is."""
    return next((path for path in agent_search_paths(agent, directory, project, user) if path.is_file()), None)


def seal_agent_file(agent: str, source: Path, project: Path, user: Path, into: Path) -> SealedAgent | None:
    """Copy the agent file of `agent` into the directory `into` as `<agent>.md`, and describe the copy.

    Return None, and write nothing, when the search finds no file. The handle is the agent name.
    """
    found = find_agent_file(agent, source, project, user)
    if found is None:
        return None
    into.mkdir(parents=True, exist_ok=True)
    sealed = into / f"{agent}.md"
    shutil.copyfile(found, sealed)
    return SealedAgent(agent, agent, into, (SealedFile(sealed, hashlib.sha256(sealed.read_bytes()).hexdigest()),))


def sealed_frontmatter(sealed: SealedAgent) -> tuple[dict | None, str | None]:
    """The frontmatter of the sealed file `<agent>.md`, or the reason that it has none: (frontmatter, None) or (None, reason)."""
    path = sealed.directory / f"{sealed.agent}.md"
    try:
        frontmatter, _, error = split_frontmatter(path.read_text())
    except (OSError, UnicodeDecodeError) as failure:
        return None, f"the sealed file {path} cannot be read: {failure}"
    if error is not None or frontmatter is None:
        return None, f"the sealed file {path} has no usable frontmatter: {error}"
    return frontmatter, None
