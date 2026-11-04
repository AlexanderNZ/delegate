"""`delegate run` gives the MCP servers that a stack names to the agents of that stack, and to no other agent.

Each case writes a workflow into a real temporary git repository and calls
`delegate.main`. The scripted adapter records each request that the engine
gives it. The `claude` process is a system boundary, so a stand-in command
records its arguments.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from delegate import adapters
from delegate.cli import delegate
from delegate.ports.harness import McpServer, McpTool

from . import claude_fake, opencode_fake
from .support import WORKFLOW, ScriptedAdapter, make_repo, read_journal, run_main, write_project_agents

pytestmark = pytest.mark.usefixtures("home", "claude_stand_in")  # the dry run looks in the user directory for agent files (#182), and probes the guard (#219)

BROWSER = McpServer("browser", command=("npx", "@playwright/mcp@latest"))
TRACKER = McpServer("tracker", url="https://mcp.example.invalid/mcp")

SERVERS = """
[stacks.python.mcp-servers.browser]
command = ["npx", "@playwright/mcp@latest"]

[stacks.python.mcp-servers.tracker]
url = "https://mcp.example.invalid/mcp"
"""

DOCS_STACK = """
[stacks.docs]
specialist = "docs-specialist"
verifier = "docs-verifier"
gates = ["test -f export.md"]
hotspots = []
"""

DOCS_TICKET = """
[[tickets]]
id = "b"
text = "Ticket b documents the export."
stack = "docs"
blocked-by = []
"""


def with_servers(workflow: str) -> str:
    """The workflow with the two servers in the stack `python`, a stack `docs` with no server, and one ticket of each."""
    head, tickets = workflow.split("[[tickets]]", 1)
    return head + SERVERS + DOCS_STACK + "\n[[tickets]]" + tickets + DOCS_TICKET


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={"b": {"export.md": "docs\n"}})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def test_the_specialist_and_the_verifier_of_a_stack_get_its_mcp_servers_and_the_agents_of_another_stack_get_none(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, with_servers(WORKFLOW))

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 0, err
    servers_of = {request.agent: request.mcp_servers for request in [*scripted.calls, *scripted.verifier_calls]}
    assert servers_of == {
        "python-specialist": (BROWSER, TRACKER),
        "python-verifier": (BROWSER, TRACKER),
        "docs-specialist": (),
        "docs-verifier": (),
    }


def test_the_claude_code_command_of_the_specialist_holds_the_mcp_servers_of_its_stack(tmp_path, monkeypatch, capsys):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"').replace("\n[[tickets]]", SERVERS + "\n[[tickets]]"))
    write_project_agents(repo)

    run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    (call,) = fake.calls()
    argv = call["argv"]
    assert "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {
        "browser": {"command": "npx", "args": ["@playwright/mcp@latest"]},
        "tracker": {"type": "http", "url": "https://mcp.example.invalid/mcp"},
    }}


def test_only_the_seal_of_the_verifier_of_the_stack_gets_the_verifier_tools_of_its_servers(tmp_path, capsys, scripted):
    workflow = with_servers(WORKFLOW).replace(
        'url = "https://mcp.example.invalid/mcp"\n', 'url = "https://mcp.example.invalid/mcp"\nverifier-tools = ["issue_read", "search"]\n', 1,
    )
    repo = make_repo(tmp_path, workflow)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 0, err
    assert scripted.sealed_tools == {"python-verifier": (McpTool("tracker", "issue_read"), McpTool("tracker", "search"))}


HOTSPOTS = 'hotspots = ["LICENSE", ".github/workflows/*"]\n'


def dry_run(tmp_path, capsys, servers: str, stack_field: str = "") -> tuple[int, str, str]:
    """`delegate run --dry-run` of a workflow with `servers` before the tickets and `stack_field` in the stack `python`."""
    workflow = WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"').replace(HOTSPOTS, HOTSPOTS + stack_field)
    repo = make_repo(tmp_path, workflow.replace("\n[[tickets]]", servers + "\n[[tickets]]"))
    write_project_agents(repo)
    try:
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])
    except SystemExit as stop:
        code = stop.code
    out, err = capsys.readouterr()
    return code, out, err


def test_a_stack_with_mcp_servers_passes_the_check(tmp_path, capsys):
    code, out, err = dry_run(tmp_path, capsys, SERVERS)

    assert (code, err) == (0, "")


@pytest.mark.parametrize(
    ("servers", "stack_field", "expected"),
    [
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = ["npx"]\nurl = "https://x.invalid"\n', "",
                     ["stacks.python.mcp-servers.browser", "command", "url", "one of"], id="command-and-url"),
        pytest.param("\n[stacks.python.mcp-servers.browser]\n", "",
                     ["stacks.python.mcp-servers.browser", "command", "url", "one of"], id="neither-command-nor-url"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = []\n', "",
                     ["stacks.python.mcp-servers.browser.command", "at least one"], id="empty-command"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = "npx @playwright/mcp"\n', "",
                     ["stacks.python.mcp-servers.browser.command", "list of strings"], id="command-not-a-list"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\nurl = 8080\n', "",
                     ["stacks.python.mcp-servers.browser.url", "string"], id="url-not-a-string"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = ["npx"]\nenv = { TOKEN = "x" }\n', "",
                     ["stacks.python.mcp-servers.browser", "env", "unknown field"], id="unknown-server-field"),
        pytest.param("", 'mcp-servers = ["browser"]\n',
                     ["stacks.python.mcp-servers", "table"], id="servers-not-a-table"),
        pytest.param("", 'mcp-servers = { browser = "npx" }\n',
                     ["stacks.python.mcp-servers.browser", "table"], id="server-not-a-table"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = ["npx"]\nverifier-tools = ["*"]\n', "",
                     ["stacks.python.mcp-servers.browser.verifier-tools", "'*'", "exact name"], id="verifier-tools-star"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = ["npx"]\nverifier-tools = ["browser_*"]\n', "",
                     ["stacks.python.mcp-servers.browser.verifier-tools", "'browser_*'", "exact name"], id="verifier-tools-pattern"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = ["npx"]\nverifier-tools = [""]\n', "",
                     ["stacks.python.mcp-servers.browser.verifier-tools", "list of strings", "['']"], id="verifier-tools-empty-name"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = ["npx"]\nverifier-tools = []\n', "",
                     ["stacks.python.mcp-servers.browser.verifier-tools", "at least one"], id="verifier-tools-empty-list"),
        pytest.param('\n[stacks.python.mcp-servers.browser]\ncommand = ["npx"]\nverifier-tools = "browser_snapshot"\n', "",
                     ["stacks.python.mcp-servers.browser.verifier-tools", "list of strings"], id="verifier-tools-not-a-list"),
    ],
)
def test_a_malformed_mcp_server_exits_1_and_names_the_field(tmp_path, capsys, servers, stack_field, expected):
    code, out, err = dry_run(tmp_path, capsys, servers, stack_field)

    assert code == 1
    assert out == ""
    assert "Traceback" not in err
    for part in expected:
        assert part in err


GRAPH = """
[stacks.python.mcp-servers.graph]
command = ["graph-server", "--stdio"]
verifier-tools = ["explore"]
"""


def sealed_record(out: str) -> dict[str, dict[str, object]]:
    """The record of each seal in the `run-start` event of the journal, by agent."""
    start = next(e for e in read_journal(out) if e["event"] == "run-start")
    return {entry["agent"]: entry for entry in start["agents"]}


def frontmatter(path) -> dict[str, object]:
    """The YAML frontmatter of an agent file."""
    return yaml.safe_load(path.read_text().split("---\n", 2)[1])


def tools_of(path) -> list[str]:
    tools = frontmatter(path)["tools"]
    return [name.strip() for name in tools.split(",")] if isinstance(tools, str) else list(tools)


def test_a_server_with_verifier_tools_adds_its_mcp_tools_to_the_sealed_claude_code_verifier_and_not_to_the_specialist(tmp_path, monkeypatch, capsys):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"').replace("\n[[tickets]]", GRAPH + "\n[[tickets]]"))
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    record = sealed_record(out)
    verifier = Path(record["python-verifier"]["seal"]) / "agents" / "python-verifier.md"
    specialist = Path(record["python-specialist"]["seal"]) / "agents" / "python-specialist.md"
    assert tools_of(verifier) == ["Read", "Grep", "Glob", "Bash", "Write", "mcp__graph__explore"]
    assert "tools" not in frontmatter(specialist)
    assert "mcp__" not in specialist.read_text()
    # The source agent file does not change, and the agent check of the verifier passes before its guard probe.
    assert "mcp__" not in (repo / ".claude" / "agents" / "python-verifier.md").read_text()
    probe = next(e for e in read_journal(out) if e["event"] == "guard-probe")
    assert (probe["agent"], probe["result"]) == ("python-verifier", "denied")


def test_a_server_with_verifier_tools_allows_its_mcp_tools_in_the_sealed_opencode_verifier_and_not_in_the_specialist(tmp_path, monkeypatch, capsys):
    opencode_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "opencode"').replace("\n[[tickets]]", GRAPH + "\n[[tickets]]"))
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    record = sealed_record(out)
    verifier = json.loads((Path(record["python-verifier"]["seal"]) / "python-verifier.json").read_text())["agent"]["python-verifier"]
    specialist = (Path(record["python-specialist"]["seal"]) / "python-specialist.json").read_text()
    permission = list(verifier["permission"].items())
    # The OpenCode id of a tool of an MCP server is `<server>_<tool>`, and the last rule of a tool decides.
    assert permission[0] == ("*", "deny")
    assert permission[-1] == ("graph_explore", "allow")
    assert "graph_explore" not in specialist
    assert "graph_explore" not in (repo / ".opencode" / "agents" / "python-verifier.md").read_text()
