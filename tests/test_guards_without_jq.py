"""The rendered Claude Code guards read the hook input with python3, not jq.

A machine with no jq must get the same verdicts as a machine with jq. Before
this change the specialist push guard let a push through and the verifier guard
denied every command when jq was absent.
"""

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from delegate.render import GIT_PUSH_HOOK, VERIFIER_BASH_HOOK

SHELLS = ("bash", "/bin/sh")


def _bin_dir_with_only(tmp_path: Path, *names: str) -> Path:
    """A directory for PATH that holds a wrapper for each named program and
    nothing else, so a program that is not named is absent from PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in names:
        real = sys.executable if name == "python3" else shutil.which(name)
        assert real, f"{name} is needed by this test"
        wrapper = bin_dir / name
        wrapper.write_text(f'#!/bin/sh\nexec "{real}" "$@"\n')
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return bin_dir


@pytest.fixture
def no_jq_env(tmp_path: Path) -> dict[str, str]:
    env = {"PATH": str(_bin_dir_with_only(tmp_path, "python3"))}
    assert shutil.which("jq", path=env["PATH"]) is None
    return env


def _run(hook: str, stdin: str, env: dict[str, str], shell: str = "bash") -> subprocess.CompletedProcess:
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=stdin, capture_output=True, text=True, env=env)


def _payload(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


@pytest.mark.parametrize("shell", SHELLS)
def test_the_push_guard_blocks_a_push_when_jq_is_absent(no_jq_env, shell):
    for command in ("git push", "git push origin main", "git add . && git push --force", "git -C /tmp/x push"):
        r = _run(GIT_PUSH_HOOK, _payload(command), no_jq_env, shell)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
        assert "blocked" in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_push_guard_lets_other_commands_through_when_jq_is_absent(no_jq_env, shell):
    for command in ("git status", "git log --oneline", "echo pushing", "git pull", "nix flake check"):
        r = _run(GIT_PUSH_HOOK, _payload(command), no_jq_env, shell)
        assert r.returncode == 0, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_verifier_guard_permits_a_read_and_denies_a_write_when_jq_is_absent(no_jq_env, shell):
    for command in ("git diff main...HEAD", "ls -la /tmp/x", "git status && cat /etc/hosts", "rm -rf /tmp/x"):
        r = _run(VERIFIER_BASH_HOOK, _payload(command), no_jq_env, shell)
        assert r.returncode == 0, (shell, command, r.stdout, r.stderr)
    for command in ("git commit -m x", "git push", "rm -rf /Users/x", "echo hi > /Users/x"):
        r = _run(VERIFIER_BASH_HOOK, _payload(command), no_jq_env, shell)
        assert r.returncode == 2, (shell, command, r.stdout)
        assert command in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "stdin",
    [
        "",
        "not json",
        "{}",
        json.dumps({"tool_input": {}}),
        json.dumps({"tool_input": {"command": 7}}),
        json.dumps({"tool_input": {"command": None}}),
    ],
    ids=["empty", "not-json", "no-tool-input", "no-command", "number-command", "null-command"],
)
@pytest.mark.parametrize("hook", [GIT_PUSH_HOOK, VERIFIER_BASH_HOOK], ids=["push-guard", "verifier-guard"])
def test_each_guard_fails_closed_when_it_cannot_read_the_command_text(no_jq_env, hook, stdin, shell):
    r = _run(hook, stdin, no_jq_env, shell)
    assert r.returncode == 2, (shell, stdin, r.stdout, r.stderr)
    assert r.stderr.strip(), "a block must say why"


@pytest.mark.parametrize("hook", [GIT_PUSH_HOOK, VERIFIER_BASH_HOOK], ids=["push-guard", "verifier-guard"])
def test_each_guard_fails_closed_when_python3_is_absent(tmp_path, hook):
    env = {"PATH": str(_bin_dir_with_only(tmp_path))}
    r = _run(hook, _payload("git diff"), env)
    assert r.returncode == 2, (r.stdout, r.stderr)
    assert r.stderr.strip(), "a block must say why"
