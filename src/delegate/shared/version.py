"""The version of the package, from one place: the `version` field of `pyproject.toml`.

An installed package has it in its metadata. A source tree that runs from `PYTHONPATH`, as the dev shell and the tests do,
has no metadata, so the version comes from the `pyproject.toml` of the tree. The release step edits that one field only.
"""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

DISTRIBUTION: str = "agent-definitions"


def package_version() -> str:
    """The version of the package. Raise RuntimeError when neither the metadata nor a `pyproject.toml` of the package has it."""
    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
        pass
    for directory in Path(__file__).resolve().parents:
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            project = tomllib.loads(pyproject.read_text()).get("project", {})
            if project.get("name") == DISTRIBUTION and "version" in project:
                return project["version"]
    raise RuntimeError(f"the version of {DISTRIBUTION} is in neither the package metadata nor a pyproject.toml above {__file__}")
