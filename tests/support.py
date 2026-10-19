"""Helpers for the engine tests: a real git repository, a workflow file, and a scripted adapter.

The scripted adapter is the in-process seam of the engine. It follows the same
interface as a harness adapter, so a test runs `delegate run` end to end with
no model.
"""

from __future__ import annotations

import atexit
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Iterator
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path

from delegate.ports.harness import Activity, AdapterError, AdapterRequest, AdapterResult

WORKFLOW = """\
base-branch = "main"
run-branch = "run/demo"
mode = "assure"
adapter = "scripted"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["test -f feature.txt"]
hotspots = ["LICENSE", ".github/workflows/*"]

[[tickets]]
id = "a"
text = "The export command writes a CSV file."
stack = "python"
blocked-by = []
"""


# The author and the committer of each commit that a test makes. They go to git in the environment: an argument
# `-c user.name=…` or a `git config` process does not hold them.
GIT_IDENTITY: dict[str, str] = {
    f"GIT_{role}_{key}": value
    for role in ("AUTHOR", "COMMITTER")
    for key, value in (("NAME", "Scratch"), ("EMAIL", "scratch@example.invalid"))
}


def git(repo: Path, *args: str) -> str:
    """Run one git process in `repo`, and return its stdout. An identity in the environment comes before `GIT_IDENTITY`."""
    r = subprocess.run(
        ["git", "-C", str(repo), "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
        env={**GIT_IDENTITY, **os.environ},
    )
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


# The summary line of `git commit`: `[<branch> <sha>] <subject>`, with `(root-commit)` before the sha of a first
# commit. With `core.abbrev=40` the sha is in full, so the scripted adapter needs no `rev-parse` after a commit.
COMMIT_SUMMARY = re.compile(r"^\[(?P<branch>\S+)(?: \(root-commit\))? (?P<head>[0-9a-f]{40})\]", re.MULTILINE)


def commit_head(repo: Path, *args: str) -> tuple[str, str]:
    """Commit in `repo` with the `git commit` arguments `args`, and return the new HEAD and the branch.

    The commit and its result are one git process. A summary line that the
    pattern does not match (a detached HEAD) costs one `rev-parse` more.
    """
    match = COMMIT_SUMMARY.search(git(repo, "-c", "core.abbrev=40", "commit", *args))
    if match is None:
        head, branch = git(repo, "rev-parse", "HEAD", "--abbrev-ref", "HEAD").split()
        return head, branch
    return match["head"], match["branch"]


# The repositories that `copy_of` made in this process, by key. Each process of the suite (each worker of
# `pytest -n`) has its own. The directory of the templates is in the temporary directory at import, because a
# test can point `tempfile` at a directory that it removes.
_templates: dict[tuple[object, ...], Path] = {}
_TEMPLATE_BASE: str = tempfile.gettempdir()

# The git options of a template: no fsmonitor daemon for a template, also in a test that turns the setting on.
TEMPLATE_GIT: tuple[str, ...] = ("-c", "core.fsmonitor=false")


def copy_of(dest: Path, key: tuple[object, ...], build: Callable[[Path], None]) -> Path:
    """Copy to `dest` the repository that `build` makes in an empty directory, and return `dest`.

    Each git command is one process start, and a process start is slow on a
    machine with endpoint security. Many tests need the same small repository,
    so the first call with a `key` builds it once in a temporary directory of
    the process, and each call copies that template. Two calls with the same
    key get the same files and the same commits. `dest` must not exist.
    """
    template = _templates.get(key)
    if template is None:
        root = Path(tempfile.mkdtemp(prefix="delegate-template-", dir=_TEMPLATE_BASE))
        atexit.register(shutil.rmtree, root, True)
        template = root / "repo"
        template.mkdir()
        build(template)
        _templates[key] = template
    # A lock file belongs to a git process that ran in the template, for example `git maintenance run --auto`, which
    # can remove it while the copy runs. The copy is a new repository with no process, so it takes no lock file.
    shutil.copytree(template, dest, symlinks=True, ignore=shutil.ignore_patterns("*.lock"))
    return dest


def _seeded(workflow: str) -> Callable[[Path], None]:
    def build(repo: Path) -> None:
        (repo / "seed.txt").write_text("seed\n")
        (repo / "workflow.toml").write_text(workflow)
        git(repo, *TEMPLATE_GIT, "init", "-q", "-b", "main")
        git(repo, *TEMPLATE_GIT, "add", "-A")
        git(repo, *TEMPLATE_GIT, "commit", "-q", "-m", "seed")

    return build


def make_repo(tmp_path: Path, workflow: str = WORKFLOW) -> Path:
    """A real git repository on `main` that holds `workflow.toml` and one seed file."""
    return copy_of(tmp_path / "repo", ("make_repo", workflow), _seeded(workflow))


def valid_report(ticket: str, branch: str, head_sha: str, **changes: object) -> dict[str, object]:
    """A specialist report that satisfies the schema. `changes` replaces fields."""
    report: dict[str, object] = {
        "ticket": ticket,
        "status": "committed",
        "branch": branch,
        "head_sha": head_sha,
        "commits": [head_sha],
        "gates_green": True,
        "summary": "Done.",
    }
    report.update(changes)
    return report


def valid_verdict(verdict: str = "ACCEPT", findings: list[str] | None = None, **changes: object) -> dict[str, object]:
    """A verifier report that satisfies the schema. `changes` replaces fields."""
    report: dict[str, object] = {
        "mode": "full",
        "verdict": verdict,
        "criteria": [{"criterion": "The export command writes a CSV file.", "evidence": "feature.txt holds the output."}],
        "gate_output": "test -f feature.txt: exit 0",
        "findings": findings if findings is not None else ([] if verdict == "ACCEPT" else ["feature.txt is not a CSV file."]),
        "unverified": [],
    }
    report.update(changes)
    return report


@dataclass
class ScriptedAdapter:
    """An adapter that does what a test tells it to, in the working directory it receives.

    A request for an agent whose name ends in `verifier` is a verifier run. All
    other requests are specialist runs.

    On a specialist call it writes `files` to the working directory, commits
    them, and writes the report (valid, or the `report_text` you give, or none).
    A `report_text` function of the request and the head that gives None
    writes no report for that call.
    `calls` holds every specialist request it received.

    On a verifier call it writes `verifier_files` to the working directory (a
    verifier that breaks its copy), and writes the verdict report: `verdict` with
    `verdict_findings`, or the `verifier_report_text` you give (a text, or a
    function of the request that gives the text or None for the default), or none.
    `during_verifier` runs while the verifier runs, so a test can change the
    real repository then. `verifier_calls` holds every verifier request. When
    `record_verifier_git` is set, `verifier_heads` and `verifier_remotes` hold
    the HEAD commit and the remotes of the working directory at the time of
    each call. They cost two git processes for each call, so they stay empty
    by default.

    `files_by_ticket` replaces `files` for a ticket whose id it holds, and a
    ticket id in `failing_tickets` ends `failed` with exit status 1, so a test
    scripts one ticket on its own.

    A specialist request whose prompt holds the section "## Findings to fix" is
    a fix-up. The adapter then commits `fixup_files` (by default one new file
    for each fix-up), or amends the last commit when `fixup_amend` is set, or
    commits nothing when `fixup_commit` is false. It ends with `fixup_end_state`
    and `fixup_exit_status`, and writes the report `fixup_report_text` (a text,
    or a function of the request and the head; by default a valid report) unless `fixup_write_report` is false. `fixup_calls`
    holds these requests. `verdict_sequence` gives the verdict of each verifier call in
    turn; the last verdict repeats. A verifier request whose prompt starts with
    "## Findings under verification" gets a report for the mode `fix-up`.

    A specialist request whose prompt holds the section "## Continuation" is a
    continuation. The adapter then commits `continuation_files[k]` for the k-th
    continuation (nothing when the list is shorter), and `continuation_calls`
    holds these requests. `outcomes` gives the (end state, exit status) of each
    specialist call that is not a fix-up, in call order, the first build
    included; the last pair repeats. `session_ids` gives the session id of each
    such call in the same way. `supports_resume` is the `supports_resume`
    attribute of the adapter. `refusals` gives, for a role (`specialist`,
    `fix-up` or `verifier`), the (tool, mode) pairs that a permission mode
    refused, in the `refused` field of its result. `verifier_final_text` (a
    text, or a function of the request) is the `final_text` of each verifier
    result.

    Each call writes the line `{"type":"result"}` to the event stream that the
    request names, and gives that path back in the result. `activity` reads
    such a stream as no tokens and no time, and raises for a missing stream.

    `on_specialist` runs at the start of every specialist request, before the
    adapter touches the working directory. A test uses it to raise an error, to
    break the repository, or to start another command while the run holds its lock.

    `report_checks` holds, for each request, the role (`specialist`, `fix-up`
    or `verifier`), the `idle_timeout` of the request, and the answer of its
    `report_valid` check before and after the adapter writes the report (`None`
    when the request has no check). A request of a role in `idle_roles` plays an
    idle session that the adapter ends at the idle timeout: when the check says
    that the report is valid, the result has `ended_idle` set, the exit status
    -15 (SIGTERM) and the end state `failed`, because the session wrote no
    `result` event.
    """

    tier_column: str = "claude-code"
    supports_resume: bool = False
    files: dict[str, str] = field(default_factory=lambda: {"feature.txt": "feature\n"})
    files_by_ticket: dict[str, dict[str, str]] = field(default_factory=dict)
    failing_tickets: set[str] = field(default_factory=set)
    commit: bool = True
    write_report: bool = True
    report_text: str | Callable[[AdapterRequest, str], str | None] | None = None
    end_state: str = "finished"
    exit_status: int = 0
    session_id: str | None = "session-1"
    calls: list[AdapterRequest] = field(default_factory=list)
    verdict: str = "ACCEPT"
    verdict_findings: list[str] | None = None
    verifier_report_text: str | Callable[[AdapterRequest], str] | None = None
    write_verifier_report: bool = True
    verifier_final_text: str | Callable[[AdapterRequest], str] | None = None
    verifier_files: dict[str, str] = field(default_factory=dict)
    verifier_end_state: str = "finished"
    verifier_exit_status: int = 0
    during_verifier: Callable[[AdapterRequest], None] | None = None
    verifier_calls: list[AdapterRequest] = field(default_factory=list)
    record_verifier_git: bool = False
    verifier_heads: list[str] = field(default_factory=list)
    verifier_remotes: list[list[str]] = field(default_factory=list)
    verdict_sequence: list[str] = field(default_factory=list)
    fixup_files: dict[str, str] | None = None
    fixup_commit: bool = True
    fixup_amend: bool = False
    fixup_calls: list[AdapterRequest] = field(default_factory=list)
    fixup_end_state: str = "finished"
    fixup_exit_status: int = 0
    fixup_report_text: str | Callable[[AdapterRequest, str], str] | None = None
    fixup_write_report: bool = True
    continuation_files: list[dict[str, str]] = field(default_factory=list)
    continuation_calls: list[AdapterRequest] = field(default_factory=list)
    outcomes: list[tuple[str, int]] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)
    on_specialist: Callable[[AdapterRequest], None] | None = None
    idle_roles: set[str] = field(default_factory=set)
    report_checks: list[tuple[str, float | None, bool | None, bool | None]] = field(default_factory=list)
    refusals: dict[str, tuple[tuple[str, str], ...]] = field(default_factory=dict)

    def _report_check(self, request: AdapterRequest) -> bool | None:
        return None if request.report_valid is None else request.report_valid()

    def _ended(self, role: str, request: AdapterRequest, before: bool | None, result: AdapterResult) -> AdapterResult:
        """Record the checks of the request, and end an idle session of a role in `idle_roles` after a valid report."""
        after = self._report_check(request)
        self.report_checks.append((role, request.idle_timeout, before, after))
        if role in self.idle_roles and after:
            return AdapterResult(-15, "failed", result.session_id, result.event_stream, ended_idle=True)
        if role in self.refusals:
            return dataclasses.replace(result, refused=self.refusals[role])
        return result

    def run(self, request: AdapterRequest) -> AdapterResult:
        if request.agent.endswith("verifier"):
            return self._run_verifier(request)
        if self.on_specialist is not None:
            self.on_specialist(request)
        is_fixup = "## Findings to fix" in request.prompt
        is_continuation = not is_fixup and "## Continuation" in request.prompt
        index = len(self.calls) + len(self.continuation_calls)
        continuation = len(self.continuation_calls)
        (self.fixup_calls if is_fixup else self.continuation_calls if is_continuation else self.calls).append(request)
        ticket = re.search(r"^Ticket (\S+)", request.prompt, re.MULTILINE).group(1)
        files = self.files_by_ticket.get(ticket, self.files)
        if is_fixup:
            files = self.fixup_files if self.fixup_files is not None else {f"fixup-{len(self.fixup_calls)}.txt": "fix\n"}
        elif is_continuation:
            files = self.continuation_files[continuation] if continuation < len(self.continuation_files) else {}
        if files:
            for name, content in files.items():
                (request.cwd / name).write_text(content)
            git(request.cwd, "add", "-A")
        if is_fixup and self.fixup_amend:
            head, branch = commit_head(request.cwd, "--amend", "-m", "amended work")
        elif (self.fixup_commit if is_fixup else self.commit) and files:
            head, branch = commit_head(request.cwd, "-m", f"scripted work ({request.agent})")
        else:
            head, branch = git(request.cwd, "rev-parse", "HEAD", "--abbrev-ref", "HEAD").split()  # one process for both
        before = self._report_check(request)
        role = "fix-up" if is_fixup else "specialist"
        if (self.fixup_write_report if is_fixup else self.write_report):
            if is_fixup and callable(self.fixup_report_text):
                text = self.fixup_report_text(request, head)
            elif is_fixup and self.fixup_report_text is not None:
                text = self.fixup_report_text
            elif callable(self.report_text):
                text = self.report_text(request, head)
            elif not is_fixup and self.report_text is not None:
                text = self.report_text
            else:
                text = json.dumps(valid_report(ticket, branch, head))
            if text is not None:  # a `report_text` function that gives None writes no report
                request.report_path.parent.mkdir(parents=True, exist_ok=True)
                request.report_path.write_text(text)
        request.report_path.parent.mkdir(parents=True, exist_ok=True)
        stream = request.event_stream
        stream.write_text('{"type":"result"}\n')
        if is_fixup:
            return self._ended(role, request, before, AdapterResult(self.fixup_exit_status, self.fixup_end_state, self.session_id, stream))
        if ticket in self.failing_tickets:
            return self._ended(role, request, before, AdapterResult(1, "failed", self.session_id, stream))
        end_state, exit_status = self.outcomes[min(index, len(self.outcomes) - 1)] if self.outcomes else (self.end_state, self.exit_status)
        session = self.session_ids[min(index, len(self.session_ids) - 1)] if self.session_ids else self.session_id
        return self._ended(role, request, before, AdapterResult(exit_status, end_state, session, stream))

    def activity(self, stream: Path) -> Activity:
        if not stream.is_file():
            raise AdapterError(f"event stream {stream} not found")
        return Activity(0, 0, None)

    def _run_verifier(self, request: AdapterRequest) -> AdapterResult:
        turn = len(self.verifier_calls)
        self.verifier_calls.append(request)
        if self.record_verifier_git:
            self.verifier_heads.append(git(request.cwd, "rev-parse", "HEAD").strip())
            self.verifier_remotes.append(git(request.cwd, "remote").split())
        for name, content in self.verifier_files.items():
            (request.cwd / name).write_text(content)
        if self.during_verifier is not None:
            self.during_verifier(request)
        request.report_path.parent.mkdir(parents=True, exist_ok=True)
        before = self._report_check(request)
        if self.write_verifier_report:
            text = self.verifier_report_text
            if callable(text):
                text = text(request)
            if text is None:
                verdict = self.verdict_sequence[min(turn, len(self.verdict_sequence) - 1)] if self.verdict_sequence else self.verdict
                mode = "fix-up" if request.prompt.startswith("## Findings under verification") else "full"
                text = json.dumps(valid_verdict(verdict, self.verdict_findings, mode=mode))
            request.report_path.write_text(text)
        stream = request.event_stream
        stream.write_text('{"type":"result"}\n')
        final = self.verifier_final_text(request) if callable(self.verifier_final_text) else self.verifier_final_text
        result = AdapterResult(
            self.verifier_exit_status, self.verifier_end_state, f"verifier-session-{turn + 1}", stream, final_text=final
        )
        return self._ended("verifier", request, before, result)


def read_journal(stdout: str) -> list[dict[str, object]]:
    """The events of the journal whose path `delegate run` printed on the line `journal <path>`."""
    lines = [line for line in stdout.splitlines() if line.startswith("journal ")]
    assert len(lines) == 1, stdout
    path = Path(lines[0].removeprefix("journal "))
    return [json.loads(line) for line in path.read_text().splitlines()]


def event_names(events: list[dict[str, object]]) -> list[str]:
    return [str(e["event"]) for e in events]


# A script that runs `delegate run` in a child process, and kills the child with SIGKILL
# at the start of the adapter call whose number is argv[3]. A killed process cannot clean up,
# so it leaves what a real kill leaves: an open journal, a lock, and a worktree.
KILL_SCRIPT = """\
import os, signal, sys
from delegate import adapters
from delegate.cli import delegate
from tests.support import ScriptedAdapter

class Killer(ScriptedAdapter):
    seen = 0

    def run(self, request):
        type(self).seen += 1
        if type(self).seen == int(sys.argv[3]):
            os.kill(os.getpid(), signal.SIGKILL)
        return super().run(request)

adapters.register("scripted", Killer(files_by_ticket={t: {t + ".txt": t + "\\n"} for t in "abcd"}))
sys.exit(delegate.main(["run", sys.argv[1], "--repo", sys.argv[2]]))
"""


def kill_a_run(repo: Path, kill_before_call: int) -> tuple[str, int]:
    """Run `workflow.toml` of `repo` in a child process that SIGKILLs itself before its Nth adapter call.

    A specialist or a verifier call counts as one call each. Return the run id
    and the process id of the child.
    """
    import signal
    import subprocess as sp
    import sys

    root = Path(__file__).resolve().parents[1]
    # The child must import the package under test (src/) and these helpers (tests/) first, and still
    # find the dependencies (pyyaml) that the parent finds through its own PYTHONPATH. A Nix build gives them in no other way.
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(root / "src"), str(root), os.environ.get("PYTHONPATH")]))}
    env.update(GIT_IDENTITY)
    child = sp.Popen(
        [sys.executable, "-c", KILL_SCRIPT, str(repo / "workflow.toml"), str(repo), str(kill_before_call)],
        cwd=root, env=env, stdout=sp.PIPE, stderr=sp.PIPE, text=True,
    )
    _, err = child.communicate(timeout=300)
    assert child.returncode == -signal.SIGKILL, (child.returncode, err)
    (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
    return run_dir.name, child.pid


def journal_path(repo: Path, run_id: str) -> Path:
    return repo / ".git" / "delegate" / "runs" / run_id / "journal.jsonl"


def read_journal_file(repo: Path, run_id: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in journal_path(repo, run_id).read_text().splitlines()]


def run_main(capsys, *argv: str) -> tuple[int, str, str]:
    """Call `delegate run <argv>` and return the exit code, stdout and stderr."""
    from delegate.cli import delegate

    code = delegate.main(["run", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def resume_main(repo: Path, capsys, run_id: str, *extra: str) -> tuple[int, str, str]:
    return run_main(capsys, "--resume", run_id, "--repo", str(repo), *extra)


def ticket_of(request: AdapterRequest) -> str:
    """The ticket id that a specialist or verifier prompt names first."""
    match = re.search(r"Ticket (\w+)", request.prompt)
    assert match is not None, request.prompt
    return match.group(1)


def tree(repo: Path, branch: str) -> list[str]:
    return git(repo, "ls-tree", "-r", "--name-only", branch).split()


def finished_run(repo: Path, capsys) -> str:
    """Run `workflow.toml` of `repo` to its end and return the run id."""
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert (code, err) == (0, "")
    return next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]


def write_journal(repo: Path, run_id: str, *events: tuple[str, dict[str, object]]) -> Path:
    """Write a journal of `(event name, fields)` pairs for the run `run_id` in the state directory of `repo`.

    It uses the journal class of the engine, so each line has `seq` and `time`
    as the engine writes them. Return the path of the journal file.
    """
    from delegate.run.journal import Journal

    path = journal_path(repo, run_id)
    journal = Journal(path)
    for name, fields in events:
        journal.append(name, **fields)
    return path


def append_events(path: Path, *events: tuple[str, dict[str, object]]) -> None:
    """Append `(event name, fields)` pairs to an existing journal file, as a run in progress does."""
    from delegate.run.journal import Journal

    journal, _ = Journal.reopen(path)
    for name, fields in events:
        journal.append(name, **fields)


def _empty(repo: Path) -> None:
    git(repo, *TEMPLATE_GIT, "init", "-q", "-b", "main")


def bare_repo(tmp_path: Path) -> Path:
    """A git repository with no commit and no run. `delegate status` and `delegate watch` need only the state directory."""
    return copy_of(tmp_path / "repo", ("bare_repo",), _empty)


def run_start(run_id: str, *tickets: str, mode: str = "assure", run_branch: str = "run/demo") -> tuple[str, dict[str, object]]:
    return "run-start", {
        "run_id": run_id, "workflow": "/w/workflow.toml", "mode": mode, "adapter": "scripted",
        "base_branch": "main", "run_branch": run_branch, "tickets": list(tickets),
    }


@contextlib.contextmanager
def live_lock(journal: Path, run_branch: str = "run/demo") -> Iterator[None]:
    """Hold the lock of `run_branch` for the run of `journal` while the block runs, as the process of a run that works does.

    It uses the lock class of the engine. The holder is this process, so the lock names a process that exists.
    """
    from delegate.run.lock import run_lock

    with run_lock(journal.parents[2], run_branch, journal.parent.name):
        yield


# A child process that takes the lock of a run branch with the lock class of the engine, and ends with no release.
STALE_LOCK_SCRIPT = """\
import os, sys
from pathlib import Path
from delegate.run.lock import run_lock

with run_lock(Path(sys.argv[1]), sys.argv[2], sys.argv[3]):
    os._exit(0)
"""


def stale_lock(journal: Path, run_branch: str = "run/demo") -> None:
    """Leave the lock of `run_branch` for the run of `journal`, held by a process that no longer exists, as a killed run leaves it."""
    import sys

    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [str(root / "src"), os.environ.get("PYTHONPATH")]))}
    subprocess.run(
        [sys.executable, "-c", STALE_LOCK_SCRIPT, str(journal.parents[2]), run_branch, journal.parent.name],
        env=env, check=True, timeout=60,
    )


def step_start(ticket: str) -> tuple[str, dict[str, object]]:
    return "step-start", {
        "ticket": ticket, "stack": "python", "branch": f"run/demo-{ticket}", "worktree": f"/w/{ticket}",
        "base_commit": "0" * 40, "agent": "python-specialist", "tier": "strong", "model": "m",
    }


def step_end(ticket: str, state: str = "built", reason: str | None = None) -> tuple[str, dict[str, object]]:
    return "step-end", {"ticket": ticket, "state": state, "reason": reason}


def run_end(result: str = "built", built: list[str] | None = None, failed: list[str] | None = None) -> tuple[str, dict[str, object]]:
    return "run-end", {"result": result, "built": built or [], "failed": failed or [], "skipped": []}


class Terminal(io.StringIO):
    """The terminal of a person: a standard output whose `isatty` is true. It is the system boundary of the live view of `watch`."""

    def isatty(self) -> bool:
        return True


# A control sequence of the terminal: the live view moves the cursor and clears lines with them.
CONTROL = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# The sequences that start and end one frame of the live view: the cursor goes home, and the screen is cleared below the frame.
FRAME_START = "\x1b[H"
FRAME_END = "\x1b[J"


# The OSC 2 sequence that sets the title of the terminal (ESC ] 2 ; <title> BEL). `delegate` writes it on a terminal.
TITLE = re.compile("\x1b\\]2;([^\x07]*)\x07")


def status_row(out: str, ticket: str) -> list[str]:
    """The cells of the row of a ticket in the ticket table of `delegate status` (#117), with no empty cell.

    Two columns are 2 spaces apart or more, so the cells split at each run of 2 spaces or more. The rail (`│`, or `|`
    in the ASCII set) is not a cell (#143). The first cell is the position in the plan, and the second cell ends with
    the ticket id. The phases after the first phase of a running ticket are on sub-rows, which are not in the cells.
    """
    for line in out.splitlines():
        cells = [cell for cell in re.split(r" {2,}", line.strip()) if cell not in ("│", "|")]
        if len(cells) > 1 and cells[0].isdigit() and cells[1].split()[-1] == ticket:
            return cells
    raise AssertionError(f"the table of status has no row of ticket {ticket}:\n{out}")


def state_and_verdict(out: str, ticket: str) -> tuple[str, str]:
    """The state and the verdict of a ticket in the ticket table of `delegate status`, with no glyph.

    The state cell has the glyph of the locale, as `✓ built` or `+ built`, so a case that reads only the state word
    needs no locale. The verdict is `ACCEPT`, `REJECT`, or empty when the ticket has none.
    """
    cells = status_row(out, ticket)
    return cells[2].split()[-1], next((cell for cell in cells[3:] if cell in ("ACCEPT", "REJECT")), "")


def without_titles(out: str) -> str:
    """The output with no title sequence: the text that the terminal shows."""
    return TITLE.sub("", out)


def frames(out: str) -> list[list[str]]:
    """The frames that the live view of `watch` drew, in order, each as its lines with no control sequence."""
    return [CONTROL.sub("", chunk.split(FRAME_END)[0]).splitlines() for chunk in out.split(FRAME_START)[1:]]
