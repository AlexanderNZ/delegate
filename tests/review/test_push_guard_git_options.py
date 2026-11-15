"""The push guard of a rendered specialist refuses a push after the global options of git, and a git command that
turns off the `pre-push` hook (#359).

A `-c` on the command line wins over each configuration file, so `git -c core.hooksPath=/dev/null push` runs no
`pre-push` hook. The text guard of each harness is the layer that sees the command. Each case renders the example
declaration with `agent-definitions render`, and runs the guard of the rendered file: the Claude Code hook as a bash
process, and the OpenCode bash map through the model of the OpenCode rules in the validator.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from delegate.adapters.git import GitVersionControl
from delegate.cli.agent_definitions import main
from delegate.definitions.validate import opencode_bash_action

from ..support import git, make_repo

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "java-spring.toml"

#: A push after a run of global options of git. Each form passed the guard of each harness before #359.
PUSH_AFTER_OPTIONS = (
    "git -c x=y push",
    "git -c x=y push origin main",
    "git --no-pager push",
    "git -C 'a b' push",
    'git -C "a b" push origin main',
    "git -C a -c x=y push",
    "git -C /repo/worktree --no-pager -c user.name=x push --force",
)

#: A git command that sets `core.hooksPath` on its command line or with `--config-env`, so git runs no `pre-push` hook.
HOOKS_OFF = (
    "git -c core.hooksPath=/dev/null push origin feature",
    "git -c core.hookspath=/dev/null push origin feature",
    "git -C /repo/worktree -c core.hooksPath=/dev/null push",
    "git -c 'core.hooksPath=/dev/null' status",
    "git --config-env=core.hooksPath=HOOKS push",
    "git --config-env=core.hookspath=HOOKS status",
)

#: Only the Claude Code hook reads the command as words. An OpenCode pattern is a glob that is case-sensitive.
CLAUDE_CODE_ONLY = (
    "git --git-dir /repo/.git push",
    "git --git-dir=/repo/.git push",
    "git 'push' origin main",
    "git -c CORE.HOOKSPATH=/dev/null status",
    "git --config-env core.hooksPath=HOOKS status",
    "git status && git -c x=y push",
    "git -C a \\\n  push",
)

#: Read commands that name a push as an argument. The OpenCode glob `git -* push*` denies them; the hook reads the words.
CLAUDE_CODE_PASS = ("git --no-pager log --grep push", "git -C /repo/worktree log --grep push")

#: The read commands of git, and commands that only name a push or the hooks setting.
STILL_PASS = (
    "git -C /repo/worktree log",
    "git -C /repo/worktree log --oneline -5",
    "git -c color.ui=never diff",
    "git -c color.ui=never diff HEAD~1 -- src",
    "git status",
    "git config --get core.hooksPath",
    'git commit -m "Refuse a push after the options of git"',
    "grep -rn hooksPath src",
)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("rendered")
    assert main(["render", str(EXAMPLE), "-o", str(out)]) == 0
    return out


def frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])


@pytest.fixture(scope="module")
def claude_code_hook(rendered) -> str:
    fm = frontmatter(rendered / "claude-code" / "java-spring-specialist.md")
    [entry] = [e for e in fm["hooks"]["PreToolUse"] if e["matcher"] == "Bash"]
    return entry["hooks"][0]["command"]


@pytest.fixture(scope="module")
def opencode_permission(rendered) -> dict:
    return frontmatter(rendered / "opencode" / "java-spring-specialist.md")["permission"]


def run_hook(hook: str, command: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(["bash", "-c", hook], input=payload, capture_output=True, text=True, cwd=cwd)


@pytest.mark.parametrize("command", PUSH_AFTER_OPTIONS)
def test_the_claude_code_guard_refuses_a_push_after_global_options(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "git push is blocked for specialist agents" in r.stderr


@pytest.mark.parametrize("command", PUSH_AFTER_OPTIONS)
def test_the_opencode_guard_refuses_a_push_after_global_options(opencode_permission, command):
    assert opencode_bash_action(opencode_permission, command) == "deny"


@pytest.mark.parametrize("command", HOOKS_OFF)
def test_the_claude_code_guard_refuses_a_git_command_that_turns_off_the_hooks(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "core.hooksPath" in r.stderr and "--config-env" in r.stderr


@pytest.mark.parametrize("command", HOOKS_OFF)
def test_the_opencode_guard_refuses_a_git_command_that_turns_off_the_hooks(opencode_permission, command):
    assert opencode_bash_action(opencode_permission, command) == "deny"


@pytest.mark.parametrize("command", CLAUDE_CODE_ONLY)
def test_the_claude_code_guard_reads_the_words_of_the_command(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "blocked for specialist agents" in r.stderr


@pytest.mark.parametrize("command", STILL_PASS)
def test_the_read_commands_of_git_still_pass_the_guard_of_each_harness(claude_code_hook, opencode_permission, command):
    r = run_hook(claude_code_hook, command)

    assert (r.returncode, r.stderr) == (0, "")
    assert opencode_bash_action(opencode_permission, command) == "allow"


@pytest.mark.parametrize("command", CLAUDE_CODE_PASS)
def test_the_claude_code_guard_passes_a_read_command_that_names_a_push(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert (r.returncode, r.stderr) == (0, "")


def test_a_push_with_the_hooks_off_from_a_real_worktree_is_refused_by_the_rendered_guard(
    tmp_path, git_identity, claude_code_hook
):
    """The push that the review made: without the text guard it reaches the bare remote; with the guard it does not."""
    vcs = GitVersionControl()
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    hooks = tmp_path / "guard-hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\necho 'delegate: push refused' >&2\nexit 1\n")
    (hooks / "pre-push").chmod(0o755)
    vcs.use_hooks_directory(repo, worktree, hooks)
    environment = {**os.environ, **vcs.worktree_environment(worktree)}

    def shell(command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-c", command], cwd=worktree, capture_output=True, text=True, env=environment)

    def remote_branches() -> list[str]:
        return git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

    # The control: the pre-push hook refuses a plain push, and the -c form goes past it.
    assert "delegate: push refused" in shell("git push origin HEAD:refs/heads/plain").stderr
    assert shell("git -c core.hooksPath=/dev/null push origin HEAD:refs/heads/control").returncode == 0
    assert remote_branches() == ["control"]

    command = "git -c core.hooksPath=/dev/null push origin HEAD:refs/heads/stolen"
    guard = run_hook(claude_code_hook, command, cwd=worktree)
    if guard.returncode == 0:
        shell(command)

    assert guard.returncode == 2, (guard.stdout, guard.stderr)
    assert "core.hooksPath" in guard.stderr
    assert remote_branches() == ["control"]
