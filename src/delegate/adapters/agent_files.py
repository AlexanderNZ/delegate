"""Where a harness finds the agent file of an agent: the project directory first, then the user directory (#205).

The engine hashes the file that an adapter names here, at the start of a run and before each agent session. The user
directory is outside the repository, so a change of a user-level agent file is in scope too.
"""

from __future__ import annotations

from pathlib import Path


def find_agent_file(agent: str, directory: Path, project: Path, user: Path) -> Path | None:
    """The path of the file `<agent>.md` in the project directory `directory / project`, else in `Path.home() / user`.

    Return None when neither is a file. The home directory is the one that the process environment gives.
    """
    for candidate in (directory / project, Path.home() / user):
        path = candidate / f"{agent}.md"
        if path.is_file():
            return path
    return None
