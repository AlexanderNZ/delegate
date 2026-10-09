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
import datetime
import re
import subprocess
import sys
import tomllib
from pathlib import Path

DEFAULT_PYPROJECT: str = "pyproject.toml"

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


def with_version(pyproject_text: str, version: Version, pyproject: Path) -> str:
    """The text of `pyproject.toml` with the version of the `[project]` table replaced."""
    table = re.search(r"^\[project\][ \t]*\n(.*?)(?=^\[|\Z)", pyproject_text, flags=re.MULTILINE | re.DOTALL)
    if not table:
        raise ReleaseError(f"{pyproject} has no [project] table")
    body, count = re.subn(r'^(version\s*=\s*)"[^"]*"', rf'\g<1>"{show(version)}"', table[1], count=1, flags=re.MULTILINE)
    if count == 0:
        raise ReleaseError(f'{pyproject} has no line `version = "..."` in its [project] table')
    return pyproject_text[: table.start(1)] + body + pyproject_text[table.end(1) :]


def with_entry(changelog_text: str, repo: Path, version: Version, date: str, changelog: Path) -> str:
    """The text of the CHANGELOG with a dated entry directly under `## [Unreleased]`.

    The entry takes the body of the Unreleased section, and the section is left
    empty. When the body is empty, the entry takes the release notes instead.
    """
    unreleased = re.search(r"^## \[Unreleased\][ \t]*(?:\n|\Z)(.*?)(?=^## |\Z)", changelog_text, flags=re.MULTILINE | re.DOTALL)
    if not unreleased:
        raise ReleaseError(f"{changelog} has no `## [Unreleased]` heading")
    body = unreleased[1].strip() or release_notes(repo, version).strip()
    entry = f"## [Unreleased]\n\n## [{show(version)}] - {date}\n\n{body}\n"
    rest = changelog_text[unreleased.end() :]
    return changelog_text[: unreleased.start()] + entry + ("\n" + rest if rest else "")


def apply(repo: Path, pyproject: Path, version: Version, date: str) -> list[Path]:
    """Set the version in `pyproject.toml` and add the CHANGELOG entry. Return the files that changed.

    Each part is skipped when it is already done, so a second call changes
    nothing. Both files are read and checked before either is written.
    """
    changelog = repo / "CHANGELOG.md"
    pyproject_text, changelog_text = read_text(pyproject), read_text(changelog)
    changes: dict[Path, str] = {}
    if project_version(pyproject) != version:
        changes[pyproject] = with_version(pyproject_text, version, pyproject)
    if not re.search(rf"^## \[{re.escape(show(version))}\] - ", changelog_text, flags=re.MULTILINE):
        changes[changelog] = with_entry(changelog_text, repo, version, date, changelog)
    for path, text in changes.items():
        path.write_text(text)
    return list(changes)


def version_argument(text: str) -> Version:
    """The `type` of a `--version` argument: a bad value is a usage error that names it."""
    try:
        return parse_version(text, "--version")
    except ReleaseError as error:
        raise argparse.ArgumentTypeError(str(error)) from error


def date_argument(text: str) -> str:
    """The `type` of a `--date` argument: a real calendar date written `YYYY-MM-DD`."""
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise ValueError
        datetime.date.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"--date is {text!r}, but a date must be a real date written YYYY-MM-DD") from error
    return text


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
    edit = commands.add_parser("apply", help="set the version in pyproject.toml and add the CHANGELOG entry")
    edit.add_argument("--version", required=True, type=version_argument)
    edit.add_argument("--date", required=True, type=date_argument)
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
        elif args.command == "apply":
            changed = apply(repo, pyproject, args.version, args.date)
            if changed:
                print(f"applied {show(args.version)} to " + " and ".join(path.name for path in changed))
            else:
                print(f"nothing to change: {show(args.version)} is already in {pyproject.name} and CHANGELOG.md")
        return 0
    except ReleaseError as error:
        print(f"release.py: error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
