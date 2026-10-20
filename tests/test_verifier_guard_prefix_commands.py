"""`env` is a prefix that the guard tests through, and no read command runs another command (#158).

Before #158, `env` was in the read list, and the guard matches a read command
by its first word. So `env touch <file>`, and each other command behind `env`,
passed the guard. Now the guard removes `env` and its options `-i`, `-0`, `-`,
`--` and `-u <name>`, and it tests the rest of the segment as its own command,
with every rule. A bare `env`, or `env` with only options and `NAME=value`
assignments, stays a read. An assignment before a command is denied, as an
assignment prefix without `env` is: `GIT_EXTERNAL_DIFF=touch` makes `git diff`
write files. Behind `env`, `bash -c` and `python3` are denied: the guard cannot
examine the command that they run, and the verifier needs them only as the
first word of a gate or a probe.

The check of the other read commands for the same shape found three more:
`find` runs a command with `-exec`, `-execdir`, `-ok` and `-okdir` (and writes
with `-delete`, `-fprint` and the like), `rg` runs a command with `--pre` and `--hostname-bin`, and a
GNU sed script runs a command with `e` (and writes with `w`, and `-i` edits in
place). The guard denies these options of `find` and `rg`, and it permits
`sed -n` only as a line range. Commands that run another command and are not in
the read list (`xargs`, `nice`, `nohup`, `timeout`, `time`, `command`, `exec`)
stay denied.

The OpenCode map cannot examine the words of a command, so `env *` would permit
a write. The map leaves `env` out.

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


def _rendered(render) -> dict:
    [agent] = parse_declaration(DECLARATION)
    fm, _body, err = split_frontmatter(render(agent, load_tiers())["stack-verifier.md"])
    assert err is None, err
    return fm


def _hook() -> str:
    [bash] = [entry for entry in _rendered(render_claude_code)["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
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


def _guard(command: str, shell: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the hook input and of the process."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", HOOK], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True, cwd=cwd)


def _denied(r: subprocess.CompletedProcess, shell: str, command: str, segment: str) -> None:
    assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
    assert f"[{segment}]" in r.stderr, (shell, command, r.stderr)


# --- env is a prefix ------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "env touch /Users/x/f",
        "env -i touch /Users/x/f",
        "env A=1 rm /Users/x/f",
        "env bash -c 'touch /Users/x/f'",
        "env -u HOME -0 -- A=1 B=2 touch /Users/x/f",
        "env --unset=HOME --ignore-environment touch /Users/x/f",
        "env - mkdir /Users/x/d",
        "env -uHOME cp /tmp/a /Users/x/f",
        "env env touch /Users/x/f",
        "env -i env -u HOME rm /Users/x/f",
        "env -i git commit -m x",
        "env python3 -c 'open(\"/Users/x/f\",\"w\")'",
        "env -u HOME python3 -m pytest",
        "env -i bash -c 'ls'",
        "env sh -c 'touch /Users/x/f'",
    ],
)
def test_a_command_behind_env_is_tested_as_its_own_command(repo, shell, command):
    # Each command writes outside a temp directory, or runs a command that the
    # guard cannot examine. Before #158, each one passed as the read "env".
    _denied(_guard(command, shell, repo), shell, command, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,segment",
    [
        ("ls && env touch /Users/x/f", "env touch /Users/x/f"),
        ("env | env rm /Users/x/f", "env rm /Users/x/f"),
        ("nix develop -c env touch /Users/x/f", "nix develop -c env touch /Users/x/f"),
        ("direnv exec . env A=1 touch /Users/x/f", "direnv exec . env A=1 touch /Users/x/f"),
        ("git -C . status; env -i rm -rf /Users/x", "env -i rm -rf /Users/x"),
    ],
)
def test_a_prefix_or_a_compound_command_does_not_hide_env(repo, shell, command, segment):
    _denied(_guard(command, shell, repo), shell, command, segment)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "env",
        "env -0",
        "env | grep PATH",
        "env -i",
        "env -u HOME",
        "env A=1 B=2",
        "env -i -- A=1",
        "env -i git status",
        "env -u HOME -- ls -la",
        "env -0 grep foo a.txt",
        "env  -i  cat a.txt",
        "nix develop -c env -i git log --oneline",
    ],
)
def test_env_alone_or_before_a_read_is_permitted(repo, shell, command):
    r = _guard(command, shell, repo)
    assert r.returncode == 0, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["env -u HOME mkdir /tmp/delegate-158-probe/d", "env -i touch /tmp/delegate-158-probe/f"])
def test_a_temp_write_behind_env_keeps_the_temp_rule(repo, shell, command):
    # The guard reads the text only. It does not run the command, so it makes no path.
    r = _guard(command, shell, repo)
    assert r.returncode == 0, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,word",
    [
        # The shell gives env the word "A= ls ", and env runs touch.
        ("env A=' ls ' touch /Users/x/f", "A='"),
        ('env A=" ls" touch /Users/x/f', 'A="'),
        ("env A=1\\ ls touch /Users/x/f", "A=1\\"),
        ("env -u * ls", "*"),
        ("env A=$X ls", "A=$X"),
        ("env 'touch' /Users/x/f", "'touch'"),
    ],
)
def test_a_word_that_env_reads_must_be_plain_text(repo, shell, command, word):
    # The guard divides the text at each space. A quote, a backslash, an
    # expansion or a pattern in a word before the command can move the start of
    # the command, so the guard denies the word and names it.
    r = _guard(command, shell, repo)
    _denied(r, shell, command, command)
    assert f"The word {word} " in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,option",
    [
        ("env -S 'touch /Users/x/f'", "-S"),
        ("env -Srm ls", "-Srm"),
        ("env --split-string=rm ls", "--split-string=rm"),
        ("env -C /Users/x touch f", "-C"),
        ("env --chdir=/Users/x touch f", "--chdir=/Users/x"),
        ("env -P /tmp/bin ls", "-P"),
        ("env -a ls touch /Users/x/f", "-a"),
        ("env -iS rm ls", "-iS"),
        ("env -v touch /Users/x/f", "-v"),
    ],
)
def test_an_env_option_outside_the_permitted_set_is_denied_and_named(repo, shell, command, option):
    # -S runs the words of a string, -C changes the directory, -P changes the
    # search path, and -a changes the name of the command. Only -i, -0, -u,
    # - and -- are permitted.
    r = _guard(command, shell, repo)
    _denied(r, shell, command, command)
    assert f"The env option {option} " in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,assignment",
    [
        ("env A=1 git status", "A=1"),
        ("env GIT_EXTERNAL_DIFF=touch git diff", "GIT_EXTERNAL_DIFF=touch"),
        (
            "env GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.fsmonitor GIT_CONFIG_VALUE_0=/tmp/x.sh git status",
            "GIT_CONFIG_COUNT=1",
        ),
        ("env -i PATH=/tmp/bin ls", "PATH=/tmp/bin"),
        ("env -- LC_ALL=C cat a.txt", "LC_ALL=C"),
    ],
)
def test_an_assignment_before_a_command_is_denied_and_named(repo, shell, command, assignment):
    # GIT_EXTERNAL_DIFF=touch makes "git diff" run touch, which writes files in
    # the tree. The guard denies an assignment prefix without env too.
    r = _guard(command, shell, repo)
    _denied(r, shell, command, command)
    assert f"The assignment {assignment} comes before a command." in r.stderr, (shell, command, r.stderr)


def test_the_opencode_map_has_no_env_rule():
    # A pattern "env *" matches "env touch <file>", and a pattern cannot
    # examine the words after env, so the map leaves env out.
    perm = _rendered(render_opencode)["permission"]["bash"]
    assert not [rule for rule in perm if rule == "env" or rule.startswith("env ")], perm
    for command in ("env touch /Users/x/f", "env -i rm /Users/x/f", "env A=1 git commit -m x"):
        assert opencode_bash_action(perm, command) == "deny", command


# --- The other read commands that run a command ------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,option",
    [
        ("find . -exec touch /Users/x/f +", "-exec"),
        ("find . -name a.txt -exec rm {} +", "-exec"),
        ("find . -execdir touch /Users/x/f \\;", "-execdir"),
        ("find . -ok rm {} \\;", "-ok"),
        ("find . -okdir rm {} \\;", "-okdir"),
        ("find . '-exec' touch /Users/x/f \\;", "-exec"),
        ('find . "-execdir" rm {} +', "-execdir"),
        ("find . -\\exec rm {} +", "-exec"),
        ("find /Users/x -delete", "-delete"),
        ("find /Users/x -rm", "-rm"),
        ("find . -fprint /Users/x/f", "-fprint"),
        ("find . -fprint0 /Users/x/f", "-fprint0"),
        ("find . -fprintf /Users/x/f %p", "-fprintf"),
        ("find . -fls /Users/x/f", "-fls"),
        ("nix develop -c find . -exec rm {} +", "-exec"),
        ("env find . -delete", "-delete"),
    ],
)
def test_a_find_option_that_runs_a_command_or_writes_is_denied_and_named(repo, shell, command, option):
    r = _guard(command, shell, repo)
    _denied(r, shell, command, command)
    assert f"The find option {option} " in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "find . -name '*.txt'",
        "find . -type f -print",
        "find . -executable -type f",
        "find . -maxdepth 1 -print0",
        "find . -name exec",
        "find . -newer a.txt -ls",
    ],
)
def test_find_without_such_an_option_is_a_read(repo, shell, command):
    r = _guard(command, shell, repo)
    assert r.returncode == 0, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,option",
    [
        ("rg --pre cat foo", "--pre"),
        ("rg --pre=touch foo", "--pre=touch"),
        ("rg -n --pre /tmp/x.sh foo", "--pre"),
        ("rg '--pre=sh' foo", "--pre=sh"),
        ("rg --hostname-bin=/tmp/x.sh --hyperlink-format=default foo", "--hostname-bin=/tmp/x.sh"),
        ("rg --hostname-bin /tmp/x.sh foo", "--hostname-bin"),
        # The guard does not know that -e takes the next word, so a pattern
        # that looks like the option denies. A deny, never a hole.
        ("rg -e --pre foo a.txt", "--pre"),
    ],
)
def test_the_rg_option_that_runs_a_command_is_denied_and_named(repo, shell, command, option):
    # rg runs the --pre command for each file that it searches, and the
    # --hostname-bin command for the hostname of a hyperlink.
    r = _guard(command, shell, repo)
    _denied(r, shell, command, command)
    assert f"The rg option {option} " in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["rg foo", "rg -n --pre-glob '*.gz' foo", "rg --no-pre foo"])
def test_rg_without_the_pre_option_is_a_read(repo, shell, command):
    r = _guard(command, shell, repo)
    assert r.returncode == 0, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "xargs touch",
        "nice touch /Users/x/f",
        "nohup touch /Users/x/f",
        "timeout 5 touch /Users/x/f",
        "time touch /Users/x/f",
        "command touch /Users/x/f",
        "exec touch /Users/x/f",
        "ls | xargs rm",
    ],
)
def test_a_command_that_runs_another_command_and_is_not_a_read_is_denied(repo, shell, command):
    r = _guard(command, shell, repo)
    assert r.returncode == 2, (shell, command, r.stderr)
    assert "Only read commands and temp-directory writes are permitted." in r.stderr, (shell, command, r.stderr)


# --- sed -n is a read only as a line range --------------------------------------------


SED_ONLY_RANGE = "A sed -n command is permitted only as a line range"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "sed -n '1e touch /Users/x/f' a.txt",
        "sed -n 1e\\ touch\\ /Users/x/f a.txt",
        "sed -n 'w /Users/x/f' a.txt",
        "sed -n '1,2w /Users/x/f' a.txt",
        "sed -n 's/a/b/w /Users/x/f' a.txt",
        "sed -n -i 1p a.txt",
        "sed -n 1p -i a.txt",
        "sed -n 1p --in-place a.txt",
        "sed -n -e 1p a.txt",
        "sed -n -f /tmp/x.sed a.txt",
        "sed -n '1p;e touch /Users/x/f' a.txt",
        "sed -n '1,5p; w /Users/x/f' a.txt",
        "sed -n '/a/p' a.txt",
        "sed -n 1,\\$p a.txt",
        "sed -n \"1,$p\" a.txt",
        "sed -n 1,2,3p a.txt",
        "sed -n 1p a.txt*",
        "sed -n 1p '-i' a.txt",
        "env sed -n 1e a.txt",
    ],
)
def test_a_sed_n_command_that_is_not_a_line_range_is_denied(repo, shell, command):
    # GNU sed runs a command with "e", and sed writes a file with "w" and "-i".
    r = _guard(command, shell, repo)
    _denied(r, shell, command, command)
    assert SED_ONLY_RANGE in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "sed -n '1,20p' a.txt",
        "sed -n 5p a.txt",
        "sed -n 1,2p a.txt b.txt",
        "sed -n '3,$p' a.txt",
        "sed -n '$p' a.txt",
        'sed -n "1,20p" a.txt',
        "sed -n '1,20p' a.txt 2>/dev/null",
        "sed -n '1,20p' a.txt | head -5",
        # No script: sed only prints its usage.
        "sed -n",
    ],
)
def test_a_sed_n_line_range_is_a_read(repo, shell, command):
    r = _guard(command, shell, repo)
    assert r.returncode == 0, (shell, command, r.stderr)
