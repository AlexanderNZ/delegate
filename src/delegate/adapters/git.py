"""The git backend of the version-control port in `delegate.ports.vcs`.

Each method runs git through `subprocess`, with the path of the repository or
worktree as the working directory of the command (`git -C`). A command that
fails raises `VcsError` with the command and the message of git. Each command reads no stdin, asks for no
credential on a terminal (`GIT_TERMINAL_PROMPT=0`), and has a time limit: 10 minutes, and 30 minutes for the clone of
a verifier copy and for the fetch, the checkout and the clean that update it (#349). A command that reaches its limit
raises `VcsError` with "timed out after N seconds" (#295). The command that stops the fsmonitor daemon has its own limit.

Every worktree and every copy that this backend makes runs with
`core.fsmonitor=false`, whatever the user sets. With the setting on, git starts a
`git fsmonitor--daemon` for each repository it touches, and the daemon outlives
its directory (issue #30). The copy gets the setting in its own repository,
which no other directory shares.

A worktree gets its settings (`core.fsmonitor=false`, and the `core.hooksPath`
of the push guard) in a file of its own, `delegate.config` in the git directory
of the worktree (`.git/worktrees/<name>/`). The backend writes the file itself, in one step and with no git process (#348), and
it asks git to write a file that it did not write. The backend writes no `includeIf`
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
import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

from ..ports.vcs import CONFLICT, FAILED, REBASED, Commit, PathChange, RebaseResult, VcsError, WorktreeState

# The limit, in seconds, for the command that stops the fsmonitor daemon of a directory.
_FSMONITOR_STOP_TIMEOUT: int = 30

# The limit, in seconds, for each other git command of the backend: 10 minutes (#295).
_GIT_TIMEOUT: int = 600

# The limit, in seconds, for the clone of a verifier copy: 30 minutes. The clone copies the whole history, and a
# filter, such as an LFS smudge, can need more time than a command that reads the repository.
_CLONE_TIMEOUT: int = 1800


def _environment() -> dict[str, str]:
    """The environment of a git command of the backend: git asks for no credential on a terminal."""
    return {**os.environ, "GIT_TERMINAL_PROMPT": "0"}


def _run(directory: Path, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    """Run git in `directory` and return the result, whatever the exit code is.

    The command runs with `core.fsmonitor=false`: a worktree gets its settings through the environment of the processes
    that the engine starts, and a command of the backend has none of them.

    The command reads no stdin (a hook, a signing program or a filter cannot wait on the stdin of the engine), starts
    no prompt for a credential, and has a time limit: `timeout` seconds, or `_GIT_TIMEOUT`. A command that reaches the
    limit is a `VcsError`.
    """
    limit = _GIT_TIMEOUT if timeout is None else timeout
    command = ["git", "-C", str(directory), "-c", "core.fsmonitor=false", *args]
    try:
        return subprocess.run(
            command, capture_output=True, text=True, stdin=subprocess.DEVNULL, env=_environment(), timeout=limit
        )
    except subprocess.TimeoutExpired:
        operation = f"git {' '.join(args)}"
        cause = f"timed out after {limit} seconds"
        raise VcsError(
            f"{operation} in {directory} {cause}", operation=operation, directory=directory, cause=cause
        ) from None


def _failure(directory: Path, args: Sequence[str], exit_code: int, stderr: str) -> VcsError:
    """The `VcsError` for a git command that ended with `exit_code`, not 0, and wrote `stderr`."""
    operation, cause = f"git {' '.join(args)}", stderr.strip() or str(exit_code)
    return VcsError(f"{operation} failed: {cause}", operation=operation, directory=directory, cause=cause)


def _git_raw(directory: Path, *args: str, timeout: int | None = None) -> str:
    """Run git in `directory` and return its output as it is. Raise VcsError when git fails."""
    result = _run(directory, *args, timeout=timeout)
    if result.returncode != 0:
        raise _failure(directory, args, result.returncode, result.stderr)
    return result.stdout


# The prefix of the ref of a local branch, which is also the pattern that lists them.
_HEADS: str = "refs/heads/"


def _branch_tips(listing: str) -> dict[str, str]:
    """The branches of the lines `<commit> refs/heads/<name>`, with the commit of each."""
    tips: dict[str, str] = {}
    for line in listing.splitlines():
        commit, _, ref = line.partition(" ")
        tips[ref.removeprefix(_HEADS)] = commit
    return tips


def _git(directory: Path, *args: str, timeout: int | None = None) -> str:
    """Run git in `directory` and return its output without the surrounding white space. Raise VcsError when git fails."""
    return _git_raw(directory, *args, timeout=timeout).strip()


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


# The settings that the backend writes to the include file, in the order of the file, with the name that the file shows.
_SETTING_NAMES: dict[str, str] = {"core.fsmonitor": "fsmonitor", "core.hookspath": "hooksPath"}

# A value that needs no quotes in a git configuration file.
_PLAIN_VALUE = re.compile(r"[A-Za-z0-9_./:@+-]+")

_ESCAPES: dict[str, str] = {"\\": "\\", '"': '"', "n": "\n", "t": "\t"}


def _quote_value(value: str) -> str:
    """The value as the line `key = value` of a git configuration file holds it: quoted, with escapes, unless it is plain."""
    if _PLAIN_VALUE.fullmatch(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'


def _unquote_value(text: str) -> str | None:
    """The value that `_quote_value` wrote as `text`, or None when `text` is not in the form that it writes."""
    if _PLAIN_VALUE.fullmatch(text):
        return text
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        return None
    out: list[str] = []
    chars = iter(text[1:-1])
    for char in chars:
        if char == '"':
            return None
        if char == "\\":
            escape = _ESCAPES.get(next(chars, ""))
            if escape is None:
                return None
            char = escape
        out.append(char)
    return "".join(out)


def _read_settings(file: Path) -> dict[str, str] | None:
    """The settings of an include file that the backend wrote, as (lower-case key, value). None when the file holds anything else.

    A missing file holds no setting. A file that a person changed, with a comment or a key of another kind, is not read:
    the caller then lets git write the setting and keep the rest.
    """
    try:
        text = file.read_text() if file.exists() else ""
    except (OSError, UnicodeDecodeError):
        return None
    settings: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.lower() == "[core]":
            continue
        name, equals, raw = line.partition(" = ")
        key, value = f"core.{name.lower()}", _unquote_value(raw)
        if not equals or key not in _SETTING_NAMES or value is None:
            return None
        settings[key] = value
    return settings


def _write_settings(repo: Path, git_dir: Path, settings: dict[str, str]) -> None:
    """Set each of `settings` (key in the form `core.hooksPath`) in the include file of the checkout of `git_dir`, with no git process.

    The configuration of the repository stays as it was. The file is replaced in one step, so a git process that
    reads it never sees half of it. A file that the backend did not write is changed with `git config`, which keeps the rest of it.
    """
    file = git_dir / _INCLUDE_NAME
    found = _read_settings(file)
    if found is None:
        for key, value in settings.items():
            _git(repo, "config", "--file", str(file), key, value)
        return
    wanted = {**found, **{key.lower(): value for key, value in settings.items()}}
    if wanted == found and file.exists():
        return
    lines = ["[core]"] + [f"\t{name} = {_quote_value(wanted[key])}" for key, name in _SETTING_NAMES.items() if key in wanted]
    temporary = file.with_name(f"{file.name}.{os.getpid()}")
    temporary.write_text("\n".join(lines) + "\n")
    os.replace(temporary, file)


def _set_worktree_setting(repo: Path, git_dir: Path, key: str, value: str) -> None:
    """Set `key` in the include file of the checkout of `git_dir`. The configuration of the repository stays as it was."""
    _write_settings(repo, git_dir, {key: value})


def _entries(file: Path) -> list[tuple[str, str]]:
    """The entries of a git configuration file, as (key, value) pairs. The keys are in lower case, as git gives them."""
    out = _git_raw(file.parent, "config", "--file", str(file), "--null", "--list")
    return [(key, value) for key, _, value in (entry.partition("\n") for entry in out.split("\0") if entry)]


def _may_set(common: Path, word: str) -> bool:
    """Tell whether the configuration file of the repository can hold a setting with `word` in its name, or include a file that can.

    A file with no `word` and no `include` cannot hold the setting, so the caller asks git nothing. When the file cannot be
    read, the answer is yes, and git gives the answer.
    """
    try:
        text = (common / "config").read_text().lower()
    except (OSError, UnicodeDecodeError):
        return True
    return word in text or "include" in text


def _may_include(common: Path) -> bool:
    """Tell whether the configuration file of the repository can hold an `includeIf` section. See `_may_set`."""
    return _may_set(common, "include")


def _drop_includes(repo: Path, common: Path, *, only_missing: bool) -> None:
    """Remove each `includeIf` section that an earlier version of this backend wrote to the configuration of the repository.

    Such a section has the value `<git common dir>/worktrees/<name>/delegate.config`. With `only_missing`, a section
    stays when its file exists. Each other section stays, also one with a file of that name elsewhere.
    """
    if not _may_include(common):
        return
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
    if not _may_set(common, "worktreeconfig"):
        return
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


def _nothing_to_prune(repo: Path) -> bool:
    """Tell whether `git worktree prune` has nothing to do: each record of a worktree names a directory that exists.

    Only a main checkout whose `.git` is a directory is read. For any other `repo`, and for a record in a form that this
    function does not know, the answer is no, and git decides.
    """
    records = repo / ".git" / "worktrees"
    if not (repo / ".git").is_dir():
        return False
    if not records.is_dir():
        return True
    for record in records.iterdir():
        try:
            target = (record / "gitdir").read_text().strip()
        except (OSError, UnicodeDecodeError):
            return False
        if not target or not Path(target).exists():
            return False
    return True


def _rebase_in_progress(worktree: Path) -> bool:
    """Tell whether a rebase is in progress in the worktree. `git rebase --abort` fails when none is."""
    paths = _git(worktree, "rev-parse", "--git-path", "rebase-merge", "--git-path", "rebase-apply").splitlines()
    return any((worktree / path).exists() for path in paths)


# The settings in the git directory of a verifier copy that can make git start a program or work in another repository:
# the configuration, the names of the hooks, the files of `info` (attributes can name a filter of the configuration),
# and whether a `commondir` file sends git to another git directory. None when they cannot be read.
_CopySettings = tuple[bytes, tuple[str, ...], tuple[tuple[str, bytes], ...], bool] | None

# The hooks of the git commands that update a verifier copy: a path with no hook in it, so no hook of the copy runs.
_NO_HOOKS: tuple[str, str] = ("-c", "core.hooksPath=/dev/null")


def _settings_of_copy(copy: Path) -> _CopySettings:
    """The settings in the git directory of the verifier copy `copy`, read with no git process."""
    git_dir = copy / ".git"
    try:
        hooks = tuple(sorted(entry.name for entry in (git_dir / "hooks").iterdir())) if (git_dir / "hooks").is_dir() else ()
        info = git_dir / "info"
        files = tuple(sorted((entry.name, entry.read_bytes()) for entry in info.iterdir())) if info.is_dir() else ()
        return (git_dir / "config").read_bytes(), hooks, files, (git_dir / "commondir").exists()
    except OSError:
        return None


# The files that let git read objects or parents that the refs of a copy do not hold.
_OTHER_HISTORY_FILES: tuple[str, ...] = ("objects/info/alternates", "objects/info/http-alternates", "info/grafts", "shallow")


def _reads_other_history(copy: Path) -> bool:
    """Tell whether the git directory of the verifier copy `copy` can make git show content or history that its refs do not hold.

    A replace ref, a file of alternates, a grafts file or a shallow file can each do that (review of #349). The check
    reads the files with no git process. A copy with refs in the reftable format needs one process to list its replace refs.
    """
    git_dir = copy / ".git"
    try:
        if any((git_dir / name).exists() for name in _OTHER_HISTORY_FILES):
            return True
        if (git_dir / "reftable").exists():
            listing = _run(copy, *_NO_HOOKS, "for-each-ref", "--count=1", "refs/replace/")
            return listing.returncode != 0 or listing.stdout != ""
        replace = git_dir / "refs" / "replace"
        if replace.is_dir() and any(path.is_file() for path in replace.rglob("*")):
            return True
        packed = git_dir / "packed-refs"
        return packed.exists() and b" refs/replace/" in packed.read_bytes()
    except OSError:
        return True


def _drop_index_flags(copy: Path) -> None:
    """Remove the index of the verifier copy `copy` when it can hold a flag that hides a changed file from the status.

    Git writes the index in version 2 unless an entry has an extended flag, as `skip-worktree` or `intent-to-add`, or a
    setting asks for a later version. With the flag, `checkout -f` keeps the file of the writer, and the status shows
    the copy clean. With no index, `checkout -f` writes each tracked file again.
    """
    index = copy / ".git" / "index"
    try:
        with index.open("rb") as file:
            header = file.read(8)
    except OSError:
        return
    if header[:4] != b"DIRC" or int.from_bytes(header[4:8], "big") != 2:
        index.unlink(missing_ok=True)


def _reset_verifier_copy(repo: Path, copy: Path, branch: str, base: str, tip: str | None) -> bool:
    """Bring the verifier copy `copy` to the branches `branch` and `base` of `repo`, with no file that a verifier left (#349).

    The copy fetches the two branches from `repo` by force, checks out `branch` by force, and removes each untracked
    and ignored file. Return True when the copy is then clean and on `branch` at the commit `tip`, and False when a
    step failed or the copy is not so: the caller then makes the copy again. With no `tip`, the tip of `branch` in
    `repo` before the fetch is the commit.
    """
    if tip is None:
        read = _run(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}^{{commit}}")
        if read.returncode != 0:
            return False
        tip = read.stdout.strip()
    steps = (
        (
            "fetch", "-q", "--no-tags", "--no-write-fetch-head", "--no-auto-maintenance", "--update-head-ok", str(repo),
            f"+refs/heads/{branch}:refs/heads/{branch}", f"+refs/heads/{base}:refs/heads/{base}",
        ),
        ("checkout", "-q", "-f", "-B", branch, f"refs/heads/{branch}"),
        ("clean", "-q", "-ffdx"),
    )
    _drop_index_flags(copy)
    for args in steps:
        if _run(copy, *_NO_HOOKS, *args, timeout=_CLONE_TIMEOUT).returncode != 0:
            return False
    status = _run(copy, *_NO_HOOKS, "status", "--porcelain=v2", "--branch", "--ignored", "--untracked-files=all")
    lines = status.stdout.splitlines()
    return (
        status.returncode == 0 and f"# branch.oid {tip}" in lines and f"# branch.head {branch}" in lines
        and all(line.startswith("# ") for line in lines)
    )


class GitVersionControl:
    """The version-control port, kept by git."""

    def __init__(self) -> None:
        # The settings in the git directory of each verifier copy that this backend made, by the path of the copy (#349).
        self._copy_settings: dict[Path, _CopySettings] = {}

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

    def branch_tips(self, repo: Path) -> dict[str, str]:
        return _branch_tips(_git(repo, "for-each-ref", "--format=%(objectname) %(refname)", _HEADS))

    def branches_held_by(self, repo: Path, holder: str) -> frozenset[str]:
        # `--merged` lists the refs whose newest commit the holder holds: one process for all branches.
        listing = _git(repo, "for-each-ref", f"--merged=refs/heads/{holder}", "--format=%(objectname) %(refname)", _HEADS)
        return frozenset(_branch_tips(listing))

    def default_branch(self, repo: Path) -> str | None:
        # One read gives the name that origin/HEAD points to and the local branches (#347).
        listing = _git(repo, "for-each-ref", "--format=%(refname) %(symref)", "refs/remotes/origin/HEAD", _HEADS)
        remote: str | None = None
        local: set[str] = set()
        for line in listing.splitlines():
            ref, _, target = line.partition(" ")
            if ref == "refs/remotes/origin/HEAD":
                remote = target.removeprefix("refs/remotes/origin/")
            elif ref.startswith(_HEADS):
                local.add(ref.removeprefix(_HEADS))
        if remote is not None:
            # `origin/main` names the local branch `main`. A remote name that is not `origin` cannot be here: the ref is under `origin`.
            return remote if remote in local else None
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
            self._add_worktree(repo, worktree, branch, start)
        git_dir = _worktree_git_dir(worktree)
        _set_worktree_setting(repo, git_dir, "core.fsmonitor", "false")
        # `.git/worktrees/<name>` of a linked worktree is below the shared data directory, so no process gives it (#348).
        common = git_dir.parent.parent if git_dir.parent.name == "worktrees" else self.shared_data_directory(repo)
        _retire_worktree_extension(repo, common)
        _drop_includes(repo, common, only_missing=False)

    def _add_worktree(self, repo: Path, worktree: Path, branch: str, start: str) -> None:
        """Make the worktree on `branch`, which starts at `start` when it does not exist.

        The first try makes the branch. It fails when the branch exists, and then the worktree checks it out. The
        common case, a branch that is new, so starts one process for the check and the add together (#348).
        """
        if not _nothing_to_prune(repo):
            _git(repo, "worktree", "prune")
        made = _run(repo, "worktree", "add", "-q", "-b", branch, str(worktree), start)
        if made.returncode == 0:
            return
        if self.branch_exists(repo, branch):
            _git(repo, "worktree", "add", "-q", str(worktree), branch)
            return
        raise _failure(repo, ("worktree", "add", "-q", "-b", branch, str(worktree), start), made.returncode, made.stderr)

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

    def discard_branches(self, repo: Path, branches: Sequence[str]) -> None:
        if branches:
            _git(repo, "branch", "--quiet", "-D", *branches)

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

    def worktree_state(self, worktree: Path) -> WorktreeState:
        # Version 2 of the status gives the commit in its `# branch.oid` header, so one process serves both reads (#348).
        # The state code of version 2 shows an unchanged side as `.`, and of version 1 as a space.
        out = _git_raw(worktree, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all", "--no-renames")
        head, changes = "", []
        for entry in filter(None, out.split("\0")):
            kind, _, rest = entry.partition(" ")
            if kind == "#":
                name, _, value = rest.partition(" ")
                head = value if name == "branch.oid" else head
            elif kind == "?":
                changes.append(PathChange("??", rest))
            elif kind == "1":  # `1 <XY> <sub> <mH> <mI> <mW> <hH> <hI> <path>`
                fields = rest.split(" ", 7)
                changes.append(PathChange(fields[0].replace(".", " "), fields[7]))
            elif kind == "u":  # `u <XY> <sub> <m1> <m2> <m3> <mW> <h1> <h2> <h3> <path>`
                fields = rest.split(" ", 9)
                changes.append(PathChange(fields[0].replace(".", " "), fields[9]))
        if not re.fullmatch(r"[0-9a-f]{40,64}", head):  # a checkout with no commit has `(initial)`
            operation = "git status --porcelain=v2 --branch"
            raise VcsError(f"{operation} failed: {worktree} has no commit", operation=operation, directory=worktree, cause="no commit")
        return WorktreeState(head, changes)

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

    def make_verifier_copy(
        self, repo: Path, copy: Path, branch: str, base: str, *, earlier: Path | None = None, tip: str | None = None
    ) -> None:
        copy = copy.absolute()
        source = copy if earlier is None else earlier.absolute()
        made = self._copy_settings.pop(source, None)
        if made is not None and source.is_dir():
            moved = source == copy
            if not moved:
                try:
                    source.rename(copy)
                    moved = True
                except OSError:
                    pass  # the copy stays where it is, and the caller removes it
            # The update starts git in a directory that a verifier could change, so it goes on only while the settings
            # in the git directory are those that the copy had when it was made (#349), and while no file in it makes git
            # read other objects or other parents. The updated copy must hold the tip of `branch` that the caller read.
            if (
                moved and _settings_of_copy(copy) == made and not _reads_other_history(copy)
                and _reset_verifier_copy(repo, copy, branch, base, tip)
            ):
                self._copy_settings[copy] = made
                return
            if moved:
                self.stop_file_watcher(copy)
                shutil.rmtree(copy, ignore_errors=True)
        # The clone starts on the branch that the HEAD of the repository names, and that branch can be `base`.
        # So the clone checks out `branch` first, which frees the other names, and then sets `base` by force.
        existed = copy.exists()
        try:
            _git(repo, "clone", "-q", "-c", "core.fsmonitor=false", "--no-checkout", str(repo), str(copy), timeout=_CLONE_TIMEOUT)
            _git(copy, "checkout", "-q", "-B", branch, tip or f"origin/{branch}")
            _git(copy, "branch", "-f", base, f"origin/{base}")
            _git(copy, "remote", "remove", "origin")
        except VcsError:
            # A failed copy leaves nothing behind, but a directory that was there before stays.
            if not existed:
                self.stop_file_watcher(copy)
                shutil.rmtree(copy, ignore_errors=True)
            raise
        self._copy_settings[copy] = _settings_of_copy(copy)

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
        # Git exits with an error when no daemon runs, or when the directory is not a repository. Both are fine, and so is a
        # stop command that reaches its limit: the caller removes the directory.
        try:
            _run(path, "fsmonitor--daemon", "stop", timeout=_FSMONITOR_STOP_TIMEOUT)
        except VcsError:
            pass
