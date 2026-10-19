"""The verifier guards deny a write inside the git toplevel of their working directory, also in a temp directory (#106).

On macOS, a worktree from `mktemp -d` is under `/var/folders` (a link to
`/private/var/folders`), and each guard permits a write to a temp path. So a
verifier could write to the worktree that it reviews. Each guard now finds the
git toplevel of the working directory of the hook (the `cwd` field of the hook
input, or else the working directory of the process), and denies a write
target in that tree. The guards compare the paths after they remove a
`/private` prefix on both sides. A write to another temp path stays permitted.

Each case uses a real git repository and a real git worktree in a temp
directory, renders the example declaration, and runs the rendered hook in a
shell, as the harness does.
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

#: The shells in which the Bash guard behaves as its table says: bash, and macOS /bin/sh.
GUARD_SHELLS = ("bash", "/bin/sh")

#: Each shell that runs the Bash guard. zsh is a third shell. The guard turns on sh emulation under zsh, so that zsh
#: divides a segment into words as bash does (#113; see the limits in skills/agent-definitions/SKILL.md).
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


def _without_private(path: str) -> str:
    return path[len("/private"):] if path.startswith("/private/") else path


class Tree:
    """A git worktree in a temp directory, and the forms of a path in it.

    `base` is the real path of the temp directory that holds the worktree. On
    macOS it starts with /private/var/folders. `TMPDIR` of the hook is `base`.
    """

    def __init__(self, base: Path):
        self.base = base
        main = base / "main"
        main.mkdir()
        git(main, "init", "-q", "-b", "main")
        (main / "app.py").write_text("print('hi')\n")
        git(main, "add", "app.py")
        git(main, "commit", "-q", "-m", "seed")
        self.worktree = base / "wt"
        git(main, "worktree", "add", "-q", "-b", "feature", str(self.worktree))
        (self.worktree / "src").mkdir()

    def forms(self, relative: str) -> list[str]:
        """The path `relative` in the worktree, as text without /private, with /private, and with $TMPDIR."""
        plain = _without_private(str(self.worktree / relative))
        return [plain, "/private" + plain, "$TMPDIR/" + str((self.worktree / relative).relative_to(self.base))]

    def env(self) -> dict[str, str]:
        return {**os.environ, "TMPDIR": f"{self.base}/"}


@pytest.fixture
def tree(tmp_path, git_identity):
    """A worktree whose path starts with a fixed temp prefix as text, after a /private prefix is removed.

    The pytest temp directory has such a path on macOS and on a Linux CI
    host. A Nix build sandbox gives /build, so the tree then goes in /tmp.
    """
    base = Path(os.path.realpath(tmp_path))
    made = None
    if not _without_private(str(base)).startswith(TEMP_TEXT):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = base = Path(os.path.realpath(tempfile.mkdtemp(dir="/tmp")))
    yield Tree(base)
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


def _payload(tool: str, tool_input: dict, cwd: str | None) -> str:
    body: dict = {"tool_name": tool, "tool_input": tool_input}
    if cwd is not None:
        body["cwd"] = cwd
    return json.dumps(body)


def _bash(tree: Tree, command: str, shell: str = "bash", cwd: str | None = None, process_cwd: Path | None = None):
    """Run the Bash guard on `command`. `cwd` goes in the hook input; `process_cwd` is the directory of the process."""
    argv, executable = [shutil.which(shell) or shell, "-c", HOOKS["Bash"]], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(
        argv, executable=executable, input=_payload("Bash", {"command": command}, cwd), capture_output=True, text=True,
        env=tree.env(), cwd=process_cwd or tree.base,
    )


def _write(tree: Tree, file_path: str, cwd: str | None = None, process_cwd: Path | None = None):
    return subprocess.run(
        ["bash", "-c", HOOKS["Write"]], input=_payload("Write", {"file_path": file_path, "content": "{}"}, cwd),
        capture_output=True, text=True, env=tree.env(), cwd=process_cwd or tree.base,
    )


def _assert_denied_for_the_toplevel(result, command: str, shell: str) -> None:
    """The guard denies `command` and names it. Under bash and /bin/sh the reason is the git toplevel."""
    assert result.returncode == 2, (command, result.stderr)
    if "\\" not in command:
        # The deny message comes from echo, and the echo of macOS /bin/sh and of zsh reads a backslash escape.
        assert f"[{command}]" in result.stderr, (command, result.stderr)
    if shell in GUARD_SHELLS:
        assert "git toplevel" in result.stderr, (command, result.stderr)


def _bash_writes(path: str) -> list[str]:
    """The write commands of the acceptance, each on `path`."""
    return [f"cp /tmp/x {path}", f"echo x > {path}", f"tee {path}", f"rm {path}", f"mkdir {path}", f"touch {path}"]


# --- The Write guard ----------------------------------------------------------


def test_the_write_guard_denies_a_file_of_the_worktree_in_each_form_of_the_path(tree):
    for file_path in tree.forms("src/app.py"):
        result = _write(tree, file_path, cwd=str(tree.worktree))

        assert result.returncode == 2, (file_path, result.stderr)
        assert file_path in result.stderr


def test_the_write_guard_names_the_git_toplevel_when_it_denies_a_temp_path(tree):
    plain, private, _variable = tree.forms("src/app.py")
    for file_path in (plain, private):
        result = _write(tree, file_path, cwd=str(tree.worktree))

        assert result.returncode == 2, (file_path, result.stderr)
        assert "git toplevel" in result.stderr


def test_the_write_guard_takes_the_working_directory_of_the_process_when_the_input_has_no_cwd(tree):
    result = _write(tree, tree.forms("app.py")[0], process_cwd=tree.worktree / "src")

    assert result.returncode == 2, result.stderr
    assert "git toplevel" in result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        pytest.param("verdict.json", id="a-file-beside-the-worktree"),
        pytest.param("wt2/verdict.json", id="a-directory-whose-name-starts-with-the-name-of-the-worktree"),
        pytest.param("proof/src/app.py", id="a-temp-copy"),
    ],
)
def test_the_write_guard_permits_a_temp_path_outside_the_worktree(tree, relative):
    for file_path in (_without_private(str(tree.base / relative)), str(tree.base / relative)):
        result = _write(tree, file_path, cwd=str(tree.worktree / "src"))

        assert result.returncode == 0, (file_path, result.stderr)


def test_the_write_guard_permits_the_verdict_of_a_run_verifier_that_works_in_its_copy(tree, git_identity):
    # The engine clones the branch into <scratch>/copy and asks for the verdict at <scratch>/verdict.json.
    scratch = tree.base / "delegate-verify-x"
    scratch.mkdir()
    git(tree.base, "clone", "-q", str(tree.base / "main"), str(scratch / "copy"))

    result = _write(tree, str(scratch / "verdict.json"), cwd=str(scratch / "copy"))

    assert result.returncode == 0, result.stderr


# --- The Bash guard -----------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_denies_each_write_command_on_a_path_of_the_worktree_in_each_form(tree, shell):
    for form in tree.forms("src/app.py"):
        for command in _bash_writes(form):
            result = _bash(tree, command, shell, cwd=str(tree.worktree))

            assert result.returncode == 2, (command, result.stderr)
            assert f"[{command}]" in result.stderr


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_names_the_git_toplevel_when_it_denies_a_temp_path(tree, shell):
    plain, _private, variable = tree.forms("src/app.py")
    for form in (plain, variable, f"'{plain}'", f'"{variable}"'):
        for command in _bash_writes(form):
            _assert_denied_for_the_toplevel(_bash(tree, command, shell, cwd=str(tree.worktree)), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_takes_the_working_directory_of_the_process_when_the_input_has_no_cwd(tree, shell):
    command = f"touch {tree.forms('app.py')[0]}"

    _assert_denied_for_the_toplevel(_bash(tree, command, shell, process_cwd=tree.worktree / "src"), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_denies_a_spelling_that_the_shell_reads_as_a_path_of_the_worktree(tree, shell):
    plain = _without_private(str(tree.worktree))
    head, name = plain.rsplit("/", 1)
    for target in (
        f"{plain}",  # the toplevel itself
        f"{plain}/",
        f"{plain}/.",
        f"{head}//{name}/src/app.py",
        f"{head}/./{name}/src/app.py",
        f"{head}/'{name}'/src/app.py",
        f"{head}/{name[:1]}\\{name[1:]}/src/app.py",
    ):
        command = f"rm -rf {target}"
        _assert_denied_for_the_toplevel(_bash(tree, command, shell, cwd=str(tree.worktree)), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_bash_guard_denies_a_removal_of_a_directory_that_holds_the_worktree(tree, shell):
    base = _without_private(str(tree.base))
    for command in (f"rm -rf {base}", f"mv {base} /tmp/elsewhere", f"rm -rf {base}/*", f"rm -rf {base}/w?", f"rm -rf {base}/{{wt,x}}"):
        _assert_denied_for_the_toplevel(_bash(tree, command, shell, cwd=str(tree.worktree)), command, shell)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_the_bash_guard_permits_a_write_to_a_temp_path_outside_the_worktree(tree, shell):
    base = _without_private(str(tree.base))
    for path in (f"{base}/proof/src/app.py", f"{base}/wt2/app.py", "$TMPDIR/proof/app.py", f"{base}/proof/*.py"):
        for command in _bash_writes(path):
            result = _bash(tree, command, shell, cwd=str(tree.worktree))

            assert result.returncode == 0, (command, result.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_the_bash_guard_permits_a_copy_of_the_worktree_and_a_cd_to_it(tree, shell):
    plain = _without_private(str(tree.worktree))
    base = _without_private(str(tree.base))
    for command in (f"cp -R {plain} {base}/proof", f"cd {plain}", f"cd {plain} && git status", f"cat {plain}/app.py"):
        result = _bash(tree, command, shell, cwd=str(tree.worktree))

        assert result.returncode == 0, (command, result.stderr)


@pytest.mark.parametrize("shell", GUARD_SHELLS)
def test_the_bash_guard_permits_a_temp_write_outside_each_git_tree_when_the_working_directory_is_in_no_repository(tree, shell):
    # A write into the worktree from here is a deny since #125: see tests/test_verifier_guard_target_tree.py.
    outside = tree.base / "plain"
    outside.mkdir()
    for command in _bash_writes(_without_private(str(outside / "src" / "app.py"))):
        result = _bash(tree, command, shell, cwd=str(outside))

        assert result.returncode == 0, (command, result.stderr)
