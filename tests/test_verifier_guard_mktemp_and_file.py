"""`mktemp` writes only in a temp directory, and `file -C` is not a read (#161).

`mktemp` and `file` are in the read list of the verifier guard. Before #161,
the guard permitted each of their forms, and two forms write a file outside a
temp directory: `mktemp <template>` makes the file that the template names,
and `file -C` compiles a magic file and writes it in the working directory.
Neither form runs another command, so the rules of #158 did not cover them.

Now the guard examines the words of `mktemp`. It permits `mktemp` when the
file that it makes is in a temp directory: with no template, with `-t` and one
name, with `-p <temp path>` or `--tmpdir[=<temp path>]` and names with no
"/", or with a template that is a temp path. After `-p`, `--tmpdir` or `-t`,
at any position, a template with a "/" denies: mktemp makes it in that
directory or in $TMPDIR, not in the working directory. It denies each other form. macOS mktemp reads the word
after `-t` as a prefix in $TMPDIR, and it makes one more file for each
template, in the working directory, so `-t` permits one name only, with no
"/". The guard denies `-C` and `--compile` after `file`, also in a cluster of
short options.

The OpenCode map cannot examine the words of a command. It permits `mktemp`
only as `mktemp` and `mktemp -d`, and it has no `file` rule.

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
import tempfile
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

MKTEMP_TEMPLATE = "A mktemp template that is not a temp path makes a file outside a temp directory."
MKTEMP_DIRECTORY = "mktemp makes its file in the directory of -p or --tmpdir"
MKTEMP_SLASH = "After -p, --tmpdir or -t, mktemp makes the template in that directory or in the system temp directory"
FILE_COMPILE = "compiles a magic file and writes it"


def _rendered(render) -> tuple[dict, str]:
    [agent] = parse_declaration(DECLARATION)
    fm, body, err = split_frontmatter(render(agent, load_tiers())["stack-verifier.md"])
    assert err is None, err
    return fm, body


def _hook() -> str:
    [bash] = [entry for entry in _rendered(render_claude_code)[0]["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
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


#: The fixed temp prefixes of the guard, without a /private prefix.
TEMP_TEXT = ("/tmp/", "/var/folders/")


def _without_private(path: str | Path) -> str:
    text = str(path)
    return text[len("/private"):] if text.startswith("/private/") else text


@pytest.fixture
def temp_base(tmp_path):
    """A directory whose path starts with a fixed temp prefix as text, after a /private prefix is removed.

    The pytest temp directory has such a path on macOS and on a Linux CI host. A
    Nix build sandbox gives /build, so the directory then goes in /tmp.
    """
    base = Path(os.path.realpath(tmp_path))
    made = None
    if not _without_private(base).startswith(TEMP_TEXT):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = base = Path(os.path.realpath(tempfile.mkdtemp(dir="/tmp")))
    yield base
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


def _guard(command: str, shell: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the hook input and of the process."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", HOOK], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True, cwd=cwd)


def _denied(r: subprocess.CompletedProcess, shell: str, command: str, reason: str) -> None:
    assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
    assert f"[{command}]" in r.stderr, (shell, command, r.stderr)
    assert reason in r.stderr, (shell, command, r.stderr)


def _permitted(r: subprocess.CompletedProcess, shell: str, command: str) -> None:
    assert r.returncode == 0, (shell, command, r.stderr)


# --- mktemp ---------------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "mktemp /Users/x/fXXXXXX",
        "mktemp -d /Users/x/dXXXXXX",
        "mktemp -q -u /Users/x/fXXXXXX",
        "mktemp '/Users/x/fXXXXXX'",
        # A relative template is in the working directory, which is in the git tree.
        "mktemp fXXXXXX",
        "mktemp ./fXXXXXX",
        "mktemp /tmp/../Users/x/fXXXXXX",
        "mktemp /tmp/aXXXXXX /Users/x/bXXXXXX",
        # The output goes to /dev/null, and mktemp still makes the file of the template.
        "mktemp /Users/x/fXXXXXX>/dev/null",
        "mktemp /Users/x/fXXXXXX 2>/dev/null",
        # macOS mktemp makes $TMPDIR/x.<random> for -t x, and also the file of each template.
        "mktemp -t x fXXXXXX",
        "mktemp -t x /Users/x/fXXXXXX",
        "mktemp fXXXXXX -t",
        # macOS mktemp puts the word after -t after $TMPDIR/, so a .. component goes out of it.
        "mktemp -t ./../../../../Users/x/f",
        "mktemp -t./../../../../Users/x/f",
        "mktemp -dt ../../../../Users/x/f",
        "mktemp -t -/../../../../Users/x/f",
        # After --, each word is a template, also a word with a hyphen.
        "mktemp -- -dXXXXXX",
        "mktemp -t -- -dXXXXXX",
        # The shell expands these words to a path that the guard cannot read.
        "mktemp -t ~",
        "mktemp -t $HOME",
        "mktemp $HOME/fXXXXXX",
        "mktemp -p /tmp a/../../Users/x/fXXXXXX",
        "env mktemp /Users/x/fXXXXXX",
        "nix develop -c mktemp /Users/x/fXXXXXX",
    ],
)
def test_a_mktemp_template_outside_a_temp_directory_is_denied(repo, shell, command):
    # Each command makes a file outside a temp directory, or can make one.
    # Before #161, the guard permitted each one as the read "mktemp".
    _denied(_guard(command, shell, repo), shell, command, MKTEMP_TEMPLATE)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "mktemp -p /Users/x",
        "mktemp -p /Users/x fXXXXXX",
        "mktemp -d -p /Users/x",
        "mktemp -dp /Users/x",
        "mktemp -p/Users/x",
        "mktemp -dp/Users/x",
        "mktemp --tmpdir=/Users/x",
        "mktemp --tmp=/Users/x",
        "mktemp -p '/Users/x'",
        "mktemp -p ..",
        "mktemp -p .",
        "mktemp -p $HOME",
        "mktemp -p /tmp/../Users/x",
        # macOS mktemp reads -p as the prefix of -t, and /Users/x as a template; GNU mktemp reads -p /Users/x.
        "mktemp -t -p /Users/x",
    ],
)
def test_a_mktemp_directory_outside_a_temp_directory_is_denied(repo, shell, command):
    _denied(_guard(command, shell, repo), shell, command, MKTEMP_DIRECTORY)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("form", ["mktemp -p {repo}", "mktemp {repo}/fXXXXXX", "mktemp --tmpdir={repo}/src"])
def test_a_mktemp_file_in_a_git_tree_is_denied_also_in_a_temp_directory(temp_base, git_identity, shell, form):
    # The repository is in a temp directory. A file that mktemp makes in it is
    # a write in the tree.
    repo = temp_base / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    command = form.format(repo=_without_private(repo))
    r = _guard(command, shell, repo)
    assert r.returncode == 2, (shell, command, r.stderr)
    assert "git toplevel" in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_mktemp_p_needs_a_path_argument(repo, shell):
    r = _guard("mktemp -p", shell, repo)
    _denied(r, shell, "mktemp -p", "The option -p needs a path argument.")


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "mktemp",
        "mktemp -d",
        "mktemp -q",
        "mktemp -u",
        "mktemp /tmp/xXXXXXX",
        "mktemp -d /tmp/xXXXXXX",
        "mktemp '/tmp/xXXXXXX'",
        "mktemp /private/tmp/xXXXXXX",
        "mktemp /var/folders/xXXXXXX",
        "mktemp $TMPDIR/xXXXXXX",
        "mktemp /tmp/aXXXXXX /tmp/bXXXXXX",
        "mktemp -t x",
        "mktemp -d -t x",
        "mktemp -dt x",
        "mktemp -tx",
        "mktemp -t",
        "mktemp -d -t delegate-161.XXXXXX",
        "mktemp -p /tmp",
        "mktemp -p /tmp xXXXXXX",
        "mktemp -p /tmp a b",
        "mktemp -dp /tmp",
        "mktemp -p/tmp",
        "mktemp -p $TMPDIR xXXXXXX",
        "mktemp --tmpdir",
        "mktemp --tmpdir xXXXXXX",
        "mktemp --tmpdir=/tmp",
        "mktemp --tmpdir=/tmp xXXXXXX",
        "mktemp --directory --tmpdir=/tmp",
        "mktemp --suffix=.txt -t xXXXXXX",
        "mktemp -d 2>/dev/null",
        "mktemp -d > /dev/null",
        "mktemp -d | head -1",
        "env mktemp -d",
        "ls && mktemp -d",
    ],
)
def test_a_mktemp_file_in_a_temp_directory_is_permitted(repo, shell, command):
    # The guard reads the text only. It does not run the command, so it makes no file.
    _permitted(_guard(command, shell, repo), shell, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["mktemp fXXXXXX", "mktemp app/fXXXXXX", "mktemp -d dXXXXXX"])
def test_a_relative_mktemp_template_counts_from_a_working_directory_in_no_git_tree(temp_base, shell, command):
    # A relative template is in the working directory. In a temp copy with no
    # .git entry, that is a temp path, as for each other write target (#159).
    copy = temp_base / "copy"
    (copy / "app").mkdir(parents=True)
    _permitted(_guard(command, shell, copy), shell, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "form",
    [
        "mktemp -p {par} wt/fXXXXXX",
        "mktemp -p {par} wt/src/fXXXXXX",
        "mktemp -dp {par} wt/dXXXXXX",
        "mktemp -p{par} wt/fXXXXXX",
        "mktemp --tmpdir={par} wt/src/fXXXXXX",
        "mktemp --tmp={par} wt/fXXXXXX",
        "mktemp -p {par} -- wt/fXXXXXX",
        # GNU mktemp and macOS mktemp both read -p after the template.
        "mktemp wt/fXXXXXX -p {par}",
        "mktemp wt/fXXXXXX --tmpdir={par}",
        # macOS mktemp puts an absolute template after the directory of -p too: {par}//tmp/x.
        "mktemp -p {par} /tmp/xXXXXXX",
        # --tmpdir with no value and -t put the name in $TMPDIR, which can hold a git tree.
        "mktemp --tmpdir copy/fXXXXXX",
        "mktemp -t copy/f",
        "mktemp -dt copy/f",
        "mktemp -tcopy/f",
    ],
)
def test_a_mktemp_template_with_a_slash_after_a_directory_option_is_denied(temp_base, git_identity, shell, form):
    # The working directory is a temp copy in no git tree, as the proof copy of
    # a verifier (#153). After -p, --tmpdir or -t, mktemp makes the template in
    # that directory or in $TMPDIR, not in the working directory. A "/" in the
    # template can go into a git tree there: GNU mktemp made {par}/sub/f<random>
    # for "-p {par} sub/fXXXXXX", and macOS mktemp made $TMPDIR/sub/f.<random>
    # for "-t sub/f". Before this fix, the guard tested the template against
    # the working directory, and permitted each form.
    par = temp_base / "par"
    (par / "wt" / "src").mkdir(parents=True)
    git(par / "wt", "init", "-q", "-b", "main")
    copy = temp_base / "copy"
    (copy / "wt" / "src").mkdir(parents=True)
    command = form.format(par=_without_private(par))
    _denied(_guard(command, shell, copy), shell, command, MKTEMP_SLASH)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "form",
    ["mktemp -p {par} fXXXXXX", "mktemp -d -p {par} dXXXXXX", "mktemp --tmpdir={par} fXXXXXX", "mktemp -t f"],
)
def test_a_mktemp_name_with_no_slash_after_a_directory_option_is_permitted(temp_base, git_identity, shell, form):
    # The file is {par}/f<random> or $TMPDIR/f.<random>: next to the git tree, not in it.
    par = temp_base / "par"
    (par / "wt").mkdir(parents=True)
    git(par / "wt", "init", "-q", "-b", "main")
    copy = temp_base / "copy"
    copy.mkdir()
    command = form.format(par=_without_private(par))
    _permitted(_guard(command, shell, copy), shell, command)


# --- file -C ------------------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,option",
    [
        ("file -C -m a.txt", "-C"),
        ("file -C", "-C"),
        ("file -m a.txt -C", "-C"),
        ("file -bC -m a.txt", "-bC"),
        ("file -Cm a.txt", "-Cm"),
        ("file '-C' -m a.txt", "-C"),
        ("file -\\C -m a.txt", "-C"),
        ("file --compile -m a.txt", "--compile"),
        ("file --comp -m a.txt", "--comp"),
        ("file --co -m a.txt", "--co"),
        ("env file -C -m a.txt", "-C"),
        ("nix develop -c file -C -m a.txt", "-C"),
    ],
)
def test_file_compile_is_denied_and_named(repo, shell, command, option):
    # file -C writes a compiled magic file, a.txt.mgc, in the working directory.
    r = _guard(command, shell, repo)
    _denied(r, shell, command, FILE_COMPILE)
    assert f"The file option {option} " in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "file a.txt",
        "file",
        "file -b a.txt",
        "file --mime-type a.txt",
        "file -m Custom.magic a.txt",
        # -m takes the rest of the word: the magic file "C".
        "file -mC a.txt",
        "file -F, a.txt",
        "file -L src/Config.py",
        "file a.txt | grep text",
    ],
)
def test_file_without_compile_is_a_read(repo, shell, command):
    _permitted(_guard(command, shell, repo), shell, command)


# --- The OpenCode map -------------------------------------------------------------------


def test_the_opencode_map_permits_mktemp_only_alone_and_has_no_file_rule():
    # A pattern cannot examine a template, the directory of -p or a cluster of
    # short options, so the map permits only the two forms with no argument.
    fm, body = _rendered(render_opencode)
    perm = fm["permission"]
    for command in ("mktemp", "mktemp -d"):
        assert opencode_bash_action(perm, command) == "allow", command
    for command in (
        "mktemp /Users/x/fXXXXXX",
        "mktemp -p /Users/x",
        "mktemp -t x fXXXXXX",
        "mktemp -d /Users/x/dXXXXXX",
        "file -C -m a.txt",
        "file --compile -m a.txt",
        # The limit: the map also denies each form that the Claude Code guard permits.
        "mktemp /tmp/xXXXXXX",
        "file a.txt",
    ):
        assert opencode_bash_action(perm, command) == "deny", command
    assert not [rule for rule in perm["bash"] if rule == "file" or rule.startswith("file ")], perm
    # The rendered verifier states the limit.
    assert "no `file` rule" in body, body
    assert "`mktemp -d`" in body, body
