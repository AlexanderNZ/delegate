"""The version of the package, from one place: the `version` field of `pyproject.toml`.

A module that runs from a source tree reads the version from the `pyproject.toml` of that tree. The dev shell and the
tests run this way, with `src` on `PYTHONPATH`. The tree can hold a stale metadata directory, for example a
`src/agent_definitions.egg-info` that an old `pip install -e .` left, and git ignores it. The metadata would then
give an old version, so a `pyproject.toml` above the module has the first place. An installed package has no such
file above it, so it reads its metadata. The release step edits that one field only.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

DISTRIBUTION: str = "agent-definitions"


def package_version() -> str:
    """The version of the package. Raise RuntimeError when neither a `pyproject.toml` of the package nor the metadata has it."""
    for directory in Path(__file__).resolve().parents:
        pyproject = directory / "pyproject.toml"
        if pyproject.is_file():
            project = tomllib.loads(pyproject.read_text()).get("project", {})
            if project.get("name") == DISTRIBUTION and "version" in project:
                return project["version"]
    try:
        return version(DISTRIBUTION)
    except PackageNotFoundError:
        raise RuntimeError(
            f"the version of {DISTRIBUTION} is in neither a pyproject.toml above {__file__} nor the package metadata"
        ) from None


class VersionAction(argparse.Action):
    """The `--version` option. It reads the version when the option is used, not when the parser is built.

    A tree can hold no version: a copy of `src/` alone has no `pyproject.toml` and no metadata. Only `--version` needs
    the version, so each other use of a command works there. With no known version, the option prints the message of
    `package_version` on stderr, with no traceback, and exits 1."""

    def __init__(self, option_strings, dest=argparse.SUPPRESS, default=argparse.SUPPRESS, help=None):
        super().__init__(option_strings=option_strings, dest=dest, default=default, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):
        try:
            text = package_version()
        except RuntimeError as error:
            print(f"{parser.prog}: error: {error}", file=sys.stderr)
            parser.exit(1)
        print(f"{parser.prog} {text}")
        parser.exit()
