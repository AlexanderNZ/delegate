#!/usr/bin/env python3
"""Cut a release: pick the next version, write the release notes, and edit the version and the CHANGELOG.

Three subcommands:

    release.py next-version --bump {patch,minor,major}
    release.py notes --version X.Y.Z
    release.py apply --version X.Y.Z --date YYYY-MM-DD

Every subcommand reads the repository that `--repo` names (the current
directory by default). A failure writes one message to stderr and exits 1.
The script uses the standard library only.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

DEFAULT_PYPROJECT: str = "skills/agent-definitions/validator/pyproject.toml"

# A release tag is `v<major>.<minor>.<patch>` and nothing else.
TAG: re.Pattern[str] = re.compile(r"v(\d+)\.(\d+)\.(\d+)")

# The conventional prefixes that get their own group in the notes, in the order of the groups.
GROUPS: list[tuple[str, str]] = [
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("ci", "CI"),
]
OTHER: str = "Other changes"

# The start of a conventional subject: `feat:`, `fix(scope):` or `feat!:`.
PREFIX: re.Pattern[str] = re.compile(r"(" + "|".join(prefix for prefix, _title in GROUPS) + r")(?:\([^)]*\))?!?:")

Version = tuple[int, int, int]


class ReleaseError(Exception):
    """A condition that stops the release. The message names the input at fault."""


def git(repo: Path, *args: str) -> str:
    """Run git in the repository and return its standard output."""
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    except FileNotFoundError as error:
        raise ReleaseError("git is not installed or not on PATH") from error
    if done.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed in {repo}: {done.stderr.strip()}")
    return done.stdout


def release_tags(repo: Path) -> list[Version]:
    """The versions of the release tags of the repository, oldest first. A tag that is not `vX.Y.Z` is ignored."""
    found: list[Version] = []
    for name in git(repo, "tag", "--list").splitlines():
        match = TAG.fullmatch(name)
        if match:
            found.append((int(match[1]), int(match[2]), int(match[3])))
    return sorted(found)


def read_text(path: Path) -> str:
    if not path.is_file():
        raise ReleaseError(f"{path} does not exist")
    return path.read_text()


def project_version(pyproject: Path) -> Version:
    """The version in the `[project]` table of `pyproject.toml`."""
    try:
        version = tomllib.loads(read_text(pyproject))["project"]["version"]
    except tomllib.TOMLDecodeError as error:
        raise ReleaseError(f"{pyproject} is not valid TOML: {error}") from error
    except KeyError as error:
        raise ReleaseError(f"{pyproject} has no project.version") from error
    return parse_version(version, f"project.version in {pyproject}")


def parse_version(text: str, source: str) -> Version:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text)
    if not match:
        raise ReleaseError(f"{source} is {text!r}, but a version must be X.Y.Z with digits only")
    return int(match[1]), int(match[2]), int(match[3])


def show(version: Version) -> str:
    return ".".join(str(part) for part in version)


def next_version(repo: Path, pyproject: Path, bump: str) -> Version:
    tags = release_tags(repo)
    if not tags:
        return project_version(pyproject)
    major, minor, patch = tags[-1]
    if bump == "major":
        return major + 1, 0, 0
    if bump == "minor":
        return major, minor + 1, 0
    return major, minor, patch + 1


def release_notes(repo: Path, version: Version) -> str:
    """The Markdown notes of the commits since the newest release tag below `version`, merges left out.

    Without such a tag, the notes cover every commit. A group appears only when
    it holds a commit. Each group lists its commits oldest first.
    """
    below = [tag for tag in release_tags(repo) if tag < version]
    since = f"v{show(below[-1])}" if below else None
    log = git(repo, "log", "--no-merges", "--topo-order", "--reverse", "--format=%h%x09%s", f"{since}..HEAD" if since else "HEAD")
    groups: dict[str, list[str]] = {title: [] for _prefix, title in GROUPS} | {OTHER: []}
    for line in log.splitlines():
        sha, _tab, subject = line.partition("\t")
        match = PREFIX.match(subject)
        title = next((title for prefix, title in GROUPS if match and match[1] == prefix), OTHER)
        groups[title].append(f"- {subject} ({sha})")
    if not any(groups.values()):
        raise ReleaseError(f"no commits since {since}, so there is nothing to release" if since else "the repository has no commits")
    return "\n\n".join(f"### {title}\n\n" + "\n".join(lines) for title, lines in groups.items() if lines) + "\n"


def version_argument(text: str) -> Version:
    """The `type` of a `--version` argument: a bad value is a usage error that names it."""
    try:
        return parse_version(text, "--version")
    except ReleaseError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path("."), help="the repository to read (default: the current directory)")
    parser.add_argument(
        "--pyproject", type=Path, default=Path(DEFAULT_PYPROJECT),
        help=f"the pyproject.toml that holds the version, relative to --repo (default: {DEFAULT_PYPROJECT})",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    bump = commands.add_parser("next-version", help="print the next version")
    bump.add_argument("--bump", required=True, choices=["patch", "minor", "major"])
    notes = commands.add_parser("notes", help="print the release notes from the commits")
    notes.add_argument("--version", required=True, type=version_argument)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo: Path = args.repo
    pyproject = repo / args.pyproject
    try:
        if args.command == "next-version":
            print(show(next_version(repo, pyproject, args.bump)))
        elif args.command == "notes":
            print(release_notes(repo, args.version), end="")
        return 0
    except ReleaseError as error:
        print(f"release.py: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
