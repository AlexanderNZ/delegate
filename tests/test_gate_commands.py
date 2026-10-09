"""Behaviour tests for per-repository gate commands and the direnv wrapper.

A repository declares the commands its verifier must run to check a change
(its gates, from its docs/agents/delegation.md). The verifier guard permits
those commands in that repository's verifier only. A gate may also arrive in
a wrapper that loads the repository's environment: "nix develop [ref] -c"
or "direnv exec <dir>". ADR 0004 records the decision.
"""

import json
import shutil
import subprocess
import sys

import pytest
import yaml

from delegate.declaration import Agent, parse_declaration
from delegate.render import VERIFIER_BASH_HOOK, render_claude_code, render_opencode
from delegate.validate import opencode_bash_action, split_frontmatter

SHELLS = ("bash", "/bin/sh")
GATES = ["npm test", "npx vitest", "dotnet test"]


def _guard_of(files: dict[str, str], name: str) -> str:
    fm, _, err = split_frontmatter(files[name])
    assert err is None, err
    return fm["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def _run(hook: str, command: str, shell: str = "bash") -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True)


@pytest.fixture
def web(tiers):
    agent = Agent(key="web", description="A web app.", gate_commands=list(GATES))
    return render_claude_code(agent, tiers), render_opencode(agent, tiers)


# --- the declaration ---------------------------------------------------------


def test_a_declaration_carries_its_gate_commands():
    [agent] = parse_declaration({"agents": {"web": {"description": "d", "gateCommands": ["npm test", "go test"]}}})
    assert agent.gate_commands == ["npm test", "go test"]
    [plain] = parse_declaration({"agents": {"web": {"description": "d"}}})
    assert plain.gate_commands == []


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "npm test; rm -rf /", "npm test && git push", "npm $(x)", 'npm "test"', "npm 'test'",
     "npm test | tee /x", "npm test > /x", "cat `x`", "npm\ttest", "npm test\\"],
)
def test_a_gate_command_with_shell_syntax_is_refused(bad):
    with pytest.raises(ValueError, match="gateCommands"):
        parse_declaration({"agents": {"web": {"description": "d", "gateCommands": [bad]}}})


@pytest.mark.parametrize(
    "bad",
    ["rm", "rm -rf", "cp", "mv", "tee", "cd", "git", "git push", "git commit", "darwin-rebuild switch",
     "nixos-rebuild", "nixos-rebuild switch", "direnv", "direnv allow", "git add", "git reset", "git stash", "sudo"],
)
def test_a_gate_command_that_opens_a_write_is_refused(bad):
    with pytest.raises(ValueError, match="opens a write"):
        parse_declaration({"agents": {"web": {"description": "d", "gateCommands": [bad]}}})


# --- the Claude Code guard ----------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_verifier_runs_its_repository_gates(web, shell):
    hook = _guard_of(web[0], "web-verifier.md")
    for command in (
        "npm test",
        "npm test -- --run",
        "npx vitest run src/cart.test.ts",
        "dotnet test sim.tests",
        "nix develop -c npm test",
        "direnv exec . npx vitest run",
        "cp -R /repo /tmp/proof && cd /tmp/proof && npm test",
    ):
        r = _run(hook, command, shell)
        assert r.returncode == 0, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_gate_command_opens_that_command_and_no_other(web, shell):
    hook = _guard_of(web[0], "web-verifier.md")
    for command in ("npm install left-pad", "npm", "npx rimraf /Users/x", "npm testx", "dotnet run", "npm test; git push"):
        r = _run(hook, command, shell)
        assert r.returncode == 2, (shell, command, r.stdout)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_verifier_without_gate_commands_keeps_the_plain_guard(tiers, shell):
    files = render_claude_code(Agent(key="docs", description="Docs."), tiers)
    hook = _guard_of(files, "docs-verifier.md")
    assert hook == VERIFIER_BASH_HOOK
    assert _run(hook, "npm test", shell).returncode == 2


def test_the_specialist_guard_does_not_change(web, tiers):
    plain = render_claude_code(Agent(key="web", description="A web app."), tiers)
    assert _guard_of(web[0], "web-specialist.md") == _guard_of(plain, "web-specialist.md")


# --- the direnv wrapper (every verifier) ---------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_direnv_exec_reaches_the_read_list_and_no_further(shell):
    for command in ("direnv exec . python3 scripts/test_x.py", "direnv exec /tmp/proof git status"):
        r = _run(VERIFIER_BASH_HOOK, command, shell)
        assert r.returncode == 0, (shell, command, r.stderr)
    for command in ("direnv exec . git push", "direnv exec . rm -rf /Users/x", "direnv exec .", "direnv allow", "direnv"):
        r = _run(VERIFIER_BASH_HOOK, command, shell)
        assert r.returncode == 2, (shell, command, r.stdout)


# --- the OpenCode map ----------------------------------------------------------


def test_the_opencode_verifier_map_allows_the_gate_commands(web):
    fm, _, err = split_frontmatter(web[1]["web-verifier.md"])
    assert err is None, err
    perm = fm["permission"]
    bash = perm["bash"]
    assert list(bash)[0] == "*" and bash["*"] == "deny"
    assert opencode_bash_action(perm, "npm test") == "allow"
    assert opencode_bash_action(perm, "npm test -- --run") == "allow"
    assert opencode_bash_action(perm, "npx vitest run") == "allow"
    assert opencode_bash_action(perm, "npm install left-pad") == "deny"
    assert opencode_bash_action(perm, "npx rimraf /x") == "deny"
