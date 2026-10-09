"""Guards that hold in every harness: no push from a worktree, no change to a hotspot path, and no change by a verifier.

The guards use git only, so they hold when a harness has no hook or permission
system of its own, or when a headless run skips it.

The push guard sets `core.hooksPath` for one worktree, through worktree-scoped
git configuration. The directory that it names holds a `pre-push` hook that
refuses every push, and a wrapper for each other hook of the repository, so
those hooks still run in the worktree. It does not stop a push with the
`--no-verify` option, which skips every `pre-push` hook.

The hotspot guard compares the changed paths of a worktree with the hotspot
patterns of the stack. A pattern that ends with `/` names a directory and
matches every path below it. Any other pattern is matched against the whole
path with `fnmatch` rules, where `*` also matches `/`, and a pattern that names
a directory also matches every path below it.

The worktree invariant compares a snapshot of a worktree before and after a
verifier run. A snapshot holds the HEAD commit and, for each path that
`git status` reports, the status code and a hash of the content of the file. So a
verifier that commits, that adds or deletes a file, or that rewrites a file that
was already changed or untracked, makes the snapshots differ. A file that git
ignores is not in the snapshot.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import shlex
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

PRE_PUSH_HOOK: str = """\
#!/bin/sh
echo "delegate: push refused: an engine worktree never pushes; the coordinator pushes" >&2
exit 1
"""


class GuardError(Exception):
    """A guard cannot be installed or checked. The message names the input at fault."""


class InvariantViolation(Exception):
    """A verifier changed the real worktree. The message names the worktree and each change."""

    def __init__(self, message: str, worktree: Path, head_before: str, head_after: str, changes: list[str]) -> None:
        super().__init__(message)
        self.worktree = worktree
        self.head_before = head_before
        self.head_after = head_after
        self.changes = changes


@dataclass(frozen=True)
class WorktreeSnapshot:
    """The HEAD commit of a worktree and the state of each path that `git status` reports."""

    worktree: Path
    head: str
    paths: dict[str, str]


@dataclass(frozen=True)
class HotspotMatch:
    """A changed path and the hotspot pattern that it matches."""

    path: str
    pattern: str


def _git(directory: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(directory), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise GuardError(f"git {' '.join(args)} failed in {directory}: {r.stderr.strip() or r.returncode}")
    return r.stdout.strip()


def install_push_guard(repo: Path, worktree: Path, hooks_dir: Path) -> None:
    """Make every push from `worktree` fail, and leave the main checkout and the other worktrees alone.

    `hooks_dir` is a directory that the engine owns, one for each worktree. It
    is made again on each call, so a second call (a resume) gives the same state.
    """
    # The hooks that the worktree used before: the repository's own, or the host's.
    inherited = Path(_git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))
    if hooks_dir.exists():
        shutil.rmtree(hooks_dir)
    hooks_dir.mkdir(parents=True)
    if inherited.is_dir():
        for hook in sorted(inherited.iterdir()):
            if hook.name != "pre-push" and not hook.name.endswith(".sample") and hook.is_file() and os.access(hook, os.X_OK):
                wrapper = hooks_dir / hook.name
                wrapper.write_text(f"#!/bin/sh\nexec {shlex.quote(str(hook.resolve()))} \"$@\"\n")
                wrapper.chmod(0o755)
    pre_push = hooks_dir / "pre-push"
    pre_push.write_text(PRE_PUSH_HOOK)
    pre_push.chmod(0o755)
    _git(repo, "config", "extensions.worktreeConfig", "true")
    _git(worktree, "config", "--worktree", "core.hooksPath", str(hooks_dir))


def _matches(path: str, pattern: str) -> bool:
    directory = pattern.rstrip("/")
    if pattern.endswith("/"):
        return path.startswith(directory + "/")
    return fnmatch.fnmatchcase(path, pattern) or path.startswith(directory + "/")


def changed_paths(worktree: Path, since: str, *, merge_base: bool) -> list[str]:
    """The paths that differ between `since` and the HEAD of the worktree, a rename counted as its old and its new path.

    With `merge_base` the comparison starts at the merge base of `since` and
    HEAD, so a `since` that moved on does not count. Raise GuardError when git fails.
    """
    spec = f"{since}...HEAD" if merge_base else f"{since}..HEAD"
    out = _git(worktree, "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", spec)
    return out.splitlines()


def hotspot_matches(paths: Sequence[str], patterns: Sequence[str]) -> list[HotspotMatch]:
    """Each path that matches a hotspot pattern, with the first pattern that it matches, in the order of `paths`."""
    found: list[HotspotMatch] = []
    for path in paths:
        pattern = next((p for p in patterns if _matches(path, p)), None)
        if pattern is not None:
            found.append(HotspotMatch(path, pattern))
    return found


def snapshot_worktree(worktree: Path) -> WorktreeSnapshot:
    """Record the HEAD and the status of `worktree`. Raise GuardError when git fails."""
    head = _git(worktree, "rev-parse", "HEAD")
    # The status code starts with a space for a change that is not staged, so the output must not be stripped.
    r = subprocess.run(
        ["git", "-C", str(worktree), "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise GuardError(f"git status failed in {worktree}: {r.stderr.strip() or r.returncode}")
    status = r.stdout
    paths: dict[str, str] = {}
    for entry in filter(None, status.split("\0")):
        code, name = entry[:2], entry[3:]
        file = worktree / name
        content = hashlib.sha1(file.read_bytes()).hexdigest() if file.is_file() else "none"
        paths[name] = f"{code} {content}"
    return WorktreeSnapshot(worktree, head, paths)


def _change(name: str, before: str | None, after: str | None) -> str:
    """One line for a path whose state differs: its status code before and after, or that its content changed."""
    code_before, code_after = (before or "clean")[:2].strip() or "clean", (after or "clean")[:2].strip() or "clean"
    if before is not None and after is not None and code_before == code_after:
        return f"{name}: content changed (status {code_after})"
    return f"{name}: status {code_before} -> {code_after}"


def check_worktree_unchanged(before: WorktreeSnapshot) -> None:
    """Raise InvariantViolation, which names each change, when the worktree differs from the snapshot `before`."""
    after = snapshot_worktree(before.worktree)
    changes = [
        _change(name, before.paths.get(name), after.paths.get(name))
        for name in sorted({*before.paths, *after.paths})
        if before.paths.get(name) != after.paths.get(name)
    ]
    if after.head == before.head and not changes:
        return
    parts = list(changes)
    if after.head != before.head:
        moved = _git(before.worktree, "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", before.head, after.head)
        changes = [f"{path}: changed between the two HEAD commits" for path in moved.splitlines()] + changes
        parts = [f"HEAD moved from {before.head} to {after.head}", *changes]
    raise InvariantViolation(
        f"the verifier changed the real worktree {before.worktree}: " + "; ".join(parts),
        before.worktree, before.head, after.head, changes,
    )
