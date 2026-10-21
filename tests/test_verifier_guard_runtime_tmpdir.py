"""The Bash guard accepts a path under the runtime value of $TMPDIR, as the Write guard does (#166).

A host can set `TMPDIR` to a directory outside the fixed temp prefixes, for
example `/home/agent/.tmp`. `mktemp -d` then gives a directory there. The Bash
guard of 1.9.0 accepted only the fixed prefixes and the literal word `$TMPDIR`,
so a `cd` or a write on the absolute path from `mktemp` was a deny, while the
Write guard permitted the same path.

The guards now share one rule. The runtime value counts only when it is safe:
an absolute path with at least two components, with no empty, `.` or `..`
component, that is not the home directory or a parent of it, and that is not
inside a git tree. Each other rule still applies to a path under it: not inside
a git tree, not holding one (#125).

Each case uses real directories and real git repositories in a temp directory
that is outside the fixed prefixes as text, renders the example declaration,
and runs the rendered hooks in bash, /bin/sh and zsh, as the harness does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from delegate.definitions.declaration import load_declaration
from delegate.definitions.render import render_claude_code
from delegate.definitions.validate import split_frontmatter
from delegate.shared.tiers import load_tiers

from .support import git

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "java-spring.toml"

#: Each shell that runs the Bash guard.
SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

#: The fixed temp prefixes of the guards, as text.
FIXED = ("/tmp/", "/private/tmp/", "/var/folders/")


def _hooks() -> dict[str, str]:
    [agent] = load_declaration(EXAMPLE)
    fm, _body, err = split_frontmatter(render_claude_code(agent, load_tiers())["java-spring-verifier.md"])
    assert err is None, err
    return {entry["matcher"]: entry["hooks"][0]["command"] for entry in fm["hooks"]["PreToolUse"]}


HOOKS = _hooks()


class Host:
    """Directories in a temp directory `base` whose path does not start with a fixed temp prefix.

    `tmp` is the runtime temp directory, as `/home/agent/.tmp` on an agent host.
    It holds the directory `x`, a git repository `repo`, and a directory `hold`
    that holds a git repository. `home` is the home directory of the hook.
    `outer` is a git repository with the directory `tmp` in it.
    """

    def __init__(self, base: Path):
        self.base = base
        self.home = base / "home"
        self.home.mkdir()
        self.tmp = base / "agent" / ".tmp"
        (self.tmp / "x").mkdir(parents=True)
        (self.tmp / "x" / "f").write_text("f\n")
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q", "-b", "main")
        (self.tmp / "hold" / "inner").mkdir(parents=True)
        git(self.tmp / "hold" / "inner", "init", "-q", "-b", "main")
        self.outer = base / "outer"
        (self.outer / "tmp" / "x").mkdir(parents=True)
        git(self.outer, "init", "-q", "-b", "main")

    def env(self, tmpdir: str | Path | None = None, home: str | Path | None = None) -> dict[str, str]:
        return {
            **os.environ,
            "TMPDIR": "/" if tmpdir == "/" else f"{self.tmp if tmpdir is None else tmpdir}/",
            "HOME": str(self.home if home is None else home),
        }


@pytest.fixture
def host(tmp_path, git_identity):
    """A Host in the pytest temp directory, when its real path is outside the fixed prefixes as text.

    On macOS the real path starts with /private/var/folders, and a Nix build
    sandbox gives /build. On a Linux host the pytest temp directory is in /tmp,
    so the host then goes in /var/tmp.
    """
    base = Path(os.path.realpath(tmp_path))
    made = None
    if str(base).startswith(FIXED):
        if not os.access("/var/tmp", os.W_OK):
            pytest.skip("no writable temp directory outside the fixed temp prefixes")
        made = base = Path(os.path.realpath(tempfile.mkdtemp(dir="/var/tmp")))
        if str(base).startswith(FIXED):
            shutil.rmtree(made, ignore_errors=True)
            pytest.skip("/var/tmp is in a fixed temp prefix on this host")
    yield Host(base)
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


def _bash(command: str, shell: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the hook input and of the process."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", HOOKS["Bash"]], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True, env=env, cwd=cwd)


def _write(path: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess:
    """Run the Write guard on a Write of `path`, as Claude Code does."""
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": path, "content": "{}"}, "cwd": str(cwd)})
    return subprocess.run(["bash", "-c", HOOKS["Write"]], input=payload, capture_output=True, text=True, env=env, cwd=cwd)


def _permitted(result: subprocess.CompletedProcess, *context) -> None:
    assert result.returncode == 0, (*context, result.stderr)


def _denied(result: subprocess.CompletedProcess, *context) -> None:
    assert result.returncode == 2, (*context, result.stderr)
    assert "verifier guard" in result.stderr, (*context, result.stderr)


# --- A path under a safe runtime TMPDIR is a temp path ------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "cd {t}/x",
        "touch {t}/x/f",
        "rm {t}/x/f",
        "mkdir {t}/x/y",
        "echo hi > {t}/x/f",
        "cp {t}/x/f {t}/x/g",
        "cd {t}/x && ls",
    ],
)
def test_a_path_under_the_runtime_tmpdir_is_permitted(host, shell, command):
    command = command.format(t=host.tmp)

    _permitted(_bash(command, shell, host.env(), host.base), shell, command)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_cd_and_a_write_on_the_directory_that_mktemp_gives_are_permitted(host, shell):
    made = subprocess.run(["mktemp", "-d"], capture_output=True, text=True, env=host.env(), check=True).stdout.strip()
    assert made.startswith(f"{host.tmp}/"), made

    for command in (f"cd {made}", f"touch {made}/f", f"cd {made} && pwd"):
        _permitted(_bash(command, shell, host.env(), host.base), shell, command)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_write_from_a_working_directory_under_the_runtime_tmpdir_is_permitted(host, shell):
    _permitted(_bash("touch x/g", shell, host.env(), host.tmp), shell)


# --- The rules of a git tree still apply under the runtime TMPDIR (#125) ------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        pytest.param("touch {t}/repo/f", id="a-path-in-a-git-tree"),
        pytest.param("echo x > {t}/repo/f", id="a-redirection-into-a-git-tree"),
        pytest.param("rm -rf {t}/repo/.git", id="the-git-directory-of-a-tree"),
        pytest.param("rm -rf {t}/hold", id="a-directory-that-holds-a-git-tree"),
        pytest.param("mv {t}/hold {t}/moved", id="a-move-of-a-directory-that-holds-a-git-tree"),
    ],
)
def test_a_path_under_the_runtime_tmpdir_in_or_holding_a_git_tree_is_denied(host, shell, command):
    command = command.format(t=host.tmp)

    _denied(_bash(command, shell, host.env(), host.base), shell, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["touch repo/f", "rm -rf hold"])
def test_a_relative_write_into_a_git_tree_under_the_runtime_tmpdir_is_denied(host, shell, command):
    _denied(_bash(command, shell, host.env(), host.tmp), shell, command)


# --- An unsafe runtime TMPDIR adds no temp path -------------------------------


def _unsafe(host: Host) -> dict[str, tuple[str, Path, str]]:
    """For each unsafe TMPDIR: the value, the home directory, and a directory under the value that exists, as text."""
    return {
        "slash": ("/", host.home, str(host.base)),
        "home": (str(host.home), host.home, str(host.home)),
        "a-parent-of-home": (str(host.base), host.home, str(host.base / "agent")),
        "inside-a-git-tree": (str(host.outer / "tmp"), host.home, str(host.outer / "tmp" / "x")),
        "a-git-tree": (str(host.outer), host.home, str(host.outer / "tmp")),
        "relative": ("agent/.tmp", host.home, str(host.tmp / "x")),
        # A "." component hides a parent of the home directory from a test on the text.
        "a-dot-component": (f"{host.base}/./agent/.tmp", host.home, f"{host.base}/./agent/.tmp/x"),
    }


UNSAFE = ["slash", "home", "a-parent-of-home", "inside-a-git-tree", "a-git-tree", "relative", "a-dot-component"]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("case", UNSAFE)
def test_a_path_under_an_unsafe_runtime_tmpdir_is_not_a_temp_path(host, shell, case):
    tmpdir, home, under = _unsafe(host)[case]
    env = host.env(tmpdir, home)

    for command in (f"cd {under}", f"touch {under}/f", f"rm {under}/f"):
        result = _bash(command, shell, env, host.base)
        _denied(result, shell, case, command)
        assert "outside a temp directory" in result.stderr, (shell, case, command, result.stderr)


@pytest.mark.parametrize("case", UNSAFE)
def test_the_write_guard_denies_a_path_under_an_unsafe_runtime_tmpdir(host, case):
    tmpdir, home, under = _unsafe(host)[case]

    result = _write(f"{under}/f", host.env(tmpdir, home), host.base)

    assert result.returncode == 2, (case, result.stderr)
    assert "temp directory" in result.stderr, (case, result.stderr)


# --- The Bash guard and the Write guard agree ---------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_and_the_write_guard_agree_on_each_runtime_tmpdir(host, shell):
    cases = {
        "safe": (str(host.tmp), host.home, [f"{host.tmp}/x/f", f"{host.tmp}/repo/f", f"{host.tmp}/hold"]),
        **{name: (tmpdir, home, [f"{under}/f"]) for name, (tmpdir, home, under) in _unsafe(host).items()},
        "one-component": ("/agent-tmp", host.home, ["/agent-tmp/f"]),
        "trailing-slashes": (f"{host.tmp}//", host.home, [f"{host.tmp}/x/f"]),
    }
    verdicts = {}
    for name, (tmpdir, home, paths) in cases.items():
        env = host.env(tmpdir, home)
        for path in paths:
            bash = _bash(f"touch {path}", shell, env, host.base).returncode
            write = _write(path, env, host.base).returncode
            verdicts[(name, path)] = (bash, write)

    assert {k: v for k, v in verdicts.items() if v[0] != v[1]} == {}
    assert verdicts[("safe", f"{host.tmp}/x/f")] == (0, 0)
    assert verdicts[("trailing-slashes", f"{host.tmp}/x/f")] == (0, 0)
    assert verdicts[("safe", f"{host.tmp}/repo/f")] == (2, 2)
    assert verdicts[("home", f"{host.home}/f")] == (2, 2)
