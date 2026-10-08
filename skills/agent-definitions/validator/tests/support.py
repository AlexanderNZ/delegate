"""Helpers for the engine tests: a real git repository, a workflow file, and a scripted adapter.

The scripted adapter is the in-process seam of the engine. It follows the same
interface as a harness adapter, so a test runs `delegate run` end to end with
no model.
"""

from __future__ import annotations

import json
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


@dataclass
class ScriptedAdapter:
    """An adapter that does what a test tells it to, in the working directory it receives.

    On each call it writes `files` to the working directory, commits them, and
    writes the report (valid, or the `report` text you give, or none). `calls`
    holds every request it received.
    """

    tier_column: str = "claude-code"
    files: dict[str, str] = field(default_factory=lambda: {"feature.txt": "feature\n"})
    commit: bool = True
    write_report: bool = True
    report_text: str | Callable[[AdapterRequest, str], str] | None = None
    end_state: str = "finished"
    exit_status: int = 0
    session_id: str | None = "session-1"
    calls: list[AdapterRequest] = field(default_factory=list)

    def run(self, request: AdapterRequest) -> AdapterResult:
        self.calls.append(request)
        head = git(request.cwd, "rev-parse", "HEAD").strip()
        if self.files:
            for name, content in self.files.items():
                (request.cwd / name).write_text(content)
            git(request.cwd, "add", "-A")
        if self.commit and self.files:
            git(request.cwd, "commit", "-q", "-m", f"scripted work ({request.agent})")
            head = git(request.cwd, "rev-parse", "HEAD").strip()
        if self.write_report:
            branch = git(request.cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()
            if callable(self.report_text):
                text = self.report_text(request, head)
            elif self.report_text is not None:
                text = self.report_text
            else:
                text = json.dumps(valid_report("a", branch, head))
            request.report_path.parent.mkdir(parents=True, exist_ok=True)
            request.report_path.write_text(text)
        request.report_path.parent.mkdir(parents=True, exist_ok=True)
        stream = request.report_path.parent / f"{request.report_path.stem}.stream.jsonl"
        stream.write_text('{"type":"result"}\n')
        return AdapterResult(self.exit_status, self.end_state, self.session_id, stream)


def read_journal(stdout: str) -> list[dict[str, object]]:
    """The events of the journal whose path `delegate run` printed on the line `journal <path>`."""
    lines = [line for line in stdout.splitlines() if line.startswith("journal ")]
    assert len(lines) == 1, stdout
    path = Path(lines[0].removeprefix("journal "))
    return [json.loads(line) for line in path.read_text().splitlines()]


def event_names(events: list[dict[str, object]]) -> list[str]:
    return [str(e["event"]) for e in events]
