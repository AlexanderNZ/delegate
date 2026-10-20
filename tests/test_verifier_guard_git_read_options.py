"""`git grep` is a read command, and the git read options that write a file or run a command are denied (#140).

Each verifier reads a git tree, so `git grep` is in the read list of every
verifier. The guard matches a read command as a prefix, and some git read
commands have an option that writes or runs:

- `git diff`, `git log` and `git show` take `--output=<file>`, which writes a
  file at any path. git accepts a unique prefix of a long option, so `--outp`
  is the same option.
- `git grep` takes `-O<cmd>` and `--open-files-in-pager[=<cmd>]`, which run a
  command on the files that match.

The guard denies these options after a git read command. An output path in a
temp directory is permitted, as for a GET-only command. The deny message names
the option. The OpenCode map cannot examine the words of a command, so it
leaves `git grep` out.

Each case renders a declaration, and runs the rendered hook in bash, /bin/sh
and zsh, as the harness does, with a real git repository as the working
directory.
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
from delegate.definitions.render import render_claude_code, render_opencode
from delegate.definitions.validate import opencode_bash_action, split_frontmatter
from delegate.shared.tiers import load_tiers

from .support import git

SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

DECLARATION = {"agents": {"stack": {"description": "A repository with one stack."}}}


def _claude_code_verifier() -> tuple[dict, str]:
    [agent] = parse_declaration(DECLARATION)
    fm, body, err = split_frontmatter(render_claude_code(agent, load_tiers())["stack-verifier.md"])
    assert err is None, err
    return fm, body


def _opencode_verifier() -> tuple[dict, str]:
    [agent] = parse_declaration(DECLARATION)
    fm, body, err = split_frontmatter(render_opencode(agent, load_tiers())["stack-verifier.md"])
    assert err is None, err
    return fm, body


def _hook() -> str:
    fm, _body = _claude_code_verifier()
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


HOOK = _hook()


@pytest.fixture
def repo(tmp_path, git_identity) -> Path:
    """A git repository with one tracked file."""
    root = Path(os.path.realpath(tmp_path)) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("foo\n")
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


# --- git grep is a read --------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "git grep foo",
        "git grep -n -i foo",
        "git grep -e foo -- a.txt",
        "git grep -o foo",
        "git grep foo 2>/dev/null",
        "git grep --no-open-files-in-pager foo",
    ],
)
def test_git_grep_is_a_read_command(repo, shell, command):
    r = _guard(command, shell, repo)
    assert r.returncode == 0, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_git_grep_by_path_is_a_read_command(repo, shell, tmp_path):
    command = f"git -C {repo} grep foo"
    r = _guard(command, shell, Path(os.path.realpath(tmp_path)))
    assert r.returncode == 0, (shell, command, r.stderr)


# --- The options that run a command ---------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,option",
    [
        ("git grep -Ocat foo", "-Ocat"),
        ("git grep -O cat foo", "-O"),
        ("git grep -O foo", "-O"),
        ("git grep -iOcat foo", "-iOcat"),
        ("git grep -n -O/tmp/x foo", "-O/tmp/x"),
        ("git grep --open-files-in-pager=cat foo", "--open-files-in-pager=cat"),
        ("git grep --open-files-in-pager foo", "--open-files-in-pager"),
        ("git grep --open-files=cat foo", "--open-files=cat"),
        ("git grep --op=cat foo", "--op=cat"),
        ("git grep --o=cat foo", "--o=cat"),
        ("git grep '-Ocat' foo", "-Ocat"),
        ('git grep "--open-files-in-pager=cat" foo', "--open-files-in-pager=cat"),
        ("git grep -\\Ocat foo", "-Ocat"),
    ],
)
def test_a_git_read_option_that_runs_a_command_is_denied_and_named(repo, shell, command, option):
    r = _guard(command, shell, repo)
    assert r.returncode == 2, (shell, command, r.stderr)
    assert f"[{command}]" in r.stderr, (shell, command, r.stderr)
    assert f"The option {option} runs a command" in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "git -C {repo} grep -Ocat foo",
        "nix develop -c git grep -Ocat foo",
        "git status && git grep -O cat foo",
    ],
)
def test_a_prefix_or_a_compound_command_does_not_hide_the_option(repo, shell, command):
    command = command.format(repo=repo)
    r = _guard(command, shell, repo)
    assert r.returncode == 2, (shell, command, r.stderr)
    assert "runs a command" in r.stderr, (shell, command, r.stderr)


# --- The option that writes a file ----------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,option",
    [
        ("git diff --output=/Users/x/f", "--output=/Users/x/f"),
        ("git log --output /Users/x/f", "--output"),
        ("git diff --outp=/Users/x/f", "--outp=/Users/x/f"),
        ("git show --output=/Users/x/f HEAD", "--output=/Users/x/f"),
        ("git show --out /Users/x/f HEAD", "--out"),
        ("git diff --output=f", "--output=f"),
        ("git diff --output=", "--output="),
        ("git diff --output=/tmp/../Users/x/f", "--output=/tmp/../Users/x/f"),
        ("git diff '--output=/Users/x/f'", "--output=/Users/x/f"),
    ],
)
def test_an_output_option_outside_a_temp_directory_is_denied_and_named(repo, shell, command, option):
    r = _guard(command, shell, repo)
    assert r.returncode == 2, (shell, command, r.stderr)
    assert f"[{command}]" in r.stderr, (shell, command, r.stderr)
    assert f"The option {option} writes a file" in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_an_output_option_with_no_path_is_denied(repo, shell):
    command = "git log --output"
    r = _guard(command, shell, repo)
    assert r.returncode == 2, (shell, command, r.stderr)
    assert "The option --output needs a path argument." in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "git diff --output=/tmp/x/f",
        "git diff --output /tmp/x/f",
        "git log --outp=/tmp/x/f -1",
        "git show --output=$TMPDIR/f HEAD",
        "git diff --output-indicator-new=+ HEAD",
    ],
)
def test_an_output_option_with_a_temp_path_is_permitted(repo, shell, command):
    r = _guard(command, shell, repo)
    assert r.returncode == 0, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_an_output_path_in_a_git_tree_in_a_temp_directory_is_denied(repo, shell):
    # The repository of the test is in a temp directory. A write into it changes a git tree (#125). The path is
    # $TMPDIR/<path in the temp base>, and the guard runs with TMPDIR set to the temp base. Thus the path is a temp
    # path also where the temp base has no temp prefix of the guard (/build in the Linux Nix build sandbox).
    base = repo.parent
    command = f"git diff --output=$TMPDIR/{repo.relative_to(base).as_posix()}/out.diff"
    r = _guard(command, shell, repo, tmpdir=base)
    assert r.returncode == 2, (shell, command, r.stderr)
    assert "git toplevel" in r.stderr, (shell, command, r.stderr)


# --- The other surfaces ---------------------------------------------------------------


def test_the_opencode_map_leaves_git_grep_out_and_keeps_the_other_git_reads():
    fm, _body = _opencode_verifier()
    permission = fm["permission"]
    assert opencode_bash_action(permission, "git grep foo") == "deny"
    assert opencode_bash_action(permission, "git grep -Ocat foo") == "deny"
    assert opencode_bash_action(permission, "git diff main...HEAD") == "allow"


def test_the_rendered_verifiers_name_git_grep_and_the_denied_options():
    _fm, claude_body = _claude_code_verifier()
    for text in ("`git grep`", "`--output`", "`-O`", "`--open-files-in-pager`"):
        assert text in claude_body, text
    _fm, opencode_body = _opencode_verifier()
    assert "`git grep`" in opencode_body
