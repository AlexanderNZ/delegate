"""Helpers for the engine tests: a real git repository, a workflow file, and a scripted adapter.

The scripted adapter is the in-process seam of the engine. It follows the same
interface as a harness adapter, so a test runs `delegate run` end to end with
no model.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from agent_definitions.adapters import AdapterRequest, AdapterResult

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


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Scratch", "-c", "user.email=scratch@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def make_repo(tmp_path: Path, workflow: str = WORKFLOW) -> Path:
    """A real git repository on `main` that holds `workflow.toml` and one seed file."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "seed.txt").write_text("seed\n")
    (repo / "workflow.toml").write_text(workflow)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


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
    them, and writes the report (valid, or the `report` text you give, or none).
    `calls` holds every specialist request it received.

    On a verifier call it writes `verifier_files` to the working directory (a
    verifier that breaks its copy), and writes the verdict report: `verdict` with
    `verdict_findings`, or the `verifier_report_text` you give, or none.
    `during_verifier` runs while the verifier runs, so a test can change the
    real repository then. `verifier_calls` holds every verifier request. `verifier_heads` and
    `verifier_remotes` hold the HEAD commit and the remotes of the working
    directory at the time of each call.

    `files_by_ticket` replaces `files` for a ticket whose id it holds, and a
    ticket id in `failing_tickets` ends `failed` with exit status 1, so a test
    scripts one ticket on its own.

    A specialist request whose prompt holds the section "## Findings to fix" is
    a fix-up. The adapter then commits `fixup_files` (by default one new file
    for each fix-up), or amends the last commit when `fixup_amend` is set, or
    commits nothing when `fixup_commit` is false. It ends with `fixup_end_state`
    and `fixup_exit_status`, and writes the report `fixup_report_text` (by
    default a valid report) unless `fixup_write_report` is false. `fixup_calls`
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
    attribute of the adapter.
    """

    tier_column: str = "claude-code"
    supports_resume: bool = False
    files: dict[str, str] = field(default_factory=lambda: {"feature.txt": "feature\n"})
    files_by_ticket: dict[str, dict[str, str]] = field(default_factory=dict)
    failing_tickets: set[str] = field(default_factory=set)
    commit: bool = True
    write_report: bool = True
    report_text: str | Callable[[AdapterRequest, str], str] | None = None
    end_state: str = "finished"
    exit_status: int = 0
    session_id: str | None = "session-1"
    calls: list[AdapterRequest] = field(default_factory=list)
    verdict: str = "ACCEPT"
    verdict_findings: list[str] | None = None
    verifier_report_text: str | None = None
    write_verifier_report: bool = True
    verifier_files: dict[str, str] = field(default_factory=dict)
    verifier_end_state: str = "finished"
    verifier_exit_status: int = 0
    during_verifier: Callable[[AdapterRequest], None] | None = None
    verifier_calls: list[AdapterRequest] = field(default_factory=list)
    verifier_heads: list[str] = field(default_factory=list)
    verifier_remotes: list[list[str]] = field(default_factory=list)
    verdict_sequence: list[str] = field(default_factory=list)
    fixup_files: dict[str, str] | None = None
    fixup_commit: bool = True
    fixup_amend: bool = False
    fixup_calls: list[AdapterRequest] = field(default_factory=list)
    fixup_end_state: str = "finished"
    fixup_exit_status: int = 0
    fixup_report_text: str | None = None
    fixup_write_report: bool = True
    continuation_files: list[dict[str, str]] = field(default_factory=list)
    continuation_calls: list[AdapterRequest] = field(default_factory=list)
    outcomes: list[tuple[str, int]] = field(default_factory=list)
    session_ids: list[str] = field(default_factory=list)

    def run(self, request: AdapterRequest) -> AdapterResult:
        if request.agent.endswith("verifier"):
            return self._run_verifier(request)
        is_fixup = "## Findings to fix" in request.prompt
        is_continuation = not is_fixup and "## Continuation" in request.prompt
        index = len(self.calls) + len(self.continuation_calls)
        continuation = len(self.continuation_calls)
        (self.fixup_calls if is_fixup else self.continuation_calls if is_continuation else self.calls).append(request)
        head = git(request.cwd, "rev-parse", "HEAD").strip()
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
            git(request.cwd, "commit", "-q", "--amend", "-m", "amended work")
            head = git(request.cwd, "rev-parse", "HEAD").strip()
        elif (self.fixup_commit if is_fixup else self.commit) and files:
            git(request.cwd, "commit", "-q", "-m", f"scripted work ({request.agent})")
            head = git(request.cwd, "rev-parse", "HEAD").strip()
        if (self.fixup_write_report if is_fixup else self.write_report):
            branch = git(request.cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()
            if is_fixup and self.fixup_report_text is not None:
                text = self.fixup_report_text
            elif callable(self.report_text):
                text = self.report_text(request, head)
            elif not is_fixup and self.report_text is not None:
                text = self.report_text
            else:
                text = json.dumps(valid_report(ticket, branch, head))
            request.report_path.parent.mkdir(parents=True, exist_ok=True)
            request.report_path.write_text(text)
        request.report_path.parent.mkdir(parents=True, exist_ok=True)
        stream = request.report_path.parent / f"{request.report_path.stem}.stream.jsonl"
        stream.write_text('{"type":"result"}\n')
        if is_fixup:
            return AdapterResult(self.fixup_exit_status, self.fixup_end_state, self.session_id, stream)
        if ticket in self.failing_tickets:
            return AdapterResult(1, "failed", self.session_id, stream)
        end_state, exit_status = self.outcomes[min(index, len(self.outcomes) - 1)] if self.outcomes else (self.end_state, self.exit_status)
        session = self.session_ids[min(index, len(self.session_ids) - 1)] if self.session_ids else self.session_id
        return AdapterResult(exit_status, end_state, session, stream)

    def _run_verifier(self, request: AdapterRequest) -> AdapterResult:
        turn = len(self.verifier_calls)
        self.verifier_calls.append(request)
        self.verifier_heads.append(git(request.cwd, "rev-parse", "HEAD").strip())
        self.verifier_remotes.append(git(request.cwd, "remote").split())
        for name, content in self.verifier_files.items():
            (request.cwd / name).write_text(content)
        if self.during_verifier is not None:
            self.during_verifier(request)
        request.report_path.parent.mkdir(parents=True, exist_ok=True)
        if self.write_verifier_report:
            text = self.verifier_report_text
            if text is None:
                verdict = self.verdict_sequence[min(turn, len(self.verdict_sequence) - 1)] if self.verdict_sequence else self.verdict
                mode = "fix-up" if request.prompt.startswith("## Findings under verification") else "full"
                text = json.dumps(valid_verdict(verdict, self.verdict_findings, mode=mode))
            request.report_path.write_text(text)
        stream = request.report_path.parent / f"{request.report_path.stem}.stream.jsonl"
        stream.write_text('{"type":"result"}\n')
        return AdapterResult(self.verifier_exit_status, self.verifier_end_state, f"verifier-session-{turn + 1}", stream)


def read_journal(stdout: str) -> list[dict[str, object]]:
    """The events of the journal whose path `delegate run` printed on the line `journal <path>`."""
    lines = [line for line in stdout.splitlines() if line.startswith("journal ")]
    assert len(lines) == 1, stdout
    path = Path(lines[0].removeprefix("journal "))
    return [json.loads(line) for line in path.read_text().splitlines()]


def event_names(events: list[dict[str, object]]) -> list[str]:
    return [str(e["event"]) for e in events]
