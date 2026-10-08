"""The global verifier guard list holds only what every verifier needs.

A build tool or a tracker CLI is one repository's gate, not a read that every
verifier needs. A declaration gives it back with gateCommands, and a command
that reads only while it stays a GET with getOnlyCommands.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent_definitions.declaration import Agent, load_declaration, parse_declaration
from agent_definitions.render import render_claude_code, render_opencode
from agent_definitions.validate import opencode_bash_action, split_frontmatter

SHELLS = ("bash", "/bin/sh")

#: Commands that the global list held before it became neutral. A verifier
#: with no declaration of its own must not run them.
LEFT_THE_GLOBAL_LIST = (
    "nix flake check",
    "nix build .#checks.x.y --no-link",
    "nix eval .#x",
    "nix run .#x",
    "nix shell nixpkgs#hello",
    "nix log /nix/store/x",
    "nix path-info .#x",
    "darwin-rebuild build --flake .",
    "tea issues 1 --repo o/r",
    "tea api /repos/o/r/issues/1",
)

#: The kit's worked example. It holds neutral gate values.
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "java-spring.toml"


def _frontmatter(text: str) -> dict:
    fm, _, err = split_frontmatter(text)
    assert err is None, err
    return fm


def _hook(agent: Agent, tiers) -> str:
    files = render_claude_code(agent, tiers)
    name = f"{agent.key}-verifier.md" if agent.pair else f"{agent.key}.md"
    return _frontmatter(files[name])["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


def _opencode_permission(agent: Agent, tiers) -> dict:
    files = render_opencode(agent, tiers)
    name = f"{agent.key}-verifier.md" if agent.pair else f"{agent.key}.md"
    return _frontmatter(files[name])["permission"]


def _run(hook: str, command: str, shell: str = "bash") -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_verifier_with_no_gate_commands_denies_nix_darwin_rebuild_and_tea(tiers, shell):
    hook = _hook(Agent(key="web", description="A web app."), tiers)
    for command in LEFT_THE_GLOBAL_LIST:
        r = _run(hook, command, shell)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
        assert command in r.stderr, (shell, command, r.stderr)


def test_an_opencode_verifier_with_no_gate_commands_denies_nix_darwin_rebuild_and_tea(tiers):
    perm = _opencode_permission(Agent(key="web", description="A web app."), tiers)
    for command in LEFT_THE_GLOBAL_LIST:
        assert opencode_bash_action(perm, command) == "deny", command


# --- a GET-only rule that a declaration names ----------------------------------


@pytest.fixture
def api_client():
    [agent] = parse_declaration(
        {"agents": {"web": {"description": "A web app.", "getOnlyCommands": ["./scripts/api"]}}}
    )
    return agent


@pytest.mark.parametrize("shell", SHELLS)
def test_a_declared_get_only_command_runs_as_a_get_and_never_with_dash_x(api_client, tiers, shell):
    hook = _hook(api_client, tiers)
    for command in ("./scripts/api /repos/o/r/issues/1", "./scripts/api", "git status && ./scripts/api /repos/o/r"):
        r = _run(hook, command, shell)
        assert r.returncode == 0, (shell, command, r.stdout, r.stderr)
    for command in ("./scripts/api -X POST /repos/o/r/issues", "./scripts/api /repos/o/r -X PATCH", "./scripts/apix /x"):
        r = _run(hook, command, shell)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
    r = _run(hook, "./scripts/api -X DELETE /repos/o/r", shell)
    assert "-X makes it a write" in r.stderr, (shell, r.stderr)


def test_a_declared_get_only_command_stays_out_of_the_opencode_map(api_client, tiers):
    # An OpenCode pattern cannot see "-X" in the arguments, so the command is
    # not permitted there at all.
    perm = _opencode_permission(api_client, tiers)
    assert opencode_bash_action(perm, "./scripts/api /repos/o/r") == "deny"
    assert opencode_bash_action(perm, "./scripts/api -X POST /repos/o/r") == "deny"


@pytest.mark.parametrize("bad", ["./scripts/api; rm -rf /x", "./scripts/'api'", "./scripts/api | tee /x", ""])
def test_a_get_only_command_with_shell_syntax_is_refused(bad):
    with pytest.raises(ValueError, match="getOnlyCommands"):
        parse_declaration({"agents": {"web": {"description": "d", "getOnlyCommands": [bad]}}})


@pytest.mark.parametrize("bad", ["git", "git commit", "rm", "cp", "darwin-rebuild switch"])
def test_a_get_only_command_that_opens_a_write_is_refused(bad):
    with pytest.raises(ValueError, match="getOnlyCommands entry .* opens a write"):
        parse_declaration({"agents": {"web": {"description": "d", "getOnlyCommands": [bad]}}})


# --- the worked example ----------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_worked_example_shows_both_fields_with_neutral_values(tiers, shell):
    [agent] = load_declaration(EXAMPLE)
    hook = _hook(agent, tiers)
    for command in ("./gradlew test", "./gradlew check --info", "./scripts/api /health", "git diff"):
        assert _run(hook, command, shell).returncode == 0, (shell, command)
    for command in ("./scripts/api -X POST /x", "./gradlew bootRun", *LEFT_THE_GLOBAL_LIST):
        assert _run(hook, command, shell).returncode == 2, (shell, command)
    perm = _opencode_permission(agent, tiers)
    assert opencode_bash_action(perm, "./gradlew test") == "allow"
    assert opencode_bash_action(perm, "./scripts/api /health") == "deny"


@pytest.mark.parametrize("shell", SHELLS)
def test_dash_x_on_a_get_only_command_is_denied_in_every_verifier(api_client, tiers, shell):
    [example] = load_declaration(EXAMPLE)
    hooks = [_hook(Agent(key="web", description="A web app."), tiers), _hook(api_client, tiers), _hook(example, tiers)]
    for hook in hooks:
        for command in ("./scripts/api -X POST /repos/o/r/issues", "./scripts/api /repos/o/r/issues/1 -X PATCH"):
            r = _run(hook, command, shell)
            assert r.returncode == 2, (shell, command, r.stdout, r.stderr)


# --- a GET-only command denies every method flag, body flag and output flag ----

#: Each spelling that can make a GET-only command write, or that writes a file.
METHOD_AND_BODY_SPELLINGS = (
    "-X POST",
    "-XPOST",
    "--method POST",
    "--method=POST",
    "--request POST",
    "--request=POST",
    "-d @/tmp/b",
    "-d title=x",
    "-dtitle=x",
    "--data title=x",
    "--data=title=x",
    "--data-raw title=x",
    "--data-binary @/tmp/b",
    "--data-urlencode title=x",
    "--input /tmp/b",
    "--input=/tmp/b",
    "-f title=x",
    "-F title=x",
    "--field title=x",
    "--field=title=x",
    "--raw-field title=x",
    "--raw-field=title=x",
)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("spelling", METHOD_AND_BODY_SPELLINGS)
def test_a_get_only_command_with_a_method_or_body_flag_is_denied(api_client, tiers, shell, spelling):
    hook = _hook(api_client, tiers)
    for command in (f"./scripts/api {spelling} /repos/o/r", f"./scripts/api /repos/o/r {spelling}"):
        r = _run(hook, command, shell)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
        assert "makes it a write" in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "spelling",
    ["'-X' POST", '"--method" POST', "'--method=POST'", "\\-X POST", "-H x -XPOST", "-sX POST"],
)
def test_a_quoted_or_clustered_method_flag_is_denied(api_client, tiers, shell, spelling):
    r = _run(_hook(api_client, tiers), f"./scripts/api {spelling} /repos/o/r", shell)
    assert r.returncode == 2, (shell, spelling, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_get_only_command_with_read_only_arguments_is_permitted(api_client, tiers, shell):
    hook = _hook(api_client, tiers)
    for command in (
        "./scripts/api",
        "./scripts/api /repos/o/r/issues/1",
        "./scripts/api --paginate /repos/o/r/issues",
        "./scripts/api -H Accept:application/json /repos/o/r",
        "./scripts/api --jq .title /repos/o/r/issues/1",
        "./scripts/api /repos/o/r/issues?state=open&per_page=5",
        "./scripts/api -o /tmp/out.json /repos/o/r",
        "./scripts/api -o/tmp/out.json /repos/o/r",
        "./scripts/api --output /tmp/out.json /repos/o/r",
        "./scripts/api --output=/tmp/out.json /repos/o/r",
        "./scripts/api -o $TMPDIR/out.json /repos/o/r",
    ):
        r = _run(hook, command, shell)
        assert r.returncode == 0, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_get_only_command_with_an_output_path_outside_a_temp_directory_is_denied(api_client, tiers, shell):
    hook = _hook(api_client, tiers)
    for command in (
        "./scripts/api -o /Users/x/out.json /repos/o/r",
        "./scripts/api -o out.json /repos/o/r",
        "./scripts/api -o/Users/x/out.json /repos/o/r",
        "./scripts/api --output ./out.json /repos/o/r",
        "./scripts/api --output=/etc/out.json /repos/o/r",
        "./scripts/api -o /tmp/../etc/out.json /repos/o/r",
        "./scripts/api /repos/o/r -o",
        "./scripts/api /repos/o/r --output",
        "./scripts/api -so /tmp/out.json /repos/o/r",
        "./scripts/api >/Users/x/out.json /repos/o/r",
    ):
        r = _run(hook, command, shell)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_flag_rule_holds_in_every_verifier_that_names_a_get_only_command(api_client, tiers, shell):
    [example] = load_declaration(EXAMPLE)
    for hook in (_hook(api_client, tiers), _hook(example, tiers)):
        assert _run(hook, "./scripts/api --method POST /x", shell).returncode == 2
        assert _run(hook, "./scripts/api -f title=x /x", shell).returncode == 2
        assert _run(hook, "./scripts/api /x", shell).returncode == 0
        # A command that is not GET-only keeps its own rules.
        assert _run(hook, "git diff --stat", shell).returncode == 0


def test_the_opencode_verifier_still_allows_no_get_only_command_whatever_the_flag(api_client, tiers):
    perm = _opencode_permission(api_client, tiers)
    for spelling in ("", "--method POST", "-f title=x", "-o /tmp/out"):
        assert opencode_bash_action(perm, f"./scripts/api {spelling} /x") == "deny", spelling
