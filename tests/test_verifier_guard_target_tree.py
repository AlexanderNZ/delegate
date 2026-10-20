"""The verifier guards deny a write into a git tree that the target names, not only the tree of the working directory (#125).

The rule of #106 protected only the git toplevel of the working directory. The
break-a-copy recipe goes to a copy that has no `.git` entry, and Claude Code
keeps that working directory for the next Bash call. Then the guards found no
toplevel, and a write into the original worktree was permitted. Each guard now
also looks for a `.git` entry (a directory, or the pointer file of a worktree)
from each write target upward, and denies a target in such a tree. It also
denies a target that holds such a tree.

Each case uses a real git repository and a real git worktree in a temp
directory, renders the example declaration, and runs the rendered hook in
bash, /bin/sh and zsh, as the harness does.
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
from delegate.definitions.render import TREE_SEARCH_LIMIT, render_claude_code
from delegate.definitions.validate import split_frontmatter
from delegate.shared.tiers import load_tiers

from .support import git

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "java-spring.toml"

#: The shells in which the deny message of the Bash guard comes through echo with no change: bash, and macOS /bin/sh.
GUARD_SHELLS = ("bash", "/bin/sh")

#: Each shell that runs a guard.
SHELLS = (
    *GUARD_SHELLS,
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

#: The fixed temp prefixes of the guards, without a /private prefix.
TEMP_TEXT = ("/tmp/", "/var/folders/")


def _hooks() -> dict[str, str]:
    [agent] = load_declaration(EXAMPLE)
    fm, _body, err = split_frontmatter(render_claude_code(agent, load_tiers())["java-spring-verifier.md"])
    assert err is None, err
    return {entry["matcher"]: entry["hooks"][0]["command"] for entry in fm["hooks"]["PreToolUse"]}


HOOKS = _hooks()


def _without_private(path: str | Path) -> str:
    text = str(path)
    return text[len("/private"):] if text.startswith("/private/") else text


class Trees:
    """A git worktree in a temp directory, and a copy of it with no `.git` entry beside it.

    `base` is the real path of the temp directory. `TMPDIR` of the hook is
    `base`. The worktree is `<base>/hold/wt`, so `<base>/hold` holds a
    worktree. The copy is `<base>/x`, as step 1 of the recipe makes it.
    """

    def __init__(self, base: Path):
        self.base = base
        main = base / "main"
        main.mkdir()
        git(main, "init", "-q", "-b", "main")
        (main / "app.py").write_text("print('hi')\n")
        git(main, "add", "app.py")
        git(main, "commit", "-q", "-m", "seed")
        self.hold = base / "hold"
        self.hold.mkdir()
        self.worktree = self.hold / "wt"
        git(main, "worktree", "add", "-q", "-b", "feature", str(self.worktree))
        (self.worktree / "src").mkdir()
        assert (self.worktree / ".git").is_file(), "a worktree has a .git pointer file"
        self.copy = base / "x"
        subprocess.run(["bash", "-c", f"cp -R {self.worktree} {self.copy} && rm -rf {self.copy}/.git"], check=True)

    def forms(self, path: Path) -> list[str]:
        """`path` as text without /private, with /private, and with $TMPDIR."""
        plain = _without_private(path)
        return [plain, "/private" + plain, "$TMPDIR/" + path.relative_to(self.base).as_posix()]

    def env(self) -> dict[str, str]:
        return {**os.environ, "TMPDIR": f"{self.base}/"}


@pytest.fixture
def trees(tmp_path, git_identity):
    """Trees whose paths start with a fixed temp prefix as text, after a /private prefix is removed.

    The pytest temp directory has such a path on macOS and on a Linux CI
    host. A Nix build sandbox gives /build, so the trees then go in /tmp.
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


def _argv(shell: str, hook: str) -> tuple[list[str], str | None]:
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return argv, executable


def _bash(trees: Trees, command: str, shell: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the session and of the process."""
    argv, executable = _argv(shell, HOOKS["Bash"])
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    return subprocess.run(
        argv, executable=executable, input=payload, capture_output=True, text=True, env=trees.env(), cwd=cwd,
    )


def _write(trees: Trees, file_path: str, shell: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the Write guard on `file_path`, with `cwd` as the working directory of the session and of the process."""
    argv, executable = _argv(shell, HOOKS["Write"])
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "{}"}, "cwd": str(cwd)})
    return subprocess.run(
        argv, executable=executable, input=payload, capture_output=True, text=True, env=trees.env(), cwd=cwd,
    )


def _bash_writes(path: str) -> list[str]:
    """The write commands of #106, each on `path`."""
    return [f"cp /tmp/x {path}", f"echo x > {path}", f"tee {path}", f"rm {path}", f"mkdir {path}", f"touch {path}"]


def _is_temp_text(path: str) -> bool:
    """True when the Bash guard reads `path` as a temp path. `/private/var/folders` is not a temp prefix."""
    return path.startswith(("/tmp/", "/private/tmp/", "/var/folders/", "$TMPDIR"))


def _assert_denied_for(result: subprocess.CompletedProcess, shown: str, tree: Path, shell: str, named: bool = True) -> None:
    """The guard denies the call and names `shown`. When `named`, under bash and /bin/sh, it names the git tree `tree`."""
    assert result.returncode == 2, (shown, result.stderr)
    if "\\" not in shown:
        # The deny message comes from echo, and the echo of macOS /bin/sh and of zsh reads a backslash escape.
        assert f"[{shown}]" in result.stderr, (shown, result.stderr)
    if named and shell in GUARD_SHELLS:
        assert f"git toplevel [{_without_private(tree)}]" in result.stderr, (shown, result.stderr)


# --- A write into another worktree, from a copy with no .git entry -----------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_denies_a_write_into_a_worktree_when_the_working_directory_is_a_copy_with_no_git_entry(trees, shell):
    # The report of #125: after `cd /tmp/x`, `touch <original worktree>/foo` was permitted.
    for form in trees.forms(trees.worktree / "foo"):
        command = f"touch {form}"
        _assert_denied_for(_bash(trees, command, shell, trees.copy), command, trees.worktree, shell, _is_temp_text(form))
    for form in trees.forms(trees.worktree / "src" / "app.py"):
        for command in _bash_writes(form):
            result = _bash(trees, command, shell, trees.copy)
            _assert_denied_for(result, command, trees.worktree, shell, _is_temp_text(form))


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_denies_a_write_into_a_repository_when_the_working_directory_is_a_copy_with_no_git_entry(trees, shell):
    # The main checkout holds a .git directory, not a pointer file.
    main = trees.base / "main"
    for form in trees.forms(main / "app.py"):
        command = f"echo x > {form}"
        _assert_denied_for(_bash(trees, command, shell, trees.copy), command, main, shell, _is_temp_text(form))


@pytest.mark.parametrize("shell", SHELLS)
def test_the_write_guard_denies_a_file_of_a_worktree_when_the_working_directory_is_a_copy_with_no_git_entry(trees, shell):
    plain, private, _variable = trees.forms(trees.worktree / "src" / "app.py")
    for file_path in (plain, private):
        result = _write(trees, file_path, shell, trees.copy)

        assert result.returncode == 2, (file_path, result.stderr)
        assert file_path in result.stderr
        assert f"git toplevel [{_without_private(trees.worktree)}]" in result.stderr


# --- A write into the copy ------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_permits_a_write_into_the_copy(trees, shell):
    plain, _private, variable = trees.forms(trees.copy / "src" / "app.py")
    commands = [*_bash_writes(plain), *_bash_writes(variable), f"cd {_without_private(trees.copy)} && echo broken > {plain}"]
    commands += [f"rm -rf {_without_private(trees.copy)}/*", f"rm -rf {_without_private(trees.copy)}"]
    for command in commands:
        result = _bash(trees, command, shell, trees.copy)

        assert result.returncode == 0, (command, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_write_guard_permits_a_write_into_the_copy(trees, shell):
    plain, private, _variable = trees.forms(trees.copy / "src" / "app.py")
    for file_path in (plain, private):
        result = _write(trees, file_path, shell, trees.copy)

        assert result.returncode == 0, (file_path, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_guards_permit_a_write_beside_a_worktree_whose_name_starts_with_its_name(trees, shell):
    beside = _without_private(trees.hold / "wt2" / "app.py")

    assert _bash(trees, f"touch {beside}", shell, trees.copy).returncode == 0
    assert _write(trees, beside, shell, trees.copy).returncode == 0


# --- A target that holds a worktree ------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_denies_a_removal_of_a_directory_that_holds_a_worktree(trees, shell):
    hold = _without_private(trees.hold)
    base = _without_private(trees.base)
    for command in (
        f"rm -rf {hold}",
        f"rm -rf {hold}/",
        f"rm -rf $TMPDIR/hold",
        f"mv {hold} {base}/elsewhere",
        f"rm -rf {base}/ho*",
        f"rm -rf {base}/h?ld",
        f"cp -R /tmp/x {hold}",
    ):
        _assert_denied_for(_bash(trees, command, shell, trees.copy), command, trees.worktree, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_write_guard_denies_a_path_that_holds_a_worktree(trees, shell):
    for file_path in trees.forms(trees.hold)[:2]:
        result = _write(trees, file_path, shell, trees.copy)

        assert result.returncode == 2, (file_path, result.stderr)
        assert f"git toplevel [{_without_private(trees.worktree)}]" in result.stderr


# --- The verdict of a delegate run verifier ----------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_a_run_verifier_writes_its_verdict_from_its_copy_and_from_its_second_copy(trees, shell):
    # The engine clones the branch into <scratch>/copy and asks for the verdict at <scratch>/verdict.json. The brief
    # puts the second copy of a red proof at <scratch>/proof, with no .git entry.
    scratch = trees.base / "delegate-verify-x"
    scratch.mkdir()
    copy = scratch / "copy"
    git(trees.base, "clone", "-q", str(trees.base / "main"), str(copy))
    proof = scratch / "proof"
    make = f"cp -R {_without_private(copy)} {_without_private(proof)} && rm -rf {_without_private(proof)}/.git"
    assert _bash(trees, make, shell, copy).returncode == 0
    subprocess.run(["bash", "-c", make], check=True)
    verdict = _without_private(scratch / "verdict.json")
    printf = f"printf '%s\\n' '{{\"verdict\": \"ACCEPT\"}}' > {verdict}"
    for cwd in (copy, proof):
        bash = _bash(trees, printf, shell, cwd)
        write = _write(trees, verdict, shell, cwd)

        assert bash.returncode == 0, (str(cwd), bash.stderr)
        assert write.returncode == 0, (str(cwd), write.stderr)
    # The verifier copy stays read-only, also from the second copy.
    _assert_denied_for(_bash(trees, f"touch {_without_private(copy)}/app.py", shell, proof), f"touch {_without_private(copy)}/app.py", copy, shell)


# --- A directory that is too large to search ---------------------------------

#: The reason of a deny when the walk in a write target stops at its limit (#132). The deny message names the limit.
WALK_LIMIT_REASON = (
    f"read {TREE_SEARCH_LIMIT} directories and did not reach the end, so the guard cannot tell that the"
)


def _big_copy(trees: Trees) -> Path:
    """A temp copy with no git tree that holds more directories than the walk reads, as a copy with node_modules does."""
    big = trees.base / "copy-with-node-modules"
    for index in range(TREE_SEARCH_LIMIT + 1):
        (big / "node_modules" / f"package{index}").mkdir(parents=True)
    return big


@pytest.mark.parametrize("shell", SHELLS)
def test_the_deny_of_a_target_that_the_walk_could_not_search_names_the_limit_and_what_to_do(trees, shell):
    # The report of #132: a verifier could not rm -rf or mv its own temp copy, and the message did not tell it that
    # the walk stopped at its limit, or what to do.
    plain = _without_private(_big_copy(trees))
    for command in (f"rm -rf {plain}", f"mv {plain} {plain}-old"):
        result = _bash(trees, command, shell, trees.copy)

        assert result.returncode == 2, (command, result.stderr)
        assert Path(plain).is_dir()
        if shell in GUARD_SHELLS:
            assert f"The walk in the directory [{plain}] {WALK_LIMIT_REASON} write target holds no git tree." in (
                result.stderr
            ), result.stderr
            assert "Leave a large temp copy in place" in result.stderr, result.stderr
            assert "one directory at a time" in result.stderr, result.stderr
            # No source shows that the system removes temp directories (#160).
            assert "the system removes" not in result.stderr, result.stderr
    write = _write(trees, plain, shell, trees.copy)

    assert write.returncode == 2, write.stderr
    assert f"The walk in [{plain}] {WALK_LIMIT_REASON} file path holds no git tree." in write.stderr, write.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_the_guards_deny_a_target_that_holds_more_directories_than_the_search_reads(trees, shell):
    # The guards cannot tell that such a directory holds no git tree, so they fail closed for the directory itself. A
    # path in it stays permitted, because the search from that path upward finds no .git entry.
    big = trees.base / "big"
    for index in range(TREE_SEARCH_LIMIT + 1):
        (big / f"d{index}").mkdir(parents=True)
    plain = _without_private(big)
    for command in (f"rm -rf {plain}", f"rm -rf {plain}/*"):
        result = _bash(trees, command, shell, trees.copy)

        assert result.returncode == 2, (command, result.stderr)
        if shell in GUARD_SHELLS:
            assert WALK_LIMIT_REASON in result.stderr, result.stderr
    write = _write(trees, plain, shell, trees.copy)
    assert write.returncode == 2, write.stderr
    assert WALK_LIMIT_REASON in write.stderr
    # A pattern limits the walk to the directories that it can match: d1, d1<n>, d1<n><n> and d1<n><n><n>.
    for command in (f"touch {plain}/d1/app.py", f"ls {plain} && rm -rf {plain}/d1", f"rm -rf {plain}/d1*"):
        assert _bash(trees, command, shell, trees.copy).returncode == 0, command
    assert _write(trees, f"{plain}/d1/app.py", shell, trees.copy).returncode == 0
