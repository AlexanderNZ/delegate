"""The output rule of a GET-only command covers each unique prefix of --output (#141).

A command built on argparse accepts a unique prefix of a long option
(`allow_abbrev`), so `--out <path>` is the same option as `--output <path>`.
The guard reads each prefix of `--output` with at least three characters after
`--` (`--out`, `--outp`, `--outpu`), and each `=<path>` form, as `--output`:
the path must be a temp path.

Each case renders a declaration with a GET-only command, and runs the
rendered hook in bash, /bin/sh and zsh, as the harness does, with a real git
repository as the working directory.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from delegate.definitions.declaration import parse_declaration
from delegate.definitions.render import render_claude_code
from delegate.definitions.validate import split_frontmatter
from delegate.shared.tiers import load_tiers

from .support import git

SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

DECLARATION = {
    "agents": {"stack": {"description": "A repository with one stack.", "getOnlyCommands": ["agent-definitions render"]}}
}


def _hook() -> str:
    [agent] = parse_declaration(DECLARATION)
    fm, _body, err = split_frontmatter(render_claude_code(agent, load_tiers())["stack-verifier.md"])
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


HOOK = _hook()


@pytest.fixture
def worktree(tmp_path, git_identity) -> Path:
    """A git repository with one commit, in the role of the worktree under review."""
    root = Path(os.path.realpath(tmp_path)) / "worktree"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-q", "-m", "a")
    return root


def _guard(command: str, shell: str, cwd: Path, tmpdir: Path | None = None) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the hook input and of the process.

    When `tmpdir` is given, the guard runs with `TMPDIR` set to it.
    """
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", HOOK], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    env = None if tmpdir is None else {**os.environ, "TMPDIR": f"{tmpdir}/"}
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True, env=env, cwd=cwd)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "spelling",
    ["--out {path}", "--out={path}", "--outpu {path}", "--outp {path}/out", "--outpu={path}/out", "--output {path}"],
)
def test_a_prefix_of_output_with_a_path_in_the_worktree_is_denied(worktree, shell, spelling):
    # The path is $TMPDIR/<path in the temp base>, and the guard runs with TMPDIR set to the temp base. Thus the path
    # is a temp path also where the temp base has no temp prefix of the guard (/build in the Linux Nix build sandbox),
    # and only the git tree rule (#125) can deny it.
    base = worktree.parent
    path = "$TMPDIR/" + worktree.relative_to(base).as_posix()
    command = "agent-definitions render " + spelling.format(path=path)
    r = _guard(command, shell, worktree, tmpdir=base)
    assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
    assert f"[{command}]" in r.stderr, (shell, command, r.stderr)
    assert "git toplevel" in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("spelling", ["--out /Users/x/out", "--out=/Users/x/out", "--outpu /Users/x/out", "--out out"])
def test_a_prefix_of_output_with_a_path_outside_a_temp_directory_is_denied(worktree, shell, spelling):
    command = f"agent-definitions render {spelling}"
    r = _guard(command, shell, worktree)
    assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
    assert "An output path is outside a temp directory." in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("spelling", ["--out", "--outpu"])
def test_a_prefix_of_output_with_no_path_is_denied(worktree, shell, spelling):
    command = f"agent-definitions render {spelling}"
    r = _guard(command, shell, worktree)
    assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
    assert "An output flag needs a path argument." in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "spelling",
    [
        "--out /tmp/x",
        "--out=/tmp/x",
        "--outp /tmp/x",
        "--outpu=$TMPDIR/x",
        "--output /tmp/x",
        # A long option that only starts with --out is another option.
        "--outline x",
    ],
)
def test_a_prefix_of_output_with_a_temp_path_is_permitted(worktree, shell, spelling):
    command = f"agent-definitions render {spelling}"
    r = _guard(command, shell, worktree)
    assert r.returncode == 0, (shell, command, r.stdout, r.stderr)
