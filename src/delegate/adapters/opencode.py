"""The OpenCode adapter: drive `opencode run` in JSON mode.

The command is `opencode run --format json --model <model> --agent <agent>
--dir <working directory>`. The harness takes its project from the PWD variable
that a caller inherits, not from the working directory of the process, so the
command names the directory.
The MCP servers of the stack go to the harness in the variable
`OPENCODE_CONFIG_CONTENT`, an inline configuration that the harness merges
with its other configuration. The harness has no option that removes the MCP
servers of the user or of the project, so the agent also gets those.

A session runs from the sealed agent of its run (#214). `seal` writes the agent file as an OpenCode configuration: a
JSON file `<agent>.json` in the seal directory, with the agent as an entry of `agent`, with `mode: all`, and with the
body of the file as its `prompt`. The adapter gives that configuration to the session in `OPENCODE_CONFIG_CONTENT`,
merged with the MCP servers. Measured on OpenCode 1.15.10 (#213): the prompt of an agent in that variable replaces the
prompt of a project agent of the same name, and its permission rules come after the rules of the tree. The enforcement
of the permission map in a live session was not measured (no model answered), so this adapter names it as a limit.

`mode: all` is needed because `opencode run --agent` refuses an agent with `mode: subagent`: the harness writes a
warning on standard error, runs its default agent, and exits 0. The adapter reads that warning and ends the session as
failed, with a reason that names the agent.

The adapter does not give the `added_directories` of the request to the
session: OpenCode 1.15.10 has no option that adds a directory to a session, and
the `bash` map of a rendered verifier has no `cd` rule (#153).

The prompt goes on standard input, never in argv, so a long brief meets no
argument limit and shows in no process list. The harness writes one JSON event
on each line of standard output. The adapter copies that stream to the file
that the request names, and reads the end state and the session id from it.
The exit status does not change the end state (see `AdapterResult`). After a
valid report, the adapter ends a session that writes no event for the idle
timeout of the request (see `process.py`). It also ends a session that runs past the agent timeout of the request, and
sets `ended_timeout`.

The activity of a stream comes from the `step_finish` events and the
`timestamp` field (epoch milliseconds). Each `step_finish` event carries
`part.tokens`, the tokens of that step, so the adapter adds them in two counts.
The cache reads (`cache.read`) are the cache tokens. The input, the output, the
reasoning, and the cache writes (`cache.write`) are the new tokens. A step with
no `cache` field has no cache reads and no cache writes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..definitions.validate import opencode_bash_action, split_frontmatter
from ..ports.harness import (
    CAPPED, FAILED, FINISHED, Activity, AdapterError, AdapterRequest, AdapterResult, McpServer, SealedAgent, SealedFile,
)
from .environment import process_environment
from .process import run_harness
from .streams import read_events

# The command that starts the harness.
COMMAND: str = "opencode"

# The column of the tier table that holds the OpenCode models.
TIER_COLUMN: str = "opencode"

# The directories of the agent files: in the project, and in the user directory.
PROJECT_AGENT_DIRECTORY: Path = Path(".opencode") / "agents"
USER_AGENT_DIRECTORY: Path = Path(".config") / "opencode" / "agents"

# The `reason` of a `step_finish` event, mapped to the end state of the run.
# `stop` is a normal end. `length` is the model reaching its output limit. Any
# other reason (for example `tool-calls`) means the stream ended inside the work.
STEP_REASON_END_STATE: dict[str, str] = {"stop": FINISHED, "length": CAPPED}

# The environment variable that holds an inline configuration, which the harness merges with its other configuration.
CONFIG_VARIABLE: str = "OPENCODE_CONFIG_CONTENT"

# The fields of `part.tokens` of a `step_finish` event that the count of new tokens adds. Each step has them.
NEW_FIELDS: tuple[str, ...] = ("input", "output", "reasoning")


# The warning of the harness when it runs another agent than the one that `--agent` names. The text before `Falling back`
# says why: the agent is a subagent, or the harness did not find it (the adapter checks that case first).
FALL_BACK_WARNING = re.compile(r'agent "(?P<name>[^"\n]*)"[^\n]*Falling back to default agent')


def _sealed_entry(sealed: SealedAgent) -> dict[str, object]:
    """The configuration of the seal: the JSON object of its file. Raise AdapterError when the file is not one."""
    path = sealed.directory / f"{sealed.agent}.json"
    try:
        config = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as failure:
        raise AdapterError(f"the sealed file {path} cannot be read as a JSON configuration: {failure}") from failure
    agents = config.get("agent") if isinstance(config, dict) else None
    entry = agents.get(sealed.agent) if isinstance(agents, dict) else None
    if not isinstance(entry, dict):
        raise AdapterError(f"the sealed file {path} holds no entry of agent {sealed.agent!r}")
    return entry


def _environment(servers: tuple[McpServer, ...], sealed: SealedAgent | None = None) -> dict[str, str]:
    """The environment of the harness: the process environment of the engine, with the sealed agent and the servers added to the inline configuration.

    The process environment of the engine (see `environment.process_environment`), unchanged, when there is no server
    and no sealed agent. An inline configuration that the engine already has keeps its other fields, its own servers
    and its other agents. The sealed agent replaces an agent of that name in it. Raise AdapterError when that
    configuration is not a JSON object.
    """
    environment = process_environment()
    if not servers and sealed is None:
        return environment
    try:
        config = json.loads(environment.get(CONFIG_VARIABLE) or "{}")
    except json.JSONDecodeError:
        config = None
    if not isinstance(config, dict) or not all(isinstance(config.get(key, {}), dict) for key in ("mcp", "agent")):
        raise AdapterError(f"{CONFIG_VARIABLE} must hold a JSON object to add the sealed agent and the MCP servers of the stack")
    if sealed is not None:
        config.setdefault("agent", {})[sealed.agent] = _sealed_entry(sealed)
    mcp = config.setdefault("mcp", {}) if servers else {}
    for server in servers:
        if server.url is not None:
            mcp[server.name] = {"type": "remote", "url": server.url}
        else:
            mcp[server.name] = {"type": "local", "command": list(server.command)}
    return {**environment, CONFIG_VARIABLE: json.dumps(config)}


def _tokens_of(event: dict[str, object], stream: Path) -> tuple[int, int]:
    """The two token counts of a `step_finish` event: the new tokens, and the cache reads."""
    tokens = event.get("part", {}).get("tokens")  # type: ignore[union-attr]
    missing = [name for name in NEW_FIELDS if not isinstance(tokens, dict) or name not in tokens]
    if missing:
        raise AdapterError(f"{stream}: a step_finish event has no part.tokens.{missing[0]} field")
    cache = tokens.get("cache") or {}  # type: ignore[union-attr]
    new = sum(int(tokens[name]) for name in NEW_FIELDS) + int(cache.get("write") or 0)  # type: ignore[index]
    return new, int(cache.get("read") or 0)


def _end_state(events: list[dict[str, object]], stream: Path) -> str:
    """The end state of a stream.

    The harness reports an API error as an `error` event and still exits 0, so
    an `error` event anywhere decides the end state. Without one, the `reason`
    of the last `step_finish` event decides it. A stream with no `step_finish`
    event never reached the end of a step.
    """
    if any(event.get("type") == "error" for event in events):
        return FAILED
    finishes = [event for event in events if event.get("type") == "step_finish"]
    if not finishes:
        return FAILED
    try:
        reason = finishes[-1]["part"]["reason"]
    except (KeyError, TypeError):
        raise AdapterError(f"{stream}: the last step_finish event has no part.reason field") from None
    return STEP_REASON_END_STATE.get(reason, FAILED)


# The host variables of the claude-code adapter. OpenCode has no option that takes a settings file or a permission
# mode, so this adapter passes neither, and says so. A host sets OPENCODE_CONFIG instead, which OpenCode reads itself.
CLAUDE_CODE_HOST_VARIABLES: tuple[str, ...] = ("DELEGATE_AGENT_SETTINGS", "DELEGATE_PERMISSION_MODE")


def _warn_of_host_settings() -> None:
    """Write one line to standard error for each host variable of the claude-code adapter that is set."""
    for name in CLAUDE_CODE_HOST_VARIABLES:
        if os.environ.get(name):
            print(
                f"delegate: {name} has no effect with the opencode adapter; set OPENCODE_CONFIG to an OpenCode "
                "configuration file with a permission block instead",
                file=sys.stderr,
            )


class OpenCodeAdapter:
    """Run one agent through `opencode run`."""

    tier_column: str = TIER_COLUMN
    checks_setup: bool = True
    # The guard probe needs a session whose model runs a command that the guard must deny. Nobody measured the
    # enforcement of the permission map of a sealed agent in a live session (#213, owner decision D-e), so there is no probe.
    probes_guard: bool = False
    supports_resume: bool = True

    def run(self, request: AdapterRequest) -> AdapterResult:
        _warn_of_host_settings()
        sealed = request.sealed
        # The harness gets the sealed agent in the inline configuration, and its handle on the command. A request with no
        # seal runs the agent that the harness finds by the name: the engine seals each agent that has a file.
        handle = request.agent if sealed is None else sealed.handle
        command = [COMMAND, "run", "--format", "json", "--model", request.model, "--agent", handle, "--dir", str(request.cwd)]
        if request.resume_session is not None:
            command += ["--session", request.resume_session]
        stream = request.event_stream
        process = run_harness(command, request, env=_environment(request.mcp_servers, sealed))
        # A session that the adapter ended at the agent timeout can have written no event: that is no failure to start.
        if not process.ended_timeout and not stream.read_text().strip():
            raise AdapterError(
                f"{COMMAND} exited with status {process.returncode} and wrote no event for agent {request.agent!r}: {process.stderr.strip()}"
            )
        # An agent that the harness does not find is not an error to the harness: it warns on standard error,
        # runs its default agent, and exits 0. The rules of the pair would then not apply, so the adapter stops.
        if f'agent "{request.agent}" not found' in process.stderr:
            raise AdapterError(
                f"{COMMAND} did not find agent {request.agent!r} and ran its default agent instead: {process.stderr.strip()}"
            )
        events = read_events(stream)
        # A subagent is refused for `opencode run` in the same way, and the harness exits 0 and runs its default agent. The
        # rules of the agent would not apply, so the session fails. It has no session id: a continuation must not resume it.
        fallen = FALL_BACK_WARNING.search(process.stderr)
        if fallen is not None:
            return AdapterResult(
                process.returncode, FAILED, None, stream, process.ended_idle, ended_timeout=process.ended_timeout,
                reason=(
                    f"{COMMAND} did not run agent {fallen['name']!r} and ran its default agent instead: "
                    f"{fallen.group(0)}; an agent for `{COMMAND} run` needs `mode: all`"
                ),
            )
        session_id = next((event["sessionID"] for event in events if "sessionID" in event), None)
        # The last text part of the session is the final text of the agent.
        texts = [
            str(event["part"]["text"])  # type: ignore[index]
            for event in events
            if event.get("type") == "text" and isinstance(event.get("part"), dict) and "text" in event["part"]  # type: ignore[operator]
        ]
        final_text = texts[-1] if texts else None
        return AdapterResult(
            process.returncode, _end_state(events, stream), session_id, stream, process.ended_idle, final_text=final_text,
            ended_timeout=process.ended_timeout,
        )

    def agent_paths(self, agent: str, source: Path) -> tuple[Path, ...]:
        """The paths where the harness looks for the agent file: the project directory of `source`, then the user directory."""
        return tuple(
            directory / f"{agent}.md" for directory in (source / PROJECT_AGENT_DIRECTORY, Path.home() / USER_AGENT_DIRECTORY)
        )

    def seal(self, agent: str, source: Path, into: Path) -> SealedAgent | None:
        """Write the agent file of `agent` for `source` as an OpenCode configuration in `into`. The handle is the agent name.

        The file is `<agent>.json`: the entry of `agent` holds the frontmatter of the agent file with `mode: all`, and the
        body of the file as `prompt`. None when there is no agent file. Raise AdapterError when the file has no usable frontmatter.
        """
        found = next((path for path in self.agent_paths(agent, source) if path.is_file()), None)
        if found is None:
            return None
        try:
            frontmatter, body, error = split_frontmatter(found.read_text())
        except (OSError, UnicodeDecodeError) as failure:
            raise AdapterError(f"the agent file {found} cannot be read: {failure}") from failure
        if error is not None or frontmatter is None:
            raise AdapterError(f"the agent file {found} has no usable frontmatter: {error}")
        into.mkdir(parents=True, exist_ok=True)
        sealed = into / f"{agent}.json"
        configuration = {"agent": {agent: {**frontmatter, "mode": "all", "prompt": body.strip()}}}
        sealed.write_text(json.dumps(configuration, indent=2) + "\n")
        return SealedAgent(agent, agent, into, (SealedFile(sealed, hashlib.sha256(sealed.read_bytes()).hexdigest()),), found)

    def permits(self, sealed: SealedAgent, command: str) -> str | None:
        """Resolve `command` against the `permission.bash` map of the sealed configuration with the matcher of the validator.

        The map permits the command when it resolves to `allow`. An agent with no permission map permits every command.
        """
        try:
            entry = _sealed_entry(sealed)
        except AdapterError as failure:
            return str(failure)
        permission = entry.get("permission")
        action = opencode_bash_action(permission, command) if isinstance(permission, dict) else "allow"
        return None if action == "allow" else f"its permission.bash map resolves the command to {action or 'no rule'}"

    def activity(self, stream: Path) -> Activity:
        new = cache = 0
        last = None
        for event in read_events(stream):
            if "timestamp" in event:
                last = datetime.fromtimestamp(int(event["timestamp"]) / 1000, timezone.utc)  # type: ignore[call-overload]
            if event.get("type") == "step_finish":
                more, read = _tokens_of(event, stream)
                new, cache = new + more, cache + read
        return Activity(new, cache, last)
