"""A relative write target counts from the working directory of the hook, as a relative cd does (#159).

A verifier that works in its proof copy (#153) writes relative paths. The guard
of 1.8.0 resolved a relative path only for `cd` (#131), and it tested the path
of a write as text, so `touch app/x` in a temp copy with no `.git` entry was a
deny. The guard now puts the working directory of the hook input before a
relative write target, and then applies each rule of the absolute form to the
result: a temp path, not inside a git tree, not holding one, and no `..`
component.

Each case uses a real git repository, a real git worktree and a copy with no
`.git` entry in a temp directory, renders the example declaration, and runs the
rendered hook in bash, /bin/sh and zsh, as the harness does.
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

#: The fixed temp prefixes of the guard, without a /private prefix.
TEMP_TEXT = ("/tmp/", "/var/folders/")


def _hook() -> str:
    [agent] = load_declaration(EXAMPLE)
    fm, _body, err = split_frontmatter(render_claude_code(agent, load_tiers())["java-spring-verifier.md"])
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


HOOK = _hook()


def _without_private(path: str | Path) -> str:
    text = str(path)
    return text[len("/private"):] if text.startswith("/private/") else text


class Trees:
    """A git repository and its worktree in a temp directory, and a copy of the worktree with no `.git` entry.

    `base` is the real path of the temp directory, and the `TMPDIR` of the hook.
    The repository is `<base>/main`, the worktree is `<base>/wt`, and the copy is
    `<base>/proof`. Each of them holds the directory `app`.
    """

    def __init__(self, base: Path):
        self.base = base
        self.main = base / "main"
        (self.main / "app").mkdir(parents=True)
        git(self.main, "init", "-q", "-b", "main")
        (self.main / "app" / "a.py").write_text("print('hi')\n")
        git(self.main, "add", "app/a.py")
        git(self.main, "commit", "-q", "-m", "seed")
        self.worktree = base / "wt"
        git(self.main, "worktree", "add", "-q", "-b", "feature", str(self.worktree))
        assert (self.worktree / ".git").is_file(), "a worktree has a .git pointer file"
        self.copy = base / "proof"
        shutil.copytree(self.worktree, self.copy, symlinks=True)
        (self.copy / ".git").unlink()

    def env(self) -> dict[str, str]:
        return {**os.environ, "TMPDIR": f"{self.base}/"}


@pytest.fixture
def trees(tmp_path, git_identity):
    """Trees whose paths start with a fixed temp prefix as text, after a /private prefix is removed.

    The pytest temp directory has such a path on macOS and on a Linux CI host. A
    Nix build sandbox gives /build, so the trees then go in /tmp.
    """
    base = Path(os.path.realpath(tmp_path))
    made = None
    if not _without_private(base).startswith(TEMP_TEXT):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = base = Path(os.path.realpath(tempfile.mkdtemp(dir="/tmp")))
    yield Trees(base)
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


def _guard(trees: Trees, command: str, shell: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the hook input and of the process."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", HOOK], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(
        argv, executable=executable, input=payload, capture_output=True, text=True, env=trees.env(), cwd=cwd,
    )


def _assert_permitted(result: subprocess.CompletedProcess, command: str, shell: str) -> None:
    assert result.returncode == 0, (shell, command, result.stderr)


def _assert_denied(result: subprocess.CompletedProcess, command: str, shell: str) -> None:
    assert result.returncode == 2, (shell, command, result.stderr)
    assert "verifier guard" in result.stderr, (shell, command, result.stderr)


# --- A relative write in a temp copy with no .git entry ------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "touch app/x",
        "echo x > app/x",
        "echo x >app/x",
        "echo x >> app/a.py",
        "touch ./app/x",
        "touch 'app/x'",
        'touch "app/x"',
        "mkdir -p app/new",
        "tee app/x",
        "rm app/a.py",
        "rm -rf app",
        "mv app/a.py app/b.py",
        "cp /etc/hosts app/x",
        "touch x",
        "git status > status.txt",
    ],
)
def test_a_relative_write_in_a_temp_copy_with_no_git_entry_is_permitted(trees, shell, command):
    # The report of #159: in the proof copy, `touch app/x` was a deny.
    assert not (trees.copy / ".git").exists()
    _assert_permitted(_guard(trees, command, shell, trees.copy), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_and_an_absolute_write_to_the_same_temp_path_get_the_same_verdict(trees, shell):
    plain = _without_private(trees.copy)
    for command in ("touch app/x", f"touch {plain}/app/x", "echo x > app/x", f"echo x > {plain}/app/x"):
        _assert_permitted(_guard(trees, command, shell, trees.copy), command, shell)
    for command in ("touch ../wt/app/x", f"touch {_without_private(trees.worktree)}/app/x"):
        _assert_denied(_guard(trees, command, shell, trees.copy), command, shell)


# --- A relative write in a git tree --------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["touch app/x", "echo x > app/x", "rm app/a.py", "touch x", "mkdir app/new"])
def test_a_relative_write_in_a_git_worktree_in_a_temp_directory_is_denied(trees, shell, command):
    # Each relative path with no .. component is in the git toplevel of the working directory, so it is a deny.
    _assert_denied(_guard(trees, command, shell, trees.worktree), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_write_in_a_subdirectory_of_a_git_worktree_is_denied(trees, shell):
    _assert_denied(_guard(trees, "touch x", shell, trees.worktree / "app"), "touch x", shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_write_into_a_git_tree_in_the_copy_is_denied(trees, shell):
    # The copy holds a nested repository. A write into it, or a removal of a directory that holds it, is a deny.
    nested = trees.copy / "vendor" / "lib"
    nested.mkdir(parents=True)
    git(nested, "init", "-q", "-b", "main")
    for command in ("touch vendor/lib/x", "echo x > vendor/lib/x", "rm -rf vendor", "rm -rf .", "mv vendor gone"):
        result = _guard(trees, command, shell, trees.copy)

        _assert_denied(result, command, shell)
        if shell != "zsh":
            assert f"git toplevel [{_without_private(nested)}]" in result.stderr, (shell, command, result.stderr)


# --- A relative write that can leave the temp copy -----------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "touch ../../x",
        "touch ../x",
        "touch app/../../x",
        "echo x > ../../x",
        "touch ~/x",
        "touch =ls",
        "touch a*",
        "touch ap?/x",
        "touch [a]pp/x",
        "touch {app,..}/x",
        "touch $HOME/x",
        'touch "a b"',
        "touch a\\ b",
    ],
)
def test_a_relative_write_that_can_leave_the_temp_copy_is_denied(trees, shell, command):
    _assert_denied(_guard(trees, command, shell, trees.copy), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "cd {worktree} && touch app/x",
        "cd {worktree} && echo x > app/x",
        "cd {worktree}; rm app/a.py",
        "cd app && touch f",
    ],
)
def test_a_relative_write_after_a_cd_in_the_same_command_is_denied(trees, shell, command):
    # The guard resolves a relative path against the working directory of the hook, and a cd changes the working
    # directory of each later segment. So after a cd, a relative write target stays a deny.
    command = command.format(worktree=_without_private(trees.worktree))
    _assert_denied(_guard(trees, command, shell, trees.copy), command, shell)


needs_a_root_outside_each_git_tree = pytest.mark.skipif(
    Path("/.git").exists(), reason="the root directory holds a .git entry"
)


@needs_a_root_outside_each_git_tree
@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_write_outside_a_temp_path_is_denied_when_the_working_directory_is_in_no_git_tree(trees, shell):
    # From /, the relative path "etc/x" is the path /etc/x. It is outside a temp directory.
    for command in ("touch etc/x", "echo x > etc/x"):
        _assert_denied(_guard(trees, command, shell, Path("/")), command, shell)


@needs_a_root_outside_each_git_tree
@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_write_from_the_root_directory_into_a_git_tree_in_a_temp_directory_is_denied(trees, shell):
    # From /, the relative path of the repository is a temp path. The guard must find the git tree in it.
    main = _without_private(trees.main).lstrip("/")
    worktree = _without_private(trees.worktree).lstrip("/")
    for command in (f"rm -rf {main}", f"touch {main}/app/x", f"echo x > {worktree}/app/x", f"rm -rf {main}/.."):
        _assert_denied(_guard(trees, command, shell, Path("/")), command, shell)
    command = f"touch {_without_private(trees.copy).lstrip('/')}/app/x"
    _assert_permitted(_guard(trees, command, shell, Path("/")), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_write_from_a_temp_directory_that_holds_the_trees_is_denied_only_in_a_tree(trees, shell):
    # The working directory holds the repository, the worktree and the copy, and is in no git tree itself.
    for command in ("rm -rf main", "touch wt/app/x", "echo x > main/app/a.py", "rm -rf .", "mv wt gone"):
        _assert_denied(_guard(trees, command, shell, trees.base), command, shell)
    for command in ("touch proof/app/x", "echo x > proof/app/x", "rm -rf proof", "mkdir new"):
        _assert_permitted(_guard(trees, command, shell, trees.base), command, shell)
