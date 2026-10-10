import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import yaml

from delegate.definitions.declaration import Agent, load_declaration
from delegate.definitions.render import (
    GIT_PUSH_HOOK,
    VERIFIER_BASH_HOOK,
    VERIFIER_GUARD_MARKER,
    VERIFIER_READ_COMMANDS,
    VERIFIER_TEMP_DEST_COMMANDS,
    VERIFIER_TEMP_WRITE_COMMANDS,
    render_claude_code,
    verifier_bash_permission,
)
from delegate.shared.tiers import load_tiers
from delegate.definitions.validate import opencode_bash_action, split_frontmatter


def _fm(path):
    fm, body, err = split_frontmatter(path.read_text())
    assert err is None, err
    return fm, body


def test_example_renders_two_files_per_harness(rendered):
    cc = sorted(p.name for p in (rendered / "claude-code").glob("*.md"))
    oc = sorted(p.name for p in (rendered / "opencode").glob("*.md"))
    assert cc == ["java-spring-specialist.md", "java-spring-verifier.md"]
    assert oc == ["java-spring-specialist.md", "java-spring-verifier.md"]


def test_every_rendered_file_parses_in_its_format(rendered):
    for p in rendered.rglob("*.md"):
        fm, body = _fm(p)
        assert fm.get("description")
        assert body.strip()


def test_claude_code_specialist_frontmatter(rendered):
    fm, body = _fm(rendered / "claude-code" / "java-spring-specialist.md")
    assert fm["name"] == "java-spring-specialist"
    assert "model" not in fm, "model is chosen at spawn; effort follows it (ADR 0001)"
    assert "effort" not in fm
    assert fm["skills"] == ["clean-ddd-hexagonal", "api-design-principles", "tdd", "test-quality", "oauth2-resource-server"]
    assert fm["maxTurns"] == 60
    assert fm["isolation"] == "worktree"
    hook = fm["hooks"]["PreToolUse"][0]
    assert hook["matcher"] == "Bash"
    assert hook["hooks"][0]["type"] == "command"
    assert "tools" not in fm, "the specialist inherits the full tool set"
    assert "java-spring-verifier" in body


def test_claude_code_verifier_is_read_only_and_shares_skills(rendered):
    spec, _ = _fm(rendered / "claude-code" / "java-spring-specialist.md")
    ver, body = _fm(rendered / "claude-code" / "java-spring-verifier.md")
    assert ver["name"] == "java-spring-verifier"
    assert [t.strip() for t in ver["tools"].split(",")] == ["Read", "Grep", "Glob", "Bash"]
    assert ver["skills"] == spec["skills"]
    assert "isolation" not in ver, "a verifier writes nothing, so it needs no worktree"
    assert "blind" in body


def test_claude_code_verifier_bash_carries_the_guard_hook(rendered):
    # The verifier must be able to run the gates. Bash without the guard
    # gives it a full shell, so the hook and the tool arrive together.
    ver, _ = _fm(rendered / "claude-code" / "java-spring-verifier.md")
    entry = ver["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == "Bash"
    assert entry["hooks"][0]["type"] == "command"
    command = entry["hooks"][0]["command"]
    assert VERIFIER_GUARD_MARKER in command
    assert VERIFIER_GUARD_MARKER in VERIFIER_BASH_HOOK
    # The example declares gate commands, so its guard permits them and still
    # denies a write. The global list holds no build tool (ADR 0004).
    assert _run_guard("./gradlew test --info", hook=command).returncode == 0
    assert _run_guard("nix flake check", hook=command).returncode == 2
    assert _run_guard("git commit -m x", hook=command).returncode == 2


def test_opencode_files_carry_tier_model_and_permissions(rendered, tiers):
    spec, sbody = _fm(rendered / "opencode" / "java-spring-specialist.md")
    ver, vbody = _fm(rendered / "opencode" / "java-spring-verifier.md")
    assert spec["mode"] == "subagent" and ver["mode"] == "subagent"
    assert spec["model"] == tiers.model_for("standard", "opencode")
    assert ver["model"] == tiers.model_for("verifier", "opencode")
    assert spec["steps"] == 60
    assert spec["permission"]["bash"]["git push*"] == "deny"
    first = next(iter(ver["permission"].items()))
    assert first == ("*", "deny"), "catch-all deny must be the first rule"
    assert ver["permission"]["read"] == "allow"
    assert "edit" not in ver["permission"]
    bash_map = ver["permission"]["bash"]
    assert next(iter(bash_map.items())) == ("*", "deny"), "the bash map needs its own catch-all deny first"
    assert "skill tool" in sbody and "skill tool" in vbody, "OpenCode cannot preload, so the body says to load them"


def test_opencode_star_key_survives_yaml_roundtrip(rendered):
    text = (rendered / "opencode" / "java-spring-verifier.md").read_text()
    raw = text.split("---")[1]
    assert yaml.safe_load(raw)["permission"]["*"] == "deny"


def test_effort_is_written_only_when_declared(java_spring, tiers):
    from delegate.definitions.render import render

    (agent,) = java_spring
    agent.effort = "xhigh"
    out = render([agent], tiers)
    cc = split_frontmatter(out["claude-code"]["java-spring-specialist.md"])[0]
    oc = split_frontmatter(out["opencode"]["java-spring-specialist.md"])[0]
    assert cc["effort"] == "xhigh"
    assert "variant" not in oc, "xhigh is not an OpenCode Anthropic variant"
    agent.effort = "max"
    oc = split_frontmatter(render([agent], tiers)["opencode"]["java-spring-specialist.md"])[0]
    assert oc["variant"] == "max"


def test_single_read_only_agent_renders_without_twin(tiers):
    from delegate.definitions.declaration import parse_declaration
    from delegate.definitions.render import render

    agents = parse_declaration({"agents": {"docs-verifier": {"description": "Docs and config review.", "pair": False, "readOnly": True}}})
    out = render(agents, tiers)
    assert list(out["claude-code"]) == ["docs-verifier.md"]
    fm = split_frontmatter(out["claude-code"]["docs-verifier.md"])[0]
    assert fm["tools"] == "Read, Grep, Glob, Bash"
    assert fm["hooks"]["PreToolUse"][0]["matcher"] == "Bash"


def _run_hook(command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(["bash", "-c", GIT_PUSH_HOOK], input=payload, capture_output=True, text=True)


def test_git_push_hook_blocks_push_and_allows_other_commands():
    for cmd in ("git push", "git push origin main", "git add . && git push --force", "git -C /tmp/x push"):
        r = _run_hook(cmd)
        assert r.returncode == 2, (cmd, r.stdout, r.stderr)
        assert "blocked" in r.stderr
    for cmd in ("git status", "git log --oneline", "echo pushing", "git pull", "nix flake check"):
        r = _run_hook(cmd)
        assert r.returncode == 0, (cmd, r.stdout, r.stderr)


def test_hook_survives_yaml_roundtrip(rendered):
    fm, _ = _fm(rendered / "claude-code" / "java-spring-specialist.md")
    assert fm["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == GIT_PUSH_HOOK


def test_opencode_specialist_catch_all_precedes_every_push_deny(rendered):
    # OpenCode keeps the last matching rule, so the catch-all must be the first
    # key or the denies are shadowed and the specialist can push.
    spec, _ = _fm(rendered / "opencode" / "java-spring-specialist.md")
    assert list(spec["permission"]["bash"].items()) == [
        ("*", "allow"),
        ("git push*", "deny"),
        ("git -C * push*", "deny"),
    ]


def test_opencode_specialist_denies_a_push_from_another_repository_path(rendered):
    # "git push*" compiles to "^git push.*$", so it does not cover
    # "git -C <path> push". The Claude Code hook covers that form, so the
    # OpenCode map carries a second deny for it. The two harnesses are still
    # not equal; test_the_two_harnesses_are_not_equal_on_compound_commands
    # records the difference that remains.
    perm = _fm(rendered / "opencode" / "java-spring-specialist.md")[0]["permission"]
    assert opencode_bash_action(perm, "git push origin main") == "deny"
    assert opencode_bash_action(perm, "git -C /repo/worktree push origin main") == "deny"
    assert opencode_bash_action(perm, "git -C /repo/worktree status") == "allow"
    assert opencode_bash_action(perm, "nix flake check") == "allow"


def test_the_two_harnesses_are_not_equal_on_compound_commands(rendered):
    # Measured 2026-09-19. The Claude Code hook applies an unanchored regular
    # expression to the raw command, so it stops a push inside a compound
    # command, a push with two spaces, and a push behind an environment
    # assignment. An OpenCode pattern is an anchored glob, and OpenCode
    # v1.18.31 passes the raw command to the permission check without a split
    # at "&&" (packages/core/src/tool/bash.ts; a TODO for a tree-sitter parser
    # is present), so all three forms stay allowed. ADR 0002 records the
    # decision that the OpenCode pattern does not widen to "*git push*".
    perm = _fm(rendered / "opencode" / "java-spring-specialist.md")[0]["permission"]
    for cmd in ("git add . && git push", "git  push", "GIT_DIR=/x git push"):
        assert _run_hook(cmd).returncode == 2, cmd
        assert opencode_bash_action(perm, cmd) == "allow", cmd


def test_rendered_verifiers_name_both_modes_and_stop_a_full_diff_reread(rendered):
    # A verifier that keeps no mode re-verifies the whole diff after a
    # rejection. The mode comes from the shape of the brief, so the body must
    # name the heading that selects fix-up mode.
    for harness in ("claude-code", "opencode"):
        body = _fm(rendered / harness / "java-spring-verifier.md")[1].lower()
        assert "fix-up mode" in body, harness
        assert "full mode" in body, harness
        assert "## findings under verification" in body, harness
        assert "do not read the full diff again" in body, harness
        assert "scope finding" in body, harness
        assert "the first line of your report names the mode" in body, harness


def test_rendered_specialists_make_a_fix_up_a_new_commit(rendered):
    # An amend destroys the delta that the scoped verifier reads.
    for harness in ("claude-code", "opencode"):
        body = _fm(rendered / harness / "java-spring-specialist.md")[1].lower()
        assert "new commit on top of the rejected commit" in body, harness
        assert "do not amend" in body, harness


# --- The verifier's guarded Bash ---------------------------------------------------


#: The guard is one bash line, and it runs under the shell the harness gives
#: the hook. Every guard case runs under both shells that SKILL.md records as
#: equal: bash, and macOS /bin/sh.
GUARD_SHELLS = ("bash", "/bin/sh")

#: A repository path outside every temp directory. A verifier copies this tree
#: into a temp directory to prove a check red.
WORKTREE = "/Users/dev/src/project-worktrees/feat-x"


def _gated_hook() -> str:
    """The guard of the worked example, whose declaration names its gates
    (./gradlew test, ./gradlew check) and a GET-only command (./scripts/api).
    The global list holds none of them (ADR 0004)."""
    example = Path(__file__).resolve().parents[1] / "examples" / "java-spring.toml"
    [agent] = load_declaration(example)
    fm, _, err = split_frontmatter(render_claude_code(agent, load_tiers())["java-spring-verifier.md"])
    assert err is None, err
    return fm["hooks"]["PreToolUse"][0]["hooks"][0]["command"]


#: The words that mark a command as one of the example's gates.
_GATED_WORDS = ("./gradlew", "./scripts/api")

GATED_HOOK = _gated_hook()


def _hook_for(command: str) -> str:
    """The gated guard for a command that runs a declared gate, else the
    global guard."""
    return GATED_HOOK if any(w in command for w in _GATED_WORDS) else VERIFIER_BASH_HOOK


def _run_guard(
    command: str, env: dict | None = None, shell: str = "bash", hook: str = VERIFIER_BASH_HOOK
) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # macOS /bin/sh and NixOS /bin/sh are both bash run as `sh`. The Linux
        # Nix build sandbox's /bin/sh is busybox instead, which has no `<<<`, so
        # run bash as `sh` here: the same shell the guard meets on a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(
        argv,
        executable=executable,
        input=payload,
        capture_output=True,
        text=True,
        env=env,
    )


GUARD_ALLOWS = [
    "git diff main...HEAD",
    "git log --oneline -5",
    "git branch --show-current",
    "git worktree list",
    "./gradlew test",
    "./gradlew test 2>&1",
    "./gradlew check --info",
    "./gradlew check",
    "ls -la /tmp/x",
    "cat /etc/hosts",
    "rm -rf /tmp/x",
    "mkdir -p /tmp/a/b",
    "cp /tmp/a /tmp/b",
    "echo hi > /tmp/x",
    "git status && ./gradlew test",
    "./scripts/api /repos/o/r/issues",
    "./scripts/api /repos/o/r/issues/1",
    # The red proof. The verifier copies the worktree into a temp
    # directory, changes to it, and runs the gate on the copy.
    f"cp -R {WORKTREE} /tmp/proof",
    f"cp -R {WORKTREE} /tmp/proof && cd /tmp/proof && ./gradlew test",
    "cd /tmp/proof",
    "cd /tmp/proof && ./gradlew test",
    "rm -f /tmp/proof/.git",
    # git -C <path> <read>, so a verifier with no worktree isolation
    # reads a worktree by path.
    f"git -C {WORKTREE} diff main...HEAD",
    f"git -C {WORKTREE} log --oneline -5",
    f"git -C {WORKTREE} status",
    "git -C /tmp/proof rev-parse HEAD",
    # A redirection after the destination keeps the destination,
    # and the redirection target obeys the temp rule on its own.
    f"cp -R {WORKTREE} /tmp/proof 2>&1",
    f"cp -R {WORKTREE} /tmp/proof > /tmp/log",
    f"cp -R {WORKTREE} /tmp/proof 2>/tmp/err",
    # The ".." rule tests a path component, not a text fragment. A file name
    # that holds two dots is not a traversal.
    "cp /tmp/a..b /tmp/c",
    "mkdir /tmp/a..b",
    "cd /tmp/a..b",
]

# (command, the segment the deny message must name)
GUARD_DENIES = [
    ("git commit -m x", "git commit -m x"),
    ("git push", "git push"),
    ("git checkout main", "git checkout main"),
    ("darwin-rebuild switch", "darwin-rebuild switch"),
    ("sudo rm -rf /tmp/x", "sudo rm -rf /tmp/x"),
    ("rm -rf ~/x", "rm -rf ~/x"),
    ("rm -rf /Users/x", "rm -rf /Users/x"),
    ("mkdir /Users/x", "mkdir /Users/x"),
    ("cp /tmp/a /Users/b", "cp /tmp/a /Users/b"),
    ("git status && git commit -m x", "git commit -m x"),
    ("git status; rm -rf /Users/x", "rm -rf /Users/x"),
    ("cat x | tee /etc/y", "tee /etc/y"),
    ("./scripts/api -X PATCH /x", "./scripts/api -X PATCH /x"),
    ("echo hi > /etc/x", "echo hi > /etc/x"),
    ("echo hi >> /Users/dev/.zshrc", "echo hi >> /Users/dev/.zshrc"),
    # The copy allow is one direction only. A copy out of a temp
    # directory, or a copy between two repository paths, stays denied.
    ("cp /tmp/a /Users/x", "cp /tmp/a /Users/x"),
    (f"cp -R {WORKTREE} /Users/dev/copy", f"cp -R {WORKTREE} /Users/dev/copy"),
    (f"cp -R {WORKTREE} /tmp/proof && cp /tmp/proof/x /Users/x", "cp /tmp/proof/x /Users/x"),
    ("cp /tmp/a /Users/x > /tmp/log", "cp /tmp/a /Users/x > /tmp/log"),
    # mv is not cp. It removes the source, so every path stays under the
    # every-path-in-temp rule.
    (f"mv {WORKTREE} /tmp/proof", f"mv {WORKTREE} /tmp/proof"),
    # cd follows the destination rule, and a bare cd names no path.
    ("cd /Users/x", "cd /Users/x"),
    ("cd ~", "cd ~"),
    ("cd", "cd"),
    ("cd /tmp/proof && git commit -m x", "git commit -m x"),
    # git -C reaches the read list only. A write behind it stays denied.
    (f"git -C {WORKTREE} push", f"git -C {WORKTREE} push"),
    (f"git -C {WORKTREE} commit -m x", f"git -C {WORKTREE} commit -m x"),
    (f"git -C {WORKTREE} checkout main", f"git -C {WORKTREE} checkout main"),
    ("git -C /tmp/proof push origin main", "git -C /tmp/proof push origin main"),
    # A path and no subcommand is not a read.
    (f"git -C {WORKTREE}", f"git -C {WORKTREE}"),
    # A redirection before the destination must not
    # hide the destination. The shell removes the redirection and runs
    # "cp /tmp/a /Users/x", so a scan that stops at the redirection reads the
    # source as the destination and permits a write to /Users/x.
    ("cp /tmp/a >/tmp/log /Users/x", "cp /tmp/a >/tmp/log /Users/x"),
    ("cp /tmp/a > /tmp/log /Users/x", "cp /tmp/a > /tmp/log /Users/x"),
    ("cp /tmp/a >>/tmp/log /Users/x", "cp /tmp/a >>/tmp/log /Users/x"),
    ("cp /tmp/a >> /tmp/log /Users/x", "cp /tmp/a >> /tmp/log /Users/x"),
    ("cp /tmp/a 2>/tmp/e /Users/x", "cp /tmp/a 2>/tmp/e /Users/x"),
    ("cp /tmp/a 2> /tmp/e /Users/x", "cp /tmp/a 2> /tmp/e /Users/x"),
    ("cp /tmp/a &>/tmp/log /Users/x", "cp /tmp/a &>/tmp/log /Users/x"),
    ("cp /tmp/a </tmp/in /Users/x", "cp /tmp/a </tmp/in /Users/x"),
    ("cp /tmp/a < /tmp/in /Users/x", "cp /tmp/a < /tmp/in /Users/x"),
    (f"cp -R {WORKTREE} /tmp/ok >/tmp/log /Users/x", f"cp -R {WORKTREE} /tmp/ok >/tmp/log /Users/x"),
    ("cd /tmp/ok >/tmp/log /Users/x", "cd /tmp/ok >/tmp/log /Users/x"),
    # A destination flag moves the destination into a
    # word that starts with a hyphen, which the flag clause used to drop.
    ("cp -t /Users/y /tmp/a", "cp -t /Users/y /tmp/a"),
    ("cp -t/Users/y /tmp/a", "cp -t/Users/y /tmp/a"),
    ("cp --target-directory=/Users/y /tmp/a", "cp --target-directory=/Users/y /tmp/a"),
    ("cp --target-directory /Users/y /tmp/a", "cp --target-directory /Users/y /tmp/a"),
    # A temp target does not save the flag. The guard cannot tell which word
    # the flag consumes, so every form of it is a deny.
    ("cp -t /tmp/y /tmp/a", "cp -t /tmp/y /tmp/a"),
    # istmp tests a text prefix, so a ".." component
    # walks out of the temp directory the prefix promises.
    (f"cp -R {WORKTREE} /tmp/../Users/y", f"cp -R {WORKTREE} /tmp/../Users/y"),
    (f"cp -R {WORKTREE} /private/tmp/../../Users/y", f"cp -R {WORKTREE} /private/tmp/../../Users/y"),
    ("cd /tmp/../Users/x", "cd /tmp/../Users/x"),
    ("mkdir /tmp/../Users/x", "mkdir /tmp/../Users/x"),
    ("rm -rf /tmp/../Users/x", "rm -rf /tmp/../Users/x"),
    ("echo hi > /tmp/../Users/x", "echo hi > /tmp/../Users/x"),
]


@pytest.mark.parametrize("shell", GUARD_SHELLS)
@pytest.mark.parametrize("command", GUARD_ALLOWS)
def test_the_guard_lets_the_verifier_run_a_read_or_gate_command(command, shell):
    # Without this the verifier cannot run the gates, and the coordinator runs
    # them in its place. A gate runs in a verifier that declares it (ADR 0004).
    r = _run_guard(command, shell=shell, hook=_hook_for(command))
    assert r.returncode == 0, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
@pytest.mark.parametrize("command,segment", GUARD_DENIES)
def test_the_guard_stops_a_write_command_and_names_the_segment(command, segment, shell):
    # A deny that does not name the offending segment leaves the agent guessing
    # which half of a compound command it must drop. Declared gates open no
    # write, so the gated guard denies the same commands.
    for hook in (VERIFIER_BASH_HOOK, GATED_HOOK):
        r = _run_guard(command, shell=shell, hook=hook)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
        assert segment in r.stderr, (shell, command, r.stderr)


def test_every_read_command_in_the_table_is_allowed_by_the_rendered_hook():
    # The table is the single source. An entry the generator drops shows here.
    for c in VERIFIER_READ_COMMANDS:
        assert _run_guard(c).returncode == 0, c


def test_a_temp_write_command_needs_a_temp_path():
    for c in VERIFIER_TEMP_WRITE_COMMANDS:
        assert _run_guard(f"{c} /tmp/x").returncode == 0, c
        assert _run_guard(f"{c} /etc/x").returncode == 2, c
        assert _run_guard(f"{c} -f").returncode == 2, c


# --- The copy the red proof needs -------------------------------------------


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_a_temp_destination_command_needs_a_temp_last_path(shell):
    # The table is the single source. A command the generator drops from the
    # destination branch falls to the catch-all deny and shows here.
    for c in VERIFIER_TEMP_DEST_COMMANDS:
        assert _run_guard(f"{c} /tmp/x", shell=shell).returncode == 0, c
        assert _run_guard(f"{c} /etc/x", shell=shell).returncode == 2, c
        assert _run_guard(f"{c} -f", shell=shell).returncode == 2, c


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_the_guard_lets_the_verifier_copy_a_repository_path_into_a_temp_directory(shell):
    # The verifier method copies the worktree into a temp directory,
    # removes the copy's .git, breaks the copy, and proves a check red. The
    # source of that copy is a repository path, and a read of a repository
    # path is a read.
    proof = f"cp -R {WORKTREE} /tmp/proof && rm -f /tmp/proof/.git && cd /tmp/proof && ./gradlew test"
    r = _run_guard(proof, shell=shell, hook=GATED_HOOK)
    assert r.returncode == 0, (shell, r.stdout, r.stderr)
    assert r.stderr == "", (shell, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_the_guard_denies_a_copy_whose_destination_leaves_a_temp_directory(shell):
    # The new allow is one direction. A copy that writes outside a temp
    # directory is the write that the read-only posture forbids.
    for command in (f"cp -R {WORKTREE} /Users/dev/copy", "cp /tmp/a /Users/x"):
        r = _run_guard(command, shell=shell)
        assert r.returncode == 2, (shell, command, r.stdout)
        assert command in r.stderr, (shell, r.stderr)
        assert "outside a temp directory" in r.stderr, (shell, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_the_guard_denies_a_copy_whose_destination_hides_behind_a_redirection(shell):
    # A redirection to a temp path must not become the destination the guard
    # examines. Both orders are here: the redirection after the destination,
    # and the redirection before it. The second order defeated the first fix,
    # because the shell removes the redirection and the true destination then
    # comes after it.
    for command in (
        "cp /tmp/a /Users/x > /tmp/log",
        "cp /tmp/a >/tmp/log /Users/x",
        "cp /tmp/a > /tmp/log /Users/x",
        "cp /tmp/a 2>/tmp/e /Users/x",
        "cp /tmp/a 2> /tmp/e /Users/x",
        "cp /tmp/a >>/tmp/log /Users/x",
        "cp /tmp/a &>/tmp/log /Users/x",
        "cp /tmp/a </tmp/in /Users/x",
        "cp /tmp/a < /tmp/in /Users/x",
    ):
        r = _run_guard(command, shell=shell)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
        assert command in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_a_redirection_after_the_destination_keeps_the_command_permitted(shell):
    # The deny is for a path that comes after a redirection, not for every
    # redirection. A verifier that sends the copy's errors to a temp log still
    # gets its copy.
    for command in (
        f"cp -R {WORKTREE} /tmp/proof 2>&1",
        f"cp -R {WORKTREE} /tmp/proof > /tmp/log",
        f"cp -R {WORKTREE} /tmp/proof 2>/tmp/err",
    ):
        r = _run_guard(command, shell=shell)
        assert r.returncode == 0, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_a_destination_flag_is_denied_in_every_form(shell):
    # GNU cp takes its destination from -t or --target-directory, and the
    # flag clause drops every word that starts with a hyphen. The guard cannot
    # tell which word such a flag consumes, so each form is a deny, a temp
    # target included.
    #
    # A rule on two literal prefixes is not enough. GNU short options cluster,
    # so a "t" at the end of a cluster still takes the next word. A GNU long
    # option accepts an unambiguous abbreviation, and "--targ" is unambiguous.
    # -T and --no-target-directory change what the last path means, so the
    # same rule denies them.
    for command in (
        "cp -t /Users/y /tmp/a",
        "cp -t/Users/y /tmp/a",
        "cp --target-directory=/Users/y /tmp/a",
        "cp --target-directory /Users/y /tmp/a",
        "cp -t /tmp/y /tmp/a",
        # the cluster forms
        "cp -Rt /Users/y /tmp/a",
        "cp -rt /Users/y /tmp/a",
        "cp -vt /Users/y /tmp/a",
        "cp -at /Users/y /tmp/a",
        "cp -Rt /tmp/y /tmp/a",
        # the abbreviated long options
        "cp --targ=/Users/y /tmp/a",
        "cp --target=/Users/y /tmp/a",
        "cp --targe /Users/y /tmp/a",
        # the no-target-directory pair
        "cp -T /Users/x /tmp/a",
        "cp --no-target-directory /Users/x /tmp/a",
    ):
        r = _run_guard(command, shell=shell)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
        assert command in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_a_flag_that_cannot_move_the_destination_stays_permitted(shell):
    # The destination-flag rule must not take the copy away. These are the
    # flags a red proof uses, and no one of them holds a "t" or a "T".
    for command in (
        f"cp -R {WORKTREE} /tmp/proof",
        f"cp -a {WORKTREE} /tmp/proof",
        f"cp -Rp {WORKTREE} /tmp/proof",
        "cp -p /tmp/a /tmp/b",
    ):
        r = _run_guard(command, shell=shell)
        assert r.returncode == 0, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_a_dot_dot_component_is_not_a_temp_path(shell):
    # istmp tests a text prefix. Without this rule "/tmp/../Users/x" carries a
    # temp prefix and walks out of the temp directory. The guard rejects the
    # ".." component; it does not resolve the path.
    for command in (
        f"cp -R {WORKTREE} /tmp/../Users/y",
        f"cp -R {WORKTREE} /private/tmp/../../Users/y",
        "cd /tmp/../Users/x",
        "mkdir /tmp/../Users/x",
        "rm -rf /tmp/../Users/x",
        "touch /tmp/../Users/x",
        "echo hi > /tmp/../Users/x",
    ):
        r = _run_guard(command, shell=shell)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
    # The rule reads a path component, so a name that holds two dots is fine.
    for command in ("cp /tmp/a..b /tmp/c", "mkdir /tmp/a..b", "cd /tmp/a..b"):
        assert _run_guard(command, shell=shell).returncode == 0, (shell, command)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_cd_reaches_a_temp_directory_and_nothing_else(shell):
    # darwin-rebuild build runs from a temp working directory, so the
    # verifier needs cd. Every other destination stays denied.
    assert _run_guard("cd /tmp/proof", shell=shell).returncode == 0
    for command in ("cd /Users/x", f"cd {WORKTREE}", "cd ~", "cd"):
        r = _run_guard(command, shell=shell)
        assert r.returncode == 2, (shell, command, r.stdout)
        assert command in r.stderr, (shell, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_git_dash_c_reaches_the_read_list_and_no_further(shell):
    # A verifier with no worktree isolation reads a worktree by path.
    # The path is not examined, because a read of any path is a read. The
    # subcommand after the path is examined, and a write stays denied.
    for read in ("diff main...HEAD", "log --oneline -5", "status", "show HEAD", "rev-parse HEAD"):
        r = _run_guard(f"git -C {WORKTREE} {read}", shell=shell)
        assert r.returncode == 0, (shell, read, r.stdout, r.stderr)
    for write in ("push", "commit -m x", "checkout main", "worktree add /tmp/x main"):
        command = f"git -C {WORKTREE} {write}"
        r = _run_guard(command, shell=shell)
        assert r.returncode == 2, (shell, command, r.stdout)
        assert command in r.stderr, (shell, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_git_dash_c_does_not_open_a_path_shaped_hole(shell):
    # The guard removes "-C <path>" and tests the rest against the read list.
    # A segment with no subcommand after the path is not a read.
    for command in (f"git -C {WORKTREE}", "git -C", "git -Cpush"):
        assert _run_guard(command, shell=shell).returncode == 2, (shell, command)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_nix_develop_dash_c_reaches_the_read_list_and_no_further(shell):
    # A repository with a flake dev shell runs its gates as
    # "nix develop -c <command>". The guard removes "nix develop [flake-ref] -c"
    # and tests the rest against the same tables, so a write stays denied.
    for command in (
        "nix develop -c python3 scripts/test_lib.py",
        "nix develop --command python3 -m pytest -q",
        "nix develop .#ci -c git status",
        "nix develop path:. -c ./gradlew test",
        "nix develop -c cp /tmp/a /tmp/b",
        "nix develop -c mkdir /tmp/x",
    ):
        r = _run_guard(command, shell=shell, hook=_hook_for(command))
        assert r.returncode == 0, (shell, command, r.stdout, r.stderr)
    for command in (
        "nix develop -c git push",
        "nix develop .#ci -c rm -rf /Users/x",
        "nix develop -c cp /tmp/a /Users/x",
        "nix develop -c curl https://example.com",
    ):
        r = _run_guard(command, shell=shell)
        assert r.returncode == 2, (shell, command, r.stdout)
        assert command in r.stderr, (shell, r.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_nix_develop_opens_no_shell_and_takes_no_flag(shell):
    # Without -c, nix develop starts an interactive shell. A flag before -c can
    # write: --profile writes a symlink, and --build runs build phases in the
    # working directory. Only a flake reference may come before -c.
    for command in (
        "nix develop",
        "nix develop .#ci",
        "nix develop -c",
        "nix develop --profile /Users/x/p -c ls",
        "nix develop --build -c ls",
        "nix develop .#ci --impure -c ls",
        "nix developer -c ls",
    ):
        assert _run_guard(command, shell=shell).returncode == 2, (shell, command)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_a_tab_does_not_hide_a_word_from_a_prefix_strip(shell):
    # The prefix strips cut at a space. A tab between words must not hide a
    # flag before -c, or a write after git -C <path>. A review found this gap.
    for command in (
        "nix develop x\t--profile\t/home/dev/p -c ls",
        "git -C /tmp/x\tpush diff",
        "direnv exec .\tgit push",
    ):
        assert _run_guard(command, shell=shell).returncode == 2, (shell, command)


def test_the_guard_denies_an_empty_command():
    r = _run_guard("")
    assert r.returncode == 2
    assert r.stderr.strip()


def test_the_guard_fails_closed_when_jq_is_absent():
    # jq off PATH must deny. The specialist push hook fails open in that state,
    # which is a hole this guard must not copy.
    r = _run_guard("git diff", env={"PATH": "/nonexistent"})
    assert r.returncode == 2, (r.stdout, r.stderr)


def test_python3_and_bash_c_are_a_known_hole_in_the_guard():
    # The guard does not parse Python or a nested shell. Both are on the allow
    # list because the gates need them. SKILL.md records the hole.
    q = chr(34)
    hole = "python3 -c " + q + "open('/Users/x','w')" + q
    assert _run_guard(hole).returncode == 0
    assert _run_guard('bash -c "rm -rf /Users/x"').returncode == 0


def test_the_verifier_guard_survives_the_yaml_round_trip(tiers):
    files = render_claude_code(Agent(key="docs", description="Docs."), tiers)
    fm, _, err = split_frontmatter(files["docs-verifier.md"])
    assert err is None, err
    assert fm["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == VERIFIER_BASH_HOOK


def test_opencode_verifier_bash_map_allows_the_read_list_and_denies_writes(rendered):
    perm = _fm(rendered / "opencode" / "java-spring-verifier.md")[0]["permission"]
    assert opencode_bash_action(perm, "git diff main...HEAD") == "allow"
    assert opencode_bash_action(perm, "git diff") == "allow"
    assert opencode_bash_action(perm, "./gradlew test") == "allow"
    assert opencode_bash_action(perm, "nix flake check") == "deny"
    assert opencode_bash_action(perm, "ls -la") == "allow"
    assert opencode_bash_action(perm, "git push") == "deny"
    assert opencode_bash_action(perm, "git commit -m x") == "deny"
    assert opencode_bash_action(perm, "rm -rf /tmp/x") == "deny"
    assert opencode_bash_action(perm, "darwin-rebuild switch") == "deny"
    assert opencode_bash_action(perm, "lsof -i") == "deny", "a bare 'ls*' pattern would catch lsof"


def test_opencode_verifier_bash_map_comes_from_the_same_table(rendered, java_spring):
    perm = _fm(rendered / "opencode" / "java-spring-verifier.md")[0]["permission"]
    (agent,) = java_spring
    assert perm["bash"] == verifier_bash_permission(tuple(agent.gate_commands))
    for c in VERIFIER_READ_COMMANDS:
        assert opencode_bash_action(perm, c) == "allow", c


def test_opencode_does_not_split_a_compound_command(rendered):
    # Measured on OpenCode v1.18.31 (ADR 0002): OpenCode hands the permission check
    # the raw command. The Claude Code guard splits at "&&"; OpenCode does not,
    # so a write hidden behind an allowed head stays allowed there.
    perm = _fm(rendered / "opencode" / "java-spring-verifier.md")[0]["permission"]
    assert _run_guard("git status && git commit -m x").returncode == 2
    assert opencode_bash_action(perm, "git status && git commit -m x") == "allow"


def test_rendered_verifiers_tell_the_agent_what_its_shell_permits(rendered):
    # A verifier that does not know it has a shell keeps asking the coordinator
    # to run the gates, and the coordinator then does the verifier's work.
    cc = _fm(rendered / "claude-code" / "java-spring-verifier.md")[1].lower()
    assert "you have bash" in cc
    assert "temp directory" in cc
    assert "run the verification gates yourself" in cc
    # A verifier that does not know it may copy a tree into a temp
    # directory asks the coordinator for the red proof, or tries to go around
    # the guard.
    assert "copy a tree into a temp directory" in cc
    assert "`cd`" in cc
    assert "git -c <path>" in cc
    oc = _fm(rendered / "opencode" / "java-spring-verifier.md")[1].lower()
    assert "permission.bash" in oc
    assert "does not divide a command at" in oc
