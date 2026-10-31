"""The agent check of a run: the agents of a run are the agents that it started with, or the journal records the change (#205).

An agent file that git ignores is in no diff, so no hotspot sees a change of it. The engine therefore records the SHA-256
of each agent file that a role of the run names (the specialist and the verifier of each stack, as the adapter finds them
for the main checkout) in `run-start`. Before each agent session, after `prepare`, it hashes the same agent in the tree
where the agent runs, and it fails the step when the hash differs from the record, or the file is missing.

`delegate run --resume <run id> --agents-changed` records the hashes of the main checkout again, in the `resume` event.
The record of a run is the `agents` field of its newest `run-start` or `resume` event. A journal of an earlier version has
none: a resume then records the hashes at the resume.

The module reads files and asks the adapter where the agent files are. It writes no journal and starts no process.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..ports.harness import Adapter
from .workflow import Workflow


@dataclass(frozen=True)
class AgentChange:
    """An agent file that differs from its record: the recorded path, the recorded hash, and the new hash (`None` when the file is missing)."""

    agent: str
    path: Path
    recorded: str
    found: str | None

    def message(self, ticket: str) -> str:
        """The reason of the step: the file, both hashes, and the command that goes on, which names the ticket (#205).

        A step that this check stops fails, and the run ends `failed`, so only `--retry <ticket>` can go on with it.
        """
        now = self.found if self.found is not None else "missing"
        return (
            f"agent file {self.path} is not the file that the run started with: recorded sha256 {self.recorded}, now {now}; "
            f"if the change is meant, go on with delegate run --resume RUN_ID --retry {ticket} --agents-changed"
        )


def file_hash(path: Path) -> str | None:
    """The SHA-256 of the file as a hexadecimal text, or None when the file is missing or cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def agent_names(workflow: Workflow) -> list[str]:
    """The agents that a role of the run names: the specialist and the verifier of each stack, each name once, in stack order."""
    names: list[str] = []
    for stack in workflow.stacks.values():
        for name in (stack.specialist, stack.verifier):
            if name not in names:
                names.append(name)
    return names


def record_agents(adapter: Adapter, directory: Path, names: list[str]) -> list[dict[str, str]]:
    """The record of the agent files that the adapter finds for `directory`: `agent`, `path` and `sha256` of each file.

    An agent that the adapter does not find, or whose file cannot be read, has no entry.
    """
    record: list[dict[str, str]] = []
    for name in names:
        path = adapter.agent_file(name, directory)
        digest = None if path is None else file_hash(path)
        if path is not None and digest is not None:
            record.append({"agent": name, "path": str(path), "sha256": digest})
    return record


def recorded_agents(events: list[dict[str, object]]) -> list[dict[str, str]] | None:
    """The newest record of the agent files in the journal, or None when no `run-start` or `resume` event holds one."""
    found: list[dict[str, str]] | None = None
    for event in events:
        if event["event"] in ("run-start", "resume") and isinstance(event.get("agents"), list):
            found = event["agents"]  # type: ignore[assignment]
    return found


def check_agent(adapter: Adapter, recorded: list[dict[str, str]], agent: str, directory: Path) -> AgentChange | None:
    """Compare the agent file in `directory` with its record. Return None when it is the same, or when the run recorded none.

    `directory` is the tree where the agent runs. The file is the one that the adapter finds for it. When the adapter
    finds none, or the file cannot be read, the file is missing. The path of the change is the recorded path, the file of
    the main checkout, because a tree of a verifier goes away after its session.
    """
    entry = next((e for e in recorded if e["agent"] == agent), None)
    if entry is None:
        return None
    path = adapter.agent_file(agent, directory)
    found = None if path is None else file_hash(path)
    if found == entry["sha256"]:
        return None
    return AgentChange(agent, Path(entry["path"]), entry["sha256"], found)
