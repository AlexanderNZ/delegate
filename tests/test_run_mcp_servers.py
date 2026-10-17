"""`delegate run` gives the MCP servers that a stack names to the agents of that stack, and to no other agent.

Each case writes a workflow into a real temporary git repository and calls
`delegate.main`. The scripted adapter records each request that the engine
gives it. The `claude` process is a system boundary, so a stand-in command
records its arguments.
"""

from __future__ import annotations

import json

import pytest

from delegate import adapters
from delegate.cli import delegate
from delegate.ports.harness import McpServer

from . import claude_fake
from .support import WORKFLOW, ScriptedAdapter, make_repo, run_main

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

    run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    (call,) = fake.calls()
    argv = call["argv"]
    assert "--strict-mcp-config" in argv
    assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {
        "browser": {"command": "npx", "args": ["@playwright/mcp@latest"]},
        "tracker": {"type": "http", "url": "https://mcp.example.invalid/mcp"},
    }}


HOTSPOTS = 'hotspots = ["LICENSE", ".github/workflows/*"]\n'


def dry_run(tmp_path, capsys, servers: str, stack_field: str = "") -> tuple[int, str, str]:
    """`delegate run --dry-run` of a workflow with `servers` before the tickets and `stack_field` in the stack `python`."""
    workflow = WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"').replace(HOTSPOTS, HOTSPOTS + stack_field)
    repo = make_repo(tmp_path, workflow.replace("\n[[tickets]]", servers + "\n[[tickets]]"))
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
    ],
)
def test_a_malformed_mcp_server_exits_1_and_names_the_field(tmp_path, capsys, servers, stack_field, expected):
    code, out, err = dry_run(tmp_path, capsys, servers, stack_field)

    assert code == 1
    assert out == ""
    assert "Traceback" not in err
    for part in expected:
        assert part in err
