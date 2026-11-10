"""The git backend of the version-control port in `delegate.ports.vcs`.

Each method runs git through `subprocess`, with the path of the repository or
worktree as the working directory of the command (`git -C`). A command that
fails raises `VcsError` with the command and the message of git.

Every worktree and every copy that this backend makes runs with
`core.fsmonitor=false`, whatever the user sets. With the setting on, git starts a
`git fsmonitor--daemon` for each repository it touches, and the daemon outlives
its directory (issue #30). The copy gets the setting in its own repository,
which no other directory shares.

A worktree gets its settings (`core.fsmonitor=false`, and the `core.hooksPath`
of the push guard) in a file of its own, `delegate.config` in the git directory
of the worktree (`.git/worktrees/<name>/`). The backend writes no `includeIf`
section to the configuration of the repository: Claude Code refuses each
worktree-isolated agent spawn in a repository whose configuration holds one
(#221). `worktree_environment` gives the environment entries instead
(`GIT_CONFIG_COUNT`, `GIT_CONFIG_KEY_<n>` and `GIT_CONFIG_VALUE_<n>`) that
include the file with an `includeIf "gitdir:<git directory of the worktree>"`
condition, so git reads the file in that worktree only. The engine gives the
entries to each agent session and each gate command of the ticket. The settings
hold for the processes that the engine starts, and a shell of a person in the
worktree reads none of them. The entries need git 2.31 or later, the version
that added `GIT_CONFIG_COUNT`. The backend was tested with git 2.54.0 only.

The git commands of the backend run with `core.fsmonitor=false`, because they
do not get the entries. The backend does not set `extensions.worktreeConfig`,
because tools that use go-git, such as `tea`, refuse a repository with that
extension (issue #84). Git ignores an include whose file is missing, so the
settings go when the worktree goes.

An earlier version wrote an `includeIf` section for each worktree, with the path
of its `delegate.config` as the value. `ensure_worktree` and `remove_worktree`
remove each such section, also when the file exists: the entries of
`worktree_environment` give the same settings. `prune_worktrees` removes the
sections whose file went with the worktree. A section of a person stays.

An earlier version set `extensions.worktreeConfig` and wrote the settings to
the `config.worktree` file of each worktree. `ensure_worktree` moves the
settings of such a worktree to its include file. It then removes the extension
when no `config.worktree` file of the repository holds a setting, and when the
repository holds the hooks of an earlier run (`delegate/hooks/` in the shared
data directory) or a moved file. So an extension that a person set for a
setting of their own stays.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..ports.vcs import CONFLICT, FAILED, REBASED, Commit, PathChange, RebaseResult, VcsError

# The limit, in seconds, for the command that stops the fsmonitor daemon of a directory.
_FSMONITOR_STOP_TIMEOUT: int = 30


def _run(directory: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in `directory` and return the result, whatever the exit code is.

    The command runs with `core.fsmonitor=false`: a worktree gets its settings through the environment of the processes
    that the engine starts, and a command of the backend has none of them.
    """
    return subprocess.run(["git", "-C", str(directory), "-c", "core.fsmonitor=false", *args], capture_output=True, text=True)


def _git_raw(directory: Path, *args: str) -> str:
    """Run git in `directory` and return its output as it is. Raise VcsError when git fails."""
    result = _run(directory, *args)
    if result.returncode != 0:
        operation, cause = f"git {' '.join(args)}", result.stderr.strip() or str(result.returncode)
        raise VcsError(f"{operation} failed: {cause}", operation=operation, directory=directory, cause=cause)
    return result.stdout


def _git(directory: Path, *args: str) -> str:
    """Run git in `directory` and return its output without the surrounding white space. Raise VcsError when git fails."""
    return _git_raw(directory, *args).strip()


# The file in the git directory of a worktree that holds the settings of that worktree alone.
_INCLUDE_NAME: str = "delegate.config"

# The settings that an earlier version wrote to the `config.worktree` file of its worktrees.
_LEGACY_KEYS: frozenset[str] = frozenset({"core.fsmonitor", "core.hookspath"})


def _gitdir_pattern(git_dir: Path) -> str:
    """The `includeIf` condition that holds for the git directory `git_dir` only: no wildcard, no trailing `/`."""
    escaped = "".join("\\" + c if c in "*?[\\" else c for c in str(git_dir))
    return f"gitdir:{escaped}"


def _worktree_git_dir(worktree: Path) -> Path:
    """The real path of the git directory of a checkout: `.git/worktrees/<name>` for a linked worktree.

    A linked worktree holds a `.git` file with one line, `gitdir: <path>`, and the path can be relative to the
    worktree (gitrepository-layout(5)). The backend reads that file, so the engine starts no git process for the
    git directory of a worktree that it made (#239). For any other form (a `.git` directory, a file of another form,
    or a path that is not a directory), git gives the answer.
    """
    gitfile = worktree / ".git"
    try:
        text = gitfile.read_text() if gitfile.is_file() else ""
    except (OSError, UnicodeDecodeError):
        text = ""
    first, _, rest = text.partition("\n")
    if first.startswith("gitdir: ") and not rest.strip():
        found = (worktree / first[len("gitdir: "):]).resolve()
        if found.is_dir():
            return found
    return Path(_git(worktree, "rev-parse", "--absolute-git-dir")).resolve()


def _set_worktree_setting(repo: Path, git_dir: Path, key: str, value: str) -> None:
    """Set `key` in the include file of the checkout of `git_dir`. The configuration of the repository stays as it was."""
    _git(repo, "config", "--file", str(git_dir / _INCLUDE_NAME), key, value)


def _entries(file: Path) -> list[tuple[str, str]]:
    """The entries of a git configuration file, as (key, value) pairs. The keys are in lower case, as git gives them."""
    out = _git_raw(file.parent, "config", "--file", str(file), "--null", "--list")
    return [(key, value) for key, _, value in (entry.partition("\n") for entry in out.split("\0") if entry)]


def _drop_includes(repo: Path, common: Path, *, only_missing: bool) -> None:
    """Remove each `includeIf` section that an earlier version of this backend wrote to the configuration of the repository.

    Such a section has the value `<git common dir>/worktrees/<name>/delegate.config`. With `only_missing`, a section
    stays when its file exists. Each other section stays, also one with a file of that name elsewhere.
    """
    out = _run(repo, "config", "--local", "--null", "--get-regexp", r"^includeif\..*\.path$").stdout
    for entry in filter(None, out.split("\0")):
        key, _, value = entry.partition("\n")
        include = Path(value)
        if include.name == _INCLUDE_NAME and include.parent.parent == common / "worktrees" and not (only_missing and include.exists()):
            # The key is `includeif.<condition>.path`, and git keeps the condition as it was written.
            _git(repo, "config", "--local", "--remove-section", "includeIf." + key[len("includeif."):-len(".path")])


def _retire_worktree_extension(repo: Path, common: Path) -> None:
    """Move the settings that an earlier version kept in `config.worktree` files, and remove the extension that they needed.

    A `config.worktree` file of a linked worktree that holds `core.fsmonitor=false`
    and, as its only other setting, a `core.hooksPath` below `delegate/hooks/`
    of the shared data directory is a file of this backend. Its settings go to
    the include file of the worktree, and the file goes. A file with any other
    setting stays, and so does the extension.
    """
    if _run(repo, "config", "--local", "--type=bool", "--get", "extensions.worktreeConfig").stdout.strip() != "true":
        return
    hooks_root = common / "delegate" / "hooks"
    admins = sorted((common / "worktrees").iterdir()) if (common / "worktrees").is_dir() else []
    moved, needed = False, False
    for file in [common / "config.worktree", *(admin / "config.worktree" for admin in admins)]:
        entries = _entries(file) if file.is_file() else []
        if not entries:
            continue
        keys = [key for key, _ in entries]
        ours = (
            file.parent.parent == common / "worktrees"
            and set(keys) <= _LEGACY_KEYS
            and len(set(keys)) == len(keys)
            and ("core.fsmonitor", "false") in entries
            and all(Path(value).is_relative_to(hooks_root) for key, value in entries if key == "core.hookspath")
        )
        if not ours:
            needed = True
            continue
        for key, value in entries:
            _set_worktree_setting(repo, file.parent, "core.hooksPath" if key == "core.hookspath" else key, value)
        file.unlink()
        moved = True
    if not needed and (moved or hooks_root.is_dir()):
        _git(repo, "config", "--local", "--unset", "extensions.worktreeConfig")


def _rebase_in_progress(worktree: Path) -> bool:
    """Tell whether a rebase is in progress in the worktree. `git rebase --abort` fails when none is."""
    paths = _git(worktree, "rev-parse", "--git-path", "rebase-merge", "--git-path", "rebase-apply").splitlines()
    return any((worktree / path).exists() for path in paths)


class GitVersionControl:
    """The version-control port, kept by git."""

    def repository_root(self, path: Path) -> Path:
        return Path(_git(path, "rev-parse", "--show-toplevel"))

    def shared_data_directory(self, path: Path) -> Path:
        # One git process: the engine asks for this directory many times in each run (#229).
        return Path(_git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")).resolve()

    def repository_paths(self, path: Path) -> tuple[Path, Path]:
        # One git process for the two paths that each command looks up (#239).
        top, common = _git(path, "rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir").splitlines()
        return Path(top), Path(common).resolve()

    def branch_exists(self, repo: Path, branch: str) -> bool:
        return _run(repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0

    def default_branch(self, repo: Path) -> str | None:
        remote = _run(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
        if remote.returncode == 0:
            # `origin/main` names the local branch `main`. A remote name that is not `origin` cannot be here: the ref is under `origin`.
            name = remote.stdout.strip().removeprefix("origin/")
            return name if name and self.branch_exists(repo, name) else None
        # The first entry of the list of worktrees is the main checkout.
        listing = _git(repo, "worktree", "list", "--porcelain").splitlines()
        main = Path(listing[0].removeprefix("worktree ")) if listing and listing[0].startswith("worktree ") else repo
        # Exit status 1 means a detached HEAD: no branch.
        current = _run(main, "symbolic-ref", "--quiet", "--short", "HEAD")
        return current.stdout.strip() or None

    def create_branch(self, repo: Path, branch: str, start: str) -> None:
        _git(repo, "branch", branch, start)

    def branch_tip(self, repo: Path, branch: str) -> str:
        return _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}")

    def commit_of(self, path: Path, ref: str) -> str:
        return _git(path, "rev-parse", "--verify", f"{ref}^{{commit}}")

    def head_commit(self, worktree: Path) -> str:
        return _git(worktree, "rev-parse", "HEAD")

    def ensure_worktree(self, repo: Path, worktree: Path, branch: str, start: str) -> None:
        worktree = worktree.absolute()
        if worktree.is_dir():
            if _rebase_in_progress(worktree):
                _git(worktree, "rebase", "--abort")
            current = _git(worktree, "rev-parse", "--abbrev-ref", "HEAD")
            if current != branch:
                raise VcsError(f"worktree {worktree} holds {current!r}, not the branch {branch!r} of its ticket")
        else:
            _git(repo, "worktree", "prune")
            if self.branch_exists(repo, branch):
                _git(repo, "worktree", "add", "-q", str(worktree), branch)
            else:
                _git(repo, "worktree", "add", "-q", "-b", branch, str(worktree), start)
        _set_worktree_setting(repo, _worktree_git_dir(worktree), "core.fsmonitor", "false")
        common = self.shared_data_directory(repo)
        _retire_worktree_extension(repo, common)
        _drop_includes(repo, common, only_missing=False)

    def remove_worktree(self, repo: Path, worktree: Path) -> None:
        worktree = worktree.absolute()
        self.stop_file_watcher(worktree)
        _git(repo, "worktree", "remove", "--force", str(worktree))
        _drop_includes(repo, self.shared_data_directory(repo), only_missing=False)

    def delete_branch(self, repo: Path, branch: str, holder: str) -> bool:
        if not self.is_ancestor(repo, f"refs/heads/{branch}", f"refs/heads/{holder}"):
            return False
        # `git branch -d` checks the branch against its upstream, and against HEAD when it has none. HEAD is the
        # branch of the main checkout, so the holder becomes the upstream: git then checks the holder, and never
        # deletes a commit that the holder does not hold. The setting goes with the branch.
        _git(repo, "branch", "--quiet", "--set-upstream-to", holder, branch)
        try:
            _git(repo, "branch", "--quiet", "-d", branch)
        except VcsError:
            _run(repo, "branch", "--unset-upstream", branch)
            raise
        return True

    def discard_branch(self, repo: Path, branch: str) -> None:
        _git(repo, "branch", "--quiet", "-D", branch)

    def prune_worktrees(self, repo: Path) -> None:
        _git(repo, "worktree", "prune")
        _drop_includes(repo, self.shared_data_directory(repo), only_missing=True)

    def rebase_onto(self, worktree: Path, onto: str) -> RebaseResult:
        rebase = _run(worktree, "rebase", onto)
        if rebase.returncode == 0:
            return RebaseResult(REBASED)
        # A list of conflicts that git cannot give is an error, and the rebase is left as it is.
        conflicts = tuple(_git(worktree, "diff", "--name-only", "--diff-filter=U").splitlines())
        if _rebase_in_progress(worktree):
            _git(worktree, "rebase", "--abort")
        if conflicts:
            return RebaseResult(CONFLICT, conflicts=conflicts)
        return RebaseResult(FAILED, detail=rebase.stderr.strip() or str(rebase.returncode))

    def commits_since(self, path: Path, since: str, until: str | None = None) -> list[Commit]:
        # The trailing `--` tells git that both names are revisions, even when a file has the same name.
        out = _git(path, "log", "--format=%H %s", f"{since}..{until or 'HEAD'}", "--")
        commits = []
        for line in out.splitlines():
            commit_id, _, subject = line.partition(" ")
            commits.append(Commit(commit_id, subject))
        return commits

    def changed_paths(
        self, path: Path, since: str, until: str | None = None, *, from_merge_base: bool = False, follow_renames: bool = False
    ) -> list[str]:
        spec = f"{since}{'...' if from_merge_base else '..'}{until or 'HEAD'}"
        if follow_renames:
            return _git_raw(path, "diff", "--name-only", spec).splitlines()
        return _git(path, "-c", "core.quotepath=off", "diff", "--name-only", "--no-renames", spec).splitlines()

    def diff_text(self, path: Path, since: str, until: str, *, from_merge_base: bool = False) -> str:
        return _git_raw(path, "diff", f"{since}{'...' if from_merge_base else '..'}{until}")

    def diff_stat(self, path: Path, since: str, until: str, *, from_merge_base: bool = False) -> str:
        return _git_raw(path, "diff", "--stat=200", f"{since}{'...' if from_merge_base else '..'}{until}")

    def uncommitted_changes(self, worktree: Path) -> list[PathChange]:
        # The state code starts with a space for a change that is not staged, so the output must not be stripped.
        out = _git_raw(worktree, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--no-renames")
        return [PathChange(entry[:2], entry[3:]) for entry in out.split("\0") if entry]

    def restore_tracked(self, worktree: Path) -> None:
        _git(worktree, "reset", "--hard", "--quiet", "HEAD")

    def hooks_directory(self, worktree: Path) -> Path:
        return Path(_git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "hooks"))

    def use_hooks_directory(self, repo: Path, worktree: Path, hooks: Path) -> None:
        _set_worktree_setting(repo, _worktree_git_dir(worktree), "core.hooksPath", str(hooks))

    def worktree_environment(self, worktree: Path) -> dict[str, str]:
        git_dir = _worktree_git_dir(worktree)
        text = os.environ.get("GIT_CONFIG_COUNT", "0")
        if not text.isascii() or not text.isdigit():
            raise VcsError(f"GIT_CONFIG_COUNT must be a number of entries, not {text!r}")
        count = int(text)
        return {
            "GIT_CONFIG_COUNT": str(count + 1),
            f"GIT_CONFIG_KEY_{count}": f"includeIf.{_gitdir_pattern(git_dir)}.path",
            f"GIT_CONFIG_VALUE_{count}": str(git_dir / _INCLUDE_NAME),
        }

    def is_ancestor(self, repo: Path, ancestor: str, descendant: str) -> bool:
        # Exit status 1 means no. Any other failure, such as a name that is no commit, is an error.
        result = _run(repo, "merge-base", "--is-ancestor", ancestor, descendant)
        if result.returncode == 0:
            return True
        if result.returncode == 1:
            return False
        operation, cause = f"git merge-base --is-ancestor {ancestor} {descendant}", result.stderr.strip() or str(result.returncode)
        raise VcsError(f"{operation} failed: {cause}", operation=operation, directory=repo, cause=cause)

    def fast_forward_branch(self, repo: Path, branch: str, to_commit: str, tip: str | None = None) -> bool:
        if tip is None:
            tip = self.branch_tip(repo, branch)
        # Any failure of the ancestor check, such as a `to_commit` that names no commit, is the answer no.
        if _run(repo, "merge-base", "--is-ancestor", tip, to_commit).returncode != 0:
            return False
        # `^{commit}` gives the branch the commit, also for a name of a tag. The old value in the last argument makes the
        # move fail if the branch is not at `tip`: it moved in the meantime, or the branch does not exist.
        _git(repo, "update-ref", f"refs/heads/{branch}", f"{to_commit}^{{commit}}", tip)
        return True

    def make_verifier_copy(self, repo: Path, copy: Path, branch: str, base: str) -> None:
        # The clone starts on the branch that the HEAD of the repository names, and that branch can be `base`.
        # So the clone checks out `branch` first, which frees the other names, and then sets `base` by force.
        copy = copy.absolute()
        existed = copy.exists()
        try:
            _git(repo, "clone", "-q", "-c", "core.fsmonitor=false", "--no-checkout", str(repo), str(copy))
            _git(copy, "checkout", "-q", "-B", branch, f"origin/{branch}")
            _git(copy, "branch", "-f", base, f"origin/{base}")
            _git(copy, "remote", "remove", "origin")
        except VcsError:
            # A failed copy leaves nothing behind, but a directory that was there before stays.
            if not existed:
                self.stop_file_watcher(copy)
                shutil.rmtree(copy, ignore_errors=True)
            raise

    def make_scratch_repository(self, path: Path) -> None:
        path = path.absolute()
        existed = path.exists()
        try:
            path.mkdir(parents=True, exist_ok=True)
            _git(path, "init", "-q", "-b", "main")
            _git(path, "config", "core.fsmonitor", "false")
            _git(
                path, "-c", "user.name=delegate", "-c", "user.email=delegate@example.invalid", "-c", "commit.gpgsign=false",
                "commit", "-q", "--allow-empty", "-m", "scratch",
            )
        except (VcsError, OSError) as error:
            if not existed:
                self.stop_file_watcher(path)
                shutil.rmtree(path, ignore_errors=True)
            if isinstance(error, OSError):
                raise VcsError(f"the scratch repository {path} cannot be made: {error}", directory=path, cause=str(error)) from None
            raise

    def stop_file_watcher(self, path: Path) -> None:
        # Git exits with an error when no daemon runs, or when the directory is not a repository. Both are fine.
        try:
            subprocess.run(
                ["git", "-C", str(path), "fsmonitor--daemon", "stop"],
                capture_output=True, text=True, timeout=_FSMONITOR_STOP_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            pass
