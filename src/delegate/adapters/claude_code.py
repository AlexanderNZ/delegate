"""The Claude Code adapter: drive `claude` in headless mode.

The command is `claude --print --output-format stream-json --verbose --model
<model> --setting-sources user --plugin-dir <seal directory> --agent <handle>
--strict-mcp-config [--mcp-config <servers>] --disallowedTools Monitor,TaskStop`, with
`CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1` in its environment, so each agent runs
every command in the foreground. `--strict-mcp-config` makes the harness ignore
the MCP servers of the user, of the project, and of the plugins, so the agent
gets only the MCP servers of its stack: none, or the servers that
`--mcp-config` names. A host can give every agent a settings file and
a permission mode: `DELEGATE_AGENT_SETTINGS` adds `--settings <path>`, and
`DELEGATE_PERMISSION_MODE` adds `--permission-mode <mode>`. With neither set, the
command does not change. An error tool result that says that a permission mode
refused a tool goes into `refused` of the result, and the command and the text of each error result of a `Bash` call
go into `command_errors`. The prompt goes on standard input, never in
argv, so a long brief meets no argument limit and shows in no process list. The
harness writes one JSON event on each line of standard output. The adapter
copies that stream to the file that the request names, and reads the final
`result` event from it. The end state comes from that event only: the exit
status does not change it (see `AdapterResult`). After a valid report, the
adapter ends a session that writes no event for the idle timeout of the request
(see `process.py`). It also ends a session that runs past the agent timeout of the request, and sets `ended_timeout`.

A session runs from the sealed agent of its run (#217), as a plugin. `seal` writes the plugin into the seal directory:
`.claude-plugin/plugin.json` (a name that starts with `delegate-seal-`), `agents/<agent>.md` (the agent file with no
`hooks` field), `hooks/hooks.json` (the `hooks` field of the agent file, with no change), and `skills/<skill>/` (a copy of
each project skill that the `skills` list of the agent names). The harness gives the agent of a plugin as
`<plugin>:<agent>`, and that is the handle. A plugin agent ignores `hooks` in its frontmatter, and the plugin runs the
hooks of `hooks/hooks.json`. `--setting-sources user` makes the session ignore the project settings, the project agents
and the project skills of its tree, so a file that a specialist commits in a tree cannot turn off a hook or replace the
agent (measured on 2.1.287, see issue 213). A skill that the `skills` list names and the project does not hold is a
user skill, which the session loads by its own name. The files of the seal also hold the user settings
`~/.claude/settings.json` when it exists, so that a change of them shows. The adapter refuses to seal for a harness
older than 2.1.287. The body of the agent file ends with the section `## Instructions of the repository` (#218): the text
of `CLAUDE.md` and `.claude/CLAUDE.md` of the main checkout, with their `@<path>` imports replaced, because
`--setting-sources user` does not load them. The harness does not preload the skills of the `skills` list under
`--agent` (measured on 2.1.287, #215), so the prompt of a new session starts with the text of each of these skills, with
each plain load-time block run in the working directory of the session (see `skill_text.py`). After a session, it reads the `init` event of the stream: when the agents that it lists do not hold
the handle, the session ends as failed with a reason.

The activity of a stream comes from the `result` events, the `assistant`
events, and the `timestamp` field. Some events of an agent that works have no
`timestamp`: a `tool_progress` heartbeat while a tool runs, and a `system`
event with the subtype `thinking_tokens` while the model thinks. When the newest
event of the stream is one of these, the time is the modification time of the
stream file: the time that the harness last wrote to it. The harness writes
one `result` event at the end of each session, and its `usage` holds the true
count of the session. The
`usage` of an `assistant` event is partial: its output count is not final. The
harness writes one event for each content block of a message, and each event of
a message repeats the usage of that message. So the token counts add the
`usage` of each `result` event. For the part of the stream after the last
`result` event (a session that runs now), they add the newest usage of each
message id once. A `usage` object gives two counts: the new tokens (the input,
the cache writes, and the output) and the cache reads.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..definitions.validate import split_frontmatter
from ..ports.harness import (
    CAPPED, FAILED, FINISHED, Activity, AdapterError, AdapterRequest, AdapterResult, McpServer, McpTool, SealedAgent,
    SealedFile,
)
from .environment import process_environment
from .process import run_harness
from .skill_text import Skill, with_skills
from .streams import read_events

# The command that starts the harness.
COMMAND: str = "claude"

# The column of the tier table that holds the Claude Code models.
TIER_COLUMN: str = "claude-code"

# The time that one run of a hook can take.
HOOK_TIMEOUT: float = 30.0

# The directory of the agent files, in the project and in the user directory.
AGENT_DIRECTORY: Path = Path(".claude") / "agents"

# The directory of the project skills, and the user settings file under the home directory.
SKILL_DIRECTORY: Path = Path(".claude") / "skills"
USER_SETTINGS: Path = Path(".claude") / "settings.json"

# The files of the instructions of a repository, in the order that the seal reads them, the heading of the section that
# holds them, and the depth to which the seal replaces an import in an imported file.
INSTRUCTION_FILES: tuple[Path, ...] = (Path("CLAUDE.md"), Path(".claude") / "CLAUDE.md")
INSTRUCTIONS_HEADING: str = "## Instructions of the repository"
IMPORT_DEPTH: int = 5

# A line that is one import: `@` and a path, with no other word on the line. A line that opens or closes a code block.
IMPORT_LINE = re.compile(r"^@(\S+)$")
CODE_FENCE = re.compile(r"^(`{3,}|~{3,})")

# The name of the plugin of a seal starts with this text. The name ends with the agent name.
PLUGIN_PREFIX: str = "delegate-seal-"

# The first version that the measurements of the sealed agent cover (issue 213), and the time that `--version` can take.
MINIMUM_VERSION: tuple[int, int, int] = (2, 1, 287)
VERSION_TIMEOUT: float = 30.0

# The tools that the adapter disallows for every agent. Under `--print` the
# notification of a monitor never comes to the agent, and a monitor that stays
# open keeps the session alive after the report. `TaskStop` only stops a
# background task, and the environment below removes the background tasks.
DISALLOWED_TOOLS: tuple[str, ...] = ("Monitor", "TaskStop")

# The environment that removes the background tasks. The `run_in_background`
# parameter of the Bash tool and of the subagent tool is not a tool name, so
# `--disallowedTools` cannot remove it.
FOREGROUND_ENVIRONMENT: dict[str, str] = {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1"}

# The environment variables of a host that give every agent a settings file and a permission mode. A host whose
# user settings refuse each tool that is not read-only (for example `defaultMode` "dontAsk" with an empty allow
# list) sets them, so the agents of a run can work. The verifier guard hook of the agent file applies on top.
SETTINGS_VARIABLE: str = "DELEGATE_AGENT_SETTINGS"
PERMISSION_MODE_VARIABLE: str = "DELEGATE_PERMISSION_MODE"

# The name of the shell tool of the harness.
SHELL_TOOL: str = "Bash"

# The text of a tool result when a permission mode refused the tool, as Claude Code wrote it on 2026-10-18.
REFUSAL = re.compile(r"Permission to use (\S+) has been denied because Claude Code is running in (.+?) mode")

# The fields of a `usage` object that its count of new tokens adds: the input, the cache writes, and the output.
NEW_FIELDS: tuple[str, ...] = ("input_tokens", "cache_creation_input_tokens", "output_tokens")

# The fields of a `usage` object that its count of cache tokens adds: the cache reads.
CACHE_FIELDS: tuple[str, ...] = ("cache_read_input_tokens",)


def _mcp_config(servers: tuple[McpServer, ...]) -> str:
    """The `--mcp-config` value of the servers: one JSON object in the form of the harness configuration."""
    entries: dict[str, dict[str, object]] = {}
    for server in servers:
        if server.url is not None:
            entries[server.name] = {"type": "http", "url": server.url}
        else:
            entries[server.name] = {"command": server.command[0], "args": list(server.command[1:])}
    return json.dumps({"mcpServers": entries})


def _is_work_with_no_time(event: dict[str, object]) -> bool:
    """True for an event that the harness writes while the agent works, with no `timestamp`.

    The harness writes a `tool_progress` heartbeat every 30 seconds while a tool
    runs, and a `system` event with the subtype `thinking_tokens` while the model
    thinks. Other events with no `timestamp` (`init`, `rate_limit_event`, `result`)
    are not work that goes on.
    """
    kind = event.get("type")
    return kind == "tool_progress" or (kind == "system" and event.get("subtype") == "thinking_tokens")


def _tokens_of(usage: dict[str, object]) -> tuple[int, int]:
    """The two token counts of a `usage` object: the new tokens, and the cache reads."""
    def count(fields: tuple[str, ...]) -> int:
        return sum(int(usage.get(name) or 0) for name in fields)  # type: ignore[call-overload]

    return count(NEW_FIELDS), count(CACHE_FIELDS)


def _host_permissions() -> list[str]:
    """The options that give the agent the settings file and the permission mode of the host, or none.

    A settings file that does not exist stops the adapter before it starts an
    agent: with no settings the agent cannot work on such a host, and the
    failure would show only as a refused tool.
    """
    options: list[str] = []
    settings = os.environ.get(SETTINGS_VARIABLE)
    if settings:
        # The agent runs in its worktree, not in the directory of the engine, so a relative path can name another file.
        if not Path(settings).is_absolute():
            raise AdapterError(f"{SETTINGS_VARIABLE} is {settings!r}, which is not an absolute path")
        if not Path(settings).is_file():
            raise AdapterError(f"{SETTINGS_VARIABLE} names {settings}, which is not a file")
        options += ["--settings", settings]
    mode = os.environ.get(PERMISSION_MODE_VARIABLE)
    if mode:
        options += ["--permission-mode", mode]
    return options


def _refusals(event: dict[str, object]) -> list[tuple[str, str]]:
    """The (tool, mode) pair of each error tool result of the event that says that a permission mode refused the tool.

    Only a result with `is_error` true counts. A Read or a grep of a file that
    quotes the refusal text gives a result that is no error.
    """
    message = event.get("message")
    if event.get("type") != "user" or not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return []
    found: list[tuple[str, str]] = []
    for block in message["content"]:
        if not isinstance(block, dict) or block.get("type") != "tool_result" or block.get("is_error") is not True:
            continue
        found += [(match.group(1), match.group(2)) for text in _texts_of(block) for match in REFUSAL.finditer(text)]
    return found


def _texts_of(block: dict[str, object]) -> list[str]:
    """The texts of a `tool_result` block: its content as one string, or the `text` of each part of its content."""
    content = block.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [str(part.get("text", "")) for part in content if isinstance(part, dict)]
    return []


def _blocks(event: dict[str, object], role: str, kind: str) -> list[dict[str, object]]:
    """The content blocks of type `kind` in an event of type `role` (`assistant` or `user`)."""
    message = event.get("message")
    if event.get("type") != role or not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return []
    return [block for block in message["content"] if isinstance(block, dict) and block.get("type") == kind]


def _command_errors(events: list[dict[str, object]]) -> tuple[tuple[str, str], ...]:
    """The (command, text) pair of each `Bash` tool call whose tool result is an error, in the order of the results.

    A result goes with the call whose `id` is its `tool_use_id`. A result with no such call is not in the pairs.
    """
    commands: dict[str, str] = {}
    found: list[tuple[str, str]] = []
    for event in events:
        for block in _blocks(event, "assistant", "tool_use"):
            tool_input = block.get("input")
            if block.get("name") == SHELL_TOOL and isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
                commands[str(block.get("id"))] = tool_input["command"]
        for block in _blocks(event, "user", "tool_result"):
            call = str(block.get("tool_use_id"))
            if block.get("is_error") is True and call in commands:
                found.append((commands[call], "\n".join(_texts_of(block))))
    return tuple(found)


def _end_state(result: dict[str, object]) -> str:
    """The end state that a `result` event gives.

    The harness sets `subtype` to `error_max_turns` when the agent reaches the
    turn cap. An API error sets `is_error` to true and leaves `subtype` as
    `success`, so `is_error` decides all other failures.
    """
    if result.get("subtype") == "error_max_turns":
        return CAPPED
    if result.get("is_error") or str(result.get("subtype", "")).startswith("error"):
        return FAILED
    return FINISHED


class ClaudeCodeAdapter:
    """Run one agent through `claude --print`."""

    tier_column: str = TIER_COLUMN
    checks_setup: bool = True
    probes_guard: bool = True
    supports_resume: bool = True

    def run(self, request: AdapterRequest) -> AdapterResult:
        sealed = request.sealed
        if sealed is None:
            raise AdapterError(
                f"the run has no sealed agent for {request.agent!r}: the Claude Code adapter starts an agent only from the sealed agent of the run"
            )
        # `--setting-sources user` makes the session ignore the project settings, agents and skills of its tree. A resumed
        # session gets the same options.
        command = [
            COMMAND, "--print", "--output-format", "stream-json", "--verbose",
            "--model", request.model, "--setting-sources", "user", "--plugin-dir", str(sealed.directory),
            "--agent", sealed.handle, "--strict-mcp-config", *_host_permissions(),
        ]
        if request.mcp_servers:
            command += ["--mcp-config", _mcp_config(request.mcp_servers)]
        # The proof copy of a verifier is outside its working directory, and a dontAsk host refuses a tool call there
        # unless the directory is added to the session (#153).
        for directory in request.added_directories:
            command += ["--add-dir", str(directory)]
        # `--mcp-config` and `--add-dir` take more than one value, so this option ends their values.
        command += ["--disallowedTools", ",".join(DISALLOWED_TOOLS)]
        if request.resume_session is not None:
            command += ["--resume", request.resume_session]
        stream = request.event_stream
        env = {**process_environment(), **FOREGROUND_ENVIRONMENT}
        if request.resume_session is None:
            # The harness does not preload the skills of the agent under `--agent` (#215). A resumed session has them already.
            skills = _skills_of(sealed)
            request = dataclasses.replace(request, prompt=with_skills(request.prompt, skills, request.cwd, {**env, **request.environment}))
        process = run_harness(command, request, env=env)
        # A session that the adapter ended at the agent timeout can have written no event: that is no failure to start.
        if not process.ended_timeout and not stream.read_text().strip():
            raise AdapterError(
                f"{COMMAND} exited with status {process.returncode} and wrote no event for agent {request.agent!r}: {process.stderr.strip()}"
            )
        end_state, session_id, final_text = FAILED, None, None
        refused: list[tuple[str, str]] = []
        events = read_events(stream)
        for event in events:
            if event.get("type") == "result":
                end_state, session_id = _end_state(event), event["session_id"]
                final_text = event["result"] if isinstance(event.get("result"), str) else final_text
            refused += _refusals(event)
        init = next((event for event in events if event.get("type") == "system" and event.get("subtype") == "init"), None)
        if init is not None and sealed.handle not in _listed_agents(init):
            # The session did not run the sealed agent, so its report is not the work of that agent. No session id: a
            # continuation must not resume this session.
            return AdapterResult(
                process.returncode, FAILED, None, stream, refused=tuple(refused), final_text=final_text,
                ended_timeout=process.ended_timeout, reason=f"the harness did not load the sealed agent {sealed.handle}",
                command_errors=_command_errors(events),
            )
        return AdapterResult(
            process.returncode, end_state, session_id, stream, process.ended_idle, tuple(refused), final_text=final_text,
            ended_timeout=process.ended_timeout, command_errors=_command_errors(events),
        )

    def agent_paths(self, agent: str, source: Path) -> tuple[Path, ...]:
        """The paths where the harness looks for the agent file: the project directory of `source`, then the user directory."""
        return tuple(directory / f"{agent}.md" for directory in (source / AGENT_DIRECTORY, Path.home() / AGENT_DIRECTORY))

    def seal(self, agent: str, source: Path, into: Path, mcp_tools: tuple[McpTool, ...] = ()) -> SealedAgent | None:
        """Write the plugin of `agent` for `source` into `into`. None when there is no agent file.

        The handle is `<plugin>:<agent>`, and the plugin name is `delegate-seal-<agent>`. Raise AdapterError for a
        harness older than 2.1.287, and for an agent file with no usable frontmatter. Each tool of `mcp_tools` goes to
        the `tools` of the sealed agent file as `mcp__<server>__<tool>` (#257), when the agent file has `tools`.
        """
        found = next((path for path in self.agent_paths(agent, source) if path.is_file()), None)
        if found is None:
            return None
        _check_version()
        try:
            frontmatter, body, error = split_frontmatter(found.read_text())
        except (OSError, UnicodeDecodeError) as failure:
            raise AdapterError(f"the agent file {found} cannot be read: {failure}") from None
        if error is not None or frontmatter is None:
            raise AdapterError(f"the agent file {found} has no usable frontmatter: {error}")
        plugin = f"{PLUGIN_PREFIX}{agent}"
        into.mkdir(parents=True, exist_ok=True)
        (into / ".claude-plugin").mkdir(exist_ok=True)
        (into / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": plugin, "version": "0.0.0", "description": f"The sealed agent {agent} of a delegate run"}, indent=2) + "\n"
        )
        hooks = frontmatter.pop("hooks", None)
        if hooks is not None:
            (into / "hooks").mkdir(exist_ok=True)
            (into / "hooks" / "hooks.json").write_text(json.dumps({"hooks": hooks}, indent=2) + "\n")
        if "skills" in frontmatter:
            frontmatter["skills"] = _seal_skills(frontmatter["skills"], plugin, source, into)
        if mcp_tools and "tools" in frontmatter:
            frontmatter["tools"] = _with_mcp_tools(frontmatter["tools"], mcp_tools)
        (into / "agents").mkdir(exist_ok=True)
        text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True, width=1_000_000, default_flow_style=False)
        (into / "agents" / f"{agent}.md").write_text(f"---\n{text}---{body}{_instructions(source)}")
        files = [SealedFile(path, _digest(path)) for path in sorted(into.rglob("*")) if path.is_file()]
        settings = Path.home() / USER_SETTINGS
        if settings.is_file():
            files.append(SealedFile(settings, _digest(settings)))
        return SealedAgent(agent, f"{plugin}:{agent}", into, tuple(files), found)

    def permits(self, sealed: SealedAgent, command: str) -> str | None:
        """Run each Bash hook of the sealed `hooks/hooks.json` with `command` on standard input, as the harness does.

        A hook that exits 0 permits the command. Any other exit status denies it: the guard blocks with status 2.
        The reason names the exit status and the message of the hook. A sealed file that lists tools but not `Bash`
        denies every command.
        """
        agent_file = sealed.directory / "agents" / f"{sealed.agent}.md"
        try:
            frontmatter, _, error = split_frontmatter(agent_file.read_text())
        except (OSError, UnicodeDecodeError) as failure:
            return f"the sealed file {agent_file} cannot be read: {failure}"
        if error is not None or frontmatter is None:
            return f"the sealed file {agent_file} has no usable frontmatter: {error}"
        hooks_file = sealed.directory / "hooks" / "hooks.json"
        hooks = None
        if hooks_file.is_file():
            try:
                hooks = json.loads(hooks_file.read_text()).get("hooks")
            except (OSError, ValueError, AttributeError) as failure:
                return f"the sealed file {hooks_file} cannot be read: {failure}"
        tools = frontmatter.get("tools")
        listed = [t.strip() for t in tools.split(",")] if isinstance(tools, str) else list(tools or [])
        if tools is not None and "Bash" not in listed:
            return "the agent does not list the Bash tool"
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        shell = shutil.which("bash") or shutil.which("sh") or "sh"
        for hook in _bash_hooks(hooks):
            try:
                process = subprocess.run([shell, "-c", hook], input=payload, capture_output=True, text=True, timeout=HOOK_TIMEOUT)
            except (OSError, subprocess.TimeoutExpired) as failure:
                return f"its Bash hook could not run: {failure}"
            if process.returncode != 0:
                message = " ".join(process.stderr.split())
                return f"its Bash hook exits {process.returncode}" + (f" and says: {message}" if message else "")
        return None

    def activity(self, stream: Path) -> Activity:
        new = cache = 0
        usage_of: dict[str, dict[str, object]] = {}  # the turns of the session after the last `result` event
        last: datetime | None = None
        events = read_events(stream)
        for event in events:
            if "timestamp" in event:
                last = datetime.fromisoformat(str(event["timestamp"]))
            message = event.get("message")
            if event.get("type") == "result" and isinstance(event.get("usage"), dict):
                more, read = _tokens_of(event["usage"])  # type: ignore[arg-type]
                new, cache = new + more, cache + read
                usage_of = {}
            elif event.get("type") == "assistant" and isinstance(message, dict) and isinstance(message.get("usage"), dict):
                usage_of[str(message.get("id"))] = message["usage"]
        if events and "timestamp" not in events[-1] and _is_work_with_no_time(events[-1]):
            last = datetime.fromtimestamp(stream.stat().st_mtime, timezone.utc)
        for usage in usage_of.values():
            more, read = _tokens_of(usage)
            new, cache = new + more, cache + read
        return Activity(new, cache, last)


def _bash_hooks(hooks: object) -> list[str]:
    """The commands of the `PreToolUse` hooks on `Bash` in the `hooks` field of an agent."""
    commands: list[str] = []
    if not isinstance(hooks, dict):
        return commands
    for entry in hooks.get("PreToolUse") or []:
        if not isinstance(entry, dict) or entry.get("matcher") != "Bash":
            continue
        commands.extend(str(h["command"]) for h in entry.get("hooks") or [] if isinstance(h, dict) and "command" in h)
    return commands


def _instructions(source: Path) -> str:
    """The section of the instructions of the repository of `source`, or an empty text when it has no `CLAUDE.md`.

    The section holds the text of `CLAUDE.md` and of `.claude/CLAUDE.md`, each one that exists, in that order, with the
    imports replaced (see `_expand`). `CLAUDE.local.md` holds personal settings, so the section never holds it.
    """
    texts: list[str] = []
    for relative in INSTRUCTION_FILES:
        path = source / relative
        if not path.is_file():
            continue
        try:
            texts.append(_expand(path.read_text(), path.parent, IMPORT_DEPTH).strip("\n"))
        except (OSError, UnicodeDecodeError) as failure:
            raise AdapterError(f"the instructions file {path} cannot be read: {failure}") from None
    if not texts:
        return ""
    return f"\n{INSTRUCTIONS_HEADING}\n\n" + "\n\n".join(texts) + "\n"


def _expand(text: str, directory: Path, depth: int) -> str:
    """Replace each line of `text` that is one import, `@<path>`, with the text of that file.

    A path counts from `directory`, the directory of the file that holds the line. The text of an imported file is
    expanded in the same way, one level deeper, until `depth` is 0. A file that is missing or cannot be read keeps its
    import line, and so does a line in a code block.
    """
    lines: list[str] = []
    fence: str | None = None  # the characters that opened the code block that the line is in
    for line in text.splitlines():
        marker = CODE_FENCE.match(line.lstrip())
        if marker is not None and fence is None:
            fence = marker.group(1)
        elif marker is not None and fence is not None and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence):
            fence = None
        elif fence is None and depth > 0 and (found := IMPORT_LINE.match(line.strip())) is not None:
            try:
                target = (directory / Path(found.group(1)).expanduser()).resolve()
                lines.append(_expand(target.read_text(), target.parent, depth - 1).strip("\n"))
                continue
            except (OSError, ValueError, RuntimeError):  # a path with no file, a text that is not UTF-8, no home directory
                pass
        lines.append(line)
    return "\n".join(lines) + "\n"


def _digest(path: Path) -> str:
    """The SHA-256 of the file as a hexadecimal text."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _with_mcp_tools(tools: object, mcp_tools: tuple[McpTool, ...]) -> object:
    """The `tools` field of an agent file with `mcp__<server>__<tool>` for each tool of `mcp_tools` at its end (#257).

    The harness gives an agent with a `tools` list only the MCP tools that the list names (measured on 2.1.287, #257).
    A field of the text form `Read, Grep` stays text, and a list stays a list. A name that the field holds already is
    not added again. A field of another type does not change: the harness reads no tool from it.
    """
    names = [f"mcp__{tool.server}__{tool.name}" for tool in mcp_tools]
    if isinstance(tools, str):
        listed = [name.strip() for name in tools.split(",") if name.strip()]
        return ", ".join([*listed, *(name for name in names if name not in listed)])
    if isinstance(tools, list):
        return [*tools, *(name for name in names if name not in tools)]
    return tools


def _seal_skills(skills: object, plugin: str, source: Path, into: Path) -> object:
    """Copy each project skill that the `skills` list names into the plugin, and prefix its name with the plugin.

    A skill that the project of `source` does not hold is a user skill: the session loads it by its own name, so it is not
    copied and its name does not change. A value that is not a list or a text stays as it is.
    """
    names = [part.strip() for part in skills.split(",") if part.strip()] if isinstance(skills, str) else skills
    if not isinstance(names, list):
        return skills
    sealed: list[object] = []
    for name in names:
        project = source / SKILL_DIRECTORY / str(name)
        if isinstance(name, str) and name and "/" not in name and project.is_dir():
            shutil.copytree(project, into / "skills" / name)
            sealed.append(f"{plugin}:{name}")
        else:
            sealed.append(name)
    return sealed


def _skills_of(sealed: SealedAgent) -> list[Skill]:
    """Each skill that the `skills` list of the sealed agent file names, in its order, with its directory.

    A name `<plugin of the seal>:<skill>` is a project skill that the seal copied: its directory is in the seal, and its
    name is the part after the colon. A name with no colon is a user skill. A name of another plugin has no directory
    that the adapter knows. No skill when the sealed file cannot be read or lists no skill.
    """
    try:
        frontmatter, _, error = split_frontmatter((sealed.directory / "agents" / f"{sealed.agent}.md").read_text())
    except (OSError, UnicodeDecodeError):
        return []
    listed = frontmatter.get("skills") if error is None and frontmatter is not None else None
    names = [part.strip() for part in listed.split(",") if part.strip()] if isinstance(listed, str) else listed
    if not isinstance(names, list):
        return []
    plugin = f"{sealed.handle.split(':')[0]}:"
    skills: list[Skill] = []
    for name in (str(entry) for entry in names if isinstance(entry, str) and entry):
        if name.startswith(plugin) and "/" not in name:
            skills.append(Skill(name[len(plugin):], sealed.directory / "skills" / name[len(plugin):]))
        elif ":" not in name and "/" not in name:
            skills.append(Skill(name, Path.home() / SKILL_DIRECTORY / name))
        else:
            skills.append(Skill(name, None))
    return skills


def _listed_agents(init: dict[str, object]) -> list[str]:
    """The names of the agents that the `init` event lists. An entry is a text, or an object with a `name`."""
    listed = init.get("agents")
    if not isinstance(listed, list):
        return []
    return [entry if isinstance(entry, str) else str(entry.get("name")) for entry in listed if isinstance(entry, (str, dict))]


def _check_version() -> None:
    """Raise AdapterError when `claude --version` names a version before the minimum.

    A harness that is not installed, or whose output has no version, is no refusal here: the session fails with its own
    error when it starts.
    """
    try:
        done = subprocess.run([COMMAND, "--version"], capture_output=True, text=True, timeout=VERSION_TIMEOUT, env=process_environment())
    except (OSError, subprocess.TimeoutExpired):
        return
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", done.stdout)
    if match is None:
        return
    found = tuple(int(part) for part in match.groups())
    if found < MINIMUM_VERSION:
        raise AdapterError(
            f"Claude Code {'.'.join(map(str, found))} is older than {'.'.join(map(str, MINIMUM_VERSION))}, "
            "the first version that the sealed agent was measured on; update Claude Code"
        )
