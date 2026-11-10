"""The agent check of a run: each agent session runs from the sealed agent of the run, and only when the seal is as recorded (#217).

An agent file that git ignores is in no diff, and a tree that a specialist wrote can hold an agent file that replaces the
agent of the harness. So the engine seals each agent that a role of the run names (the specialist and the verifier of each
stack) at the start of the run, from the main checkout, into `<run directory>/agents/<agent>/`, through the port operation
`seal`. `run-start` records each seal in `agents`: the agent, the source path, the seal directory, the handle, and the path
and SHA-256 of each file of the seal. Before each agent session, the engine hashes each file of the seal and fails the step
when one differs from the record, or is missing. It reads no agent file in the tree where the agent runs.

`delegate run --resume <run id> --agents-changed` seals each agent again from the main checkout, and records the new seal in
the `resume` event. The record of a run is the `agents` field of its newest `run-start` or `resume`
event that holds the record of this form. A journal of an earlier version has the record of #205 (the path and the hash of
an agent file) or none: a resume then seals at the resume and records the seal.

The module reads files and asks the adapter to seal. It writes no journal and starts no process.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from ..ports.harness import Adapter, McpTool, SealedAgent, SealedFile
from .workflow import Stack, Workflow

# The directory of the seals under the run directory.
SEAL_DIRECTORY = "agents"


@dataclass(frozen=True)
class AgentChange:
    """A file of a seal that differs from its record: the agent, the path, the recorded hash, and the new hash (`None` when the file is missing)."""

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
            f"sealed file {self.path} of agent {self.agent} is not the file that the run sealed: recorded sha256 {self.recorded}, now {now}; "
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


def verifier_mcp_tools(workflow: Workflow) -> dict[str, tuple[McpTool, ...]]:
    """The MCP tools that the seal of each verifier adds (#257): the `verifier_tools` of each server of each stack of the verifier.

    An agent that is the verifier of no stack has no entry, and neither has a verifier whose stacks name no `verifier-tools`.
    A tool is in the tuple once, in the order of the stacks, of the servers and of the names.
    """
    tools: dict[str, dict[McpTool, None]] = {}
    for stack in workflow.stacks.values():
        for server in stack.mcp_servers:
            for name in server.verifier_tools:
                tools.setdefault(stack.verifier, {})[McpTool(server.name, name)] = None
    return {agent: tuple(found) for agent, found in tools.items()}


def record_of(sealed: SealedAgent) -> dict[str, object]:
    """The journal record of one seal: the agent, the source path, the seal directory, the handle, and the files with their hashes."""
    return {
        "agent": sealed.agent,
        "source": None if sealed.source is None else str(sealed.source),
        "seal": str(sealed.directory),
        "handle": sealed.handle,
        "files": [{"path": str(file.path), "sha256": file.sha256} for file in sealed.files],
    }


def seal_agents(
    adapter: Adapter, repo: Path, run_dir: Path, names: list[str], mcp_tools: dict[str, tuple[McpTool, ...]] | None = None,
) -> list[dict[str, object]]:
    """Seal each agent of `names` from `repo` into `<run_dir>/agents/<agent>/` and give the record of the seals.

    A seal that is already there goes first, so a seal again leaves no file of the old one. An agent that the adapter does not
    find has no entry. An adapter that refuses to seal raises AdapterError, and the caller reports it. The seal of an agent
    with an entry in `mcp_tools` gets those tools (#257). The adapter gets the argument only when the entry is not empty,
    so an adapter whose `seal` takes three arguments seals each other agent.
    """
    record: list[dict[str, object]] = []
    for name in names:
        directory = run_dir / SEAL_DIRECTORY / name
        shutil.rmtree(directory, ignore_errors=True)
        tools = (mcp_tools or {}).get(name, ())
        sealed = adapter.seal(name, repo, directory, mcp_tools=tools) if tools else adapter.seal(name, repo, directory)
        if sealed is not None:
            record.append(record_of(sealed))
    return record


def _is_record(entries: object) -> bool:
    """True when `entries` is a record of seals: a list whose entries each hold `files`. The record of #205 has none."""
    return isinstance(entries, list) and all(isinstance(e, dict) and isinstance(e.get("files"), list) for e in entries)


def recorded_agents(events: list[dict[str, object]]) -> list[dict[str, object]] | None:
    """The newest record of seals in the journal, or None when no event holds one.

    The record of #205 (schema 1, an agent file with its hash) is not a record of seals, so it counts as none: an event
    holds a record of seals only when each of its entries has `files`. An empty record counts, because no agent was found.
    """
    found: list[dict[str, object]] | None = None
    for event in events:
        entries = event.get("agents")
        if event["event"] in ("run-start", "resume") and _is_record(entries):
            found = entries  # type: ignore[assignment]
    return found


def denied_agents(events: list[dict[str, object]]) -> set[str]:
    """The agents whose guard probe showed a denial after the newest record of seals in the journal (#219).

    A resume that seals again writes a newer record, so the probes before it do not count and each verifier is probed
    again. A resume that seals nothing leaves the record and its probes as they were.
    """
    since = -1
    for index, event in enumerate(events):
        if event["event"] in ("run-start", "resume") and _is_record(event.get("agents")):
            since = index
    return {str(e["agent"]) for e in events[since + 1 :] if e["event"] == "guard-probe" and e.get("result") == "denied"}


def unpermitted_gates(adapter: Adapter, stack: Stack, verifier: SealedAgent) -> list[str]:
    """One problem text for each gate of `stack` that the guard of the sealed `verifier` does not permit (#296).

    The dry run (`cli/setup_check.py`) and the start of a run (`engine.py`) both use it, so they give the same text.
    """
    problems: list[str] = []
    for gate in stack.gates:
        reason = adapter.permits(verifier, gate)
        if reason is not None:
            problems.append(f"stack {stack.name}: the verifier {stack.verifier!r} ({verifier.source}) does not permit the gate {gate!r}: {reason}")
    return problems


def sealed_agent(entry: dict[str, object]) -> SealedAgent:
    """The sealed agent that a record entry describes, as the adapter gets it in a request."""
    source = entry.get("source")
    return SealedAgent(
        str(entry["agent"]), str(entry["handle"]), Path(str(entry["seal"])),
        tuple(SealedFile(Path(str(f["path"])), str(f["sha256"])) for f in entry["files"]),  # type: ignore[attr-defined]
        None if source is None else Path(str(source)),
    )


def check_agent(recorded: list[dict[str, object]], agent: str) -> tuple[SealedAgent | None, AgentChange | None]:
    """Compare each file of the seal of `agent` with its record: the sealed agent, and the first change, or None.

    Return (None, None) when the run recorded no seal for the agent. The files are those of the record, so no agent file
    in a tree is read. A file that is missing or cannot be read is a change with the new hash None.
    """
    entry = next((e for e in recorded if e["agent"] == agent), None)
    if entry is None:
        return None, None
    sealed = sealed_agent(entry)
    for file in sealed.files:
        found = file_hash(file.path)
        if found != file.sha256:
            return sealed, AgentChange(agent, file.path, file.sha256, found)
    return sealed, None

