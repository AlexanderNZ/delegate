"""Where a command finds the state of a run: the state directory of a repository, and the names of the files of a run in it.

The engine, the views (`status`, `runs`, `clean`, `pause`, `stop`) and the commands of `delegate` all need these paths, and none of them
needs the engine for it (#331). The lookup, `locate_state`, writes nothing: its warning is the `note` of the result, and the driver of
the command prints it (#331).
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from ..ports.vcs import VcsError, VersionControl
from .errors import EngineError
from .journal import JournalError, read_events

# The environment variable of a host that sets the state root of every repository. The default root is outside the git
# directory too (#200), because Claude Code in `dontAsk` mode refuses `Edit` and `Write` on a path with a `.git`
# component, also with an allow rule (#97), and the Vite dev server refuses to serve one.
STATE_ROOT_VARIABLE: str = "DELEGATE_STATE_ROOT"

# The file in `<git common dir>/delegate` that holds the absolute path of the state directory, one line, when the
# engine keeps its state elsewhere (#199). A command in a shell without `DELEGATE_STATE_ROOT` finds the state with it.
STATE_ROOT_FILE: str = "state-root"

# The file of the run directory that holds the traceback of a crash: this prefix, the `seq` of the `crashed` event (of
# the `step-end` in a journal of an earlier version), `.txt`.
CRASH_FILE_PREFIX: str = "crash-"

# The directory of the run that holds the scratch directory of each specialist and fix-up session:
# `<run dir>/scratch/<ticket>/<role>/` (#227).
SCRATCH_DIRECTORY: str = "scratch"

# The prefix of the name of the scratch directory of a verifier session, in the temp directory of the host:
# `delegate-verifier-<run id>-<ticket>-<random>` (#294). See `engine._verifier_scratch_directory`.
VERIFIER_SCRATCH_PREFIX: str = "delegate-verifier-"

# The file in the directory of a run that asks the run to pause at its next step boundary (see `delegate.run.pause`).
PAUSE_REQUEST_NAME: str = "pause-request"


class StateRootError(EngineError):
    """`DELEGATE_STATE_ROOT` has a value that the engine cannot use."""


def _recorded_root(shared: Path) -> Path | None:
    """The state directory that `<git common dir>/delegate/state-root` names, or None when the file does not exist."""
    path = shared / "delegate" / STATE_ROOT_FILE
    try:
        text = path.read_text()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise StateRootError(f"the file {path} cannot be read: {error}") from None
    recorded = text.strip()
    if not recorded or not Path(recorded).is_absolute():
        raise StateRootError(f"the file {path} holds {recorded!r}, which is not an absolute path; remove the file or write the path of the state directory")
    return Path(recorded)


def default_state_root() -> Path:
    """The state root of a host that sets no `DELEGATE_STATE_ROOT`: `$XDG_STATE_HOME/delegate`, or `~/.local/state/delegate`.

    An `XDG_STATE_HOME` that is empty or not an absolute path counts as not set, as the XDG specification says.
    """
    base = os.environ.get("XDG_STATE_HOME", "")
    if base and Path(base).is_absolute():
        return Path(base) / "delegate"
    return Path.home() / ".local" / "state" / "delegate"


def _repository_directory(shared: Path, root: Path) -> Path:
    """`<root>/<name>-<key>`, the state directory of the repository whose git common directory is `shared`.

    `<name>` is the name of the directory that holds `shared` (`.git` is in the top level of the main checkout), or its
    own name when it is not called `.git` (a bare repository or a separate git directory), and `<key>` is the first 12
    hex digits of the SHA-256 of its absolute path.
    """
    name = shared.parent.name if shared.name == ".git" else shared.name
    key = hashlib.sha256(str(shared).encode()).hexdigest()[:12]
    return root / f"{name}-{key}"


@dataclass(frozen=True)
class StateLocation:
    """Where a command finds the repository and the state of its runs: the paths that it looks up one time (#239).

    `top` is the top level of the repository, `shared` its git common directory, and `directories` the state directory
    of a new run, then `<git common dir>/delegate` when that is another directory. `locate_state` looks them up, and a
    command passes the value on to each function that needs the paths. The value holds paths only: each method reads
    the disk again when it is called. No value lives longer than the command that looked it up, because a worktree
    can be made again at the same path.

    `note` is the line that the lookup asks the command to print on standard error, or None: `DELEGATE_STATE_ROOT` and
    the file `state-root` name two directories (#331). The lookup prints nothing. The driver of the command prints the
    note one time.
    """

    top: Path
    shared: Path
    directories: tuple[Path, ...]
    note: str | None = None

    def state_directory(self, run_id: str | None = None) -> Path:
        """The directory where the engine keeps its state, as `state_directory` gives it."""
        if run_id is not None and not (self.directories[0] / "runs" / run_id).is_dir():
            for other in self.directories[1:]:
                if (other / "runs" / run_id).is_dir():
                    return other
        return self.directories[0]

    def run_directories(self) -> list[Path]:
        """Each state directory that can hold a run: the one of `state_directory` first, then `<git common dir>/delegate` when it holds a directory `runs`."""
        return [self.directories[0], *(other for other in self.directories[1:] if (other / "runs").is_dir())]


def state_directory(repo: Path, vcs: VersionControl, run_id: str | None = None) -> tuple[Path, Path]:
    """The top level of the repository and the directory where the engine keeps its state.

    By default the state is outside the git directory (#200), so no path that
    the engine gives to an agent or a gate has a `.git` component. The
    directory is the first of these. When the host sets `DELEGATE_STATE_ROOT`,
    it is `<root>/<name>-<key>`. Else it is the path in
    `<git common dir>/delegate/state-root`, when that file exists. Else it is
    `<root>/<name>-<key>` with the root `default_state_root()`. `<name>` and
    `<key>` come from the git common directory, which every worktree of a
    repository shares: `<name>` is the name of the directory that holds it
    (`.git` is in the top level of the main checkout), or its own name when it
    is not called `.git` (a bare repository or a separate git directory), and
    `<key>` is the first 12 hex digits of the SHA-256 of its absolute path. So
    one root serves every repository of a host, each repository gets its own
    directory, and every worktree of a repository finds the same one.

    A run that started under `<git common dir>/delegate` before #200 stays
    there. With `run_id`, the id of a run that is in that directory and not in
    the directory above, the result is `<git common dir>/delegate`.

    When the variable is set, and `state-root` names another directory, the
    variable wins. This function drops the note about it; a command that
    prints the note uses `locate_state`.

    Raise EngineError when `repo` is not inside a repository, and
    StateRootError when the root, or the path in `state-root`, is not an
    absolute path.
    """
    location = locate_state(repo, vcs)
    return location.top, location.state_directory(run_id)


def locate_state(repo: Path, vcs: VersionControl) -> StateLocation:
    """Look up the top level, the git common directory and the state directories of the repository that holds `repo`.

    The rules are those of `state_directory`. Raise its errors. A command calls this one time and passes the result on.
    The lookup writes nothing: when the variable names another directory than `state-root`, the `note` of the result
    holds the warning line, and the driver of the command prints it (#331).
    """
    try:
        top, shared = vcs.repository_paths(repo)
    except VcsError as error:
        raise EngineError(str(error)) from None
    legacy = shared / "delegate"
    recorded = _recorded_root(shared)
    root = os.environ.get(STATE_ROOT_VARIABLE)
    note = None
    if not root:
        chosen = recorded if recorded is not None else _repository_directory(shared, default_state_root())
    else:
        if not Path(root).is_absolute():
            raise StateRootError(f"{STATE_ROOT_VARIABLE} is {root!r}, which is not an absolute path")
        chosen = _repository_directory(shared, Path(root))
        if recorded is not None and recorded != chosen:
            note = (
                f"delegate: {STATE_ROOT_VARIABLE} names the state directory {chosen}, but {legacy / STATE_ROOT_FILE} names {recorded}; "
                f"using {chosen}"
            )
    return StateLocation(top, shared, (chosen,) if chosen == legacy else (chosen, legacy), note)


def record_state_root(location: StateLocation, state_dir: Path) -> None:
    """Write the path of `state_dir` to `<git common dir>/delegate/state-root` when the state is not in `<git common dir>/delegate`.

    The file is the one line that a command needs to find the state without `DELEGATE_STATE_ROOT`. It is written
    to a private file first, and a rename puts it in place, so a command never reads half a path.
    """
    shared_delegate = location.shared / "delegate"
    if state_dir == shared_delegate:
        return
    path = shared_delegate / STATE_ROOT_FILE
    line = f"{state_dir}\n"
    try:
        if path.exists() and path.read_text() == line:
            return
        shared_delegate.mkdir(parents=True, exist_ok=True)
        private = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        private.write_text(line)
        os.replace(private, path)
    except OSError as error:
        raise EngineError(f"the engine cannot write {path}: {error}") from None


def workflow_of_run(repo: Path, run_id: str, vcs: VersionControl, *, location: StateLocation | None = None) -> Path:
    """The path of the workflow file that the run `run_id` started from. Raise EngineError when the run is unknown.

    `location` is the location that the command looked up (see `locate_state`), or None to look it up here.
    """
    state_dir = (location or locate_state(repo, vcs)).state_directory(run_id)
    try:
        events = read_events(state_dir / "runs" / run_id / "journal.jsonl", torn_tail=True)
    except JournalError as error:
        raise EngineError(f"run {run_id!r} cannot be resumed: {error}") from None
    if not events or events[0]["event"] != "run-start":
        raise EngineError(f"run {run_id!r} cannot be resumed: its journal does not start with run-start")
    return Path(str(events[0]["workflow"]))
