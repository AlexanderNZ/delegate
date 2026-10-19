"""A verifier can go to a directory of its repository, and a `docker compose -f <file>` gate matches (#121).

Many repositories give their gates in the form `cd <dir> && <gate>`, for
example `cd api && ./gradlew test`. The guard of 1.6.0 permitted a `cd` only to
a temp path, so each verifier of such a stack was refused at its own gate. The
guard now permits `cd <dir>` also when `<dir>` is in the git toplevel of the
working directory, the toplevel of #106. A `cd` writes nothing, and each later
segment is examined on its own.

A gate matches as a prefix, so the gate `docker compose config` did not match
`docker compose -f compose.yml config`. The guard now removes each `-f <file>`
and `--file <file>` after `docker compose` before the test, as it removes
`-C <path>` after `git`. The subcommand after the options is still examined.

Each case uses a real git repository in a temp directory, renders a
declaration with these gates, and runs the rendered hook in bash, /bin/sh and
zsh, as the harness does.
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
from delegate.definitions.render import render_claude_code
from delegate.definitions.validate import split_frontmatter
from delegate.shared.tiers import load_tiers

from .support import git

#: Each shell that runs the Bash guard: bash, /bin/sh and zsh (a comment on #121 asks for zsh cases).
SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

GATES = ["./gradlew test", "docker compose config"]


def _hook() -> str:
    [agent] = parse_declaration(
        {"agents": {"stack": {"description": "A repository with stacks in subdirectories.", "gateCommands": GATES}}}
    )
    fm, _body, err = split_frontmatter(render_claude_code(agent, load_tiers())["stack-verifier.md"])
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


HOOK = _hook()


@pytest.fixture
def repo(tmp_path, git_identity) -> Path:
    """A git repository with the stack directories api/ and app/, and a directory beside it."""
    root = Path(os.path.realpath(tmp_path)) / "repo"
    for stack in ("api", "app/src"):
        (root / stack).mkdir(parents=True)
    git(root, "init", "-q", "-b", "main")
    (tmp_path / "outside").mkdir()
    return root


def _guard(command: str, shell: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the hook input and of the process."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", HOOK], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True, cwd=cwd)


def _assert_permitted(result: subprocess.CompletedProcess, command: str, shell: str) -> None:
    assert result.returncode == 0, (shell, command, result.stderr)


def _assert_denied(result: subprocess.CompletedProcess, command: str, shell: str) -> None:
    assert result.returncode == 2, (shell, command, result.stderr)
    assert "verifier guard" in result.stderr, (shell, command, result.stderr)


# --- cd to a directory of the repository ----------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "cd api && ./gradlew test",
        "cd api && ./gradlew test --info",
        "cd app/src && ./gradlew test",
        "cd ./api && ./gradlew test",
        "cd 'api' && ./gradlew test",
        "cd api/ && git status",
        "cd . && ./gradlew test",
        "cd api; ./gradlew test",
    ],
)
def test_a_cd_to_a_directory_of_the_repository_reaches_the_gate(repo, shell, command):
    _assert_permitted(_guard(command, shell, repo), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_cd_by_absolute_path_to_a_directory_of_the_repository_is_permitted(repo, shell):
    for target in (repo, repo / "api", repo / "app" / "src"):
        command = f"cd {target} && ./gradlew test"
        _assert_permitted(_guard(command, shell, repo / "app"), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "target",
    [
        "../outside",
        "..",
        "/etc",
        "/Users/x",
        "api/../..",
        "api/../../outside",
        "./..",
        "~",
        "~/src",
        "$HOME",
        "${HOME}",
        "a*",
        ".?",
        "{..,api}",
        "\\.\\.",
        "'..'",
        "=ls",
    ],
)
def test_a_cd_out_of_the_repository_is_denied(repo, shell, target):
    command = f"cd {target} && ./gradlew test"
    _assert_denied(_guard(command, shell, repo), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_cd_to_an_absolute_path_through_dot_dot_is_denied(repo, shell):
    for target in (f"{repo}/..", f"{repo}/api/../../outside", f"{repo}/../outside", f"{repo}/api/../.."):
        command = f"cd {target} && ./gradlew test"
        _assert_denied(_guard(command, shell, repo), command, shell)


#: This checkout, when it is a git repository outside each temp prefix. There the temp rule permits no `cd`, so
#: only the rule of the git toplevel can permit one.
CHECKOUT = Path(__file__).resolve().parents[1]
_TEMP_TEXT = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
needs_a_checkout_outside_temp = pytest.mark.skipif(
    not (CHECKOUT / ".git").exists() or str(CHECKOUT).startswith(_TEMP_TEXT),
    reason="this checkout is not a git repository outside each temp prefix",
)


@needs_a_checkout_outside_temp
@pytest.mark.parametrize("shell", SHELLS)
def test_a_cd_by_absolute_path_outside_temp_is_permitted_only_in_the_git_toplevel(shell):
    for target in (f"{CHECKOUT}", f"{CHECKOUT}/", f"{CHECKOUT}/src", f"{CHECKOUT}//src/./delegate", f"'{CHECKOUT}/tests'"):
        command = f"cd {target} && ./gradlew test"
        _assert_permitted(_guard(command, shell, CHECKOUT / "tests"), command, shell)
    for target in (f"{CHECKOUT}2", f"{CHECKOUT}x/src", f"{CHECKOUT}/..", f"{CHECKOUT}/src/../..", f"{CHECKOUT.parent}"):
        command = f"cd {target} && ./gradlew test"
        _assert_denied(_guard(command, shell, CHECKOUT / "tests"), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["cd /etc api", "cd api /etc", "cd api app", "cd repo /"])
def test_a_cd_with_two_path_arguments_is_denied(repo, shell, command):
    # bash 3.2 goes to the first path and ignores the second, and zsh reads two
    # paths as a text substitution in the working directory. Either one can
    # leave the repository, so the guard permits one path argument only.
    _assert_denied(_guard(command, shell, repo), command, shell)


#: The root directory: it is in no git tree, and it is not a temp path.
needs_a_root_outside_each_git_tree = pytest.mark.skipif(
    Path("/.git").exists(), reason="the root directory holds a .git entry"
)


@needs_a_root_outside_each_git_tree
@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_cd_is_denied_when_the_working_directory_is_in_no_repository_and_not_a_temp_path(shell):
    # From /, the relative path "etc" is the path /etc. It is outside a temp directory, and there is no git toplevel.
    command = "cd etc && ./gradlew test"
    _assert_denied(_guard(command, shell, Path("/")), command, shell)


@needs_a_root_outside_each_git_tree
@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_cd_from_the_root_directory_to_a_temp_path_is_permitted(shell):
    # From /, the relative path "tmp/proof" is the temp path /tmp/proof, as `cd /tmp/proof` is.
    for command in ("cd tmp/proof && ./gradlew test", "cd /tmp/proof && ./gradlew test"):
        _assert_permitted(_guard(command, shell, Path("/")), command, shell)


# --- A relative cd in a copy with no .git entry (#131) ---------------------------

#: The fixed temp prefixes of the guard, without a /private prefix.
_TEMP_PREFIXES = ("/tmp/", "/var/folders/")


def _without_private(path: str) -> str:
    return path[len("/private"):] if path.startswith("/private/") else path


@pytest.fixture
def copy(repo):
    """A copy of `repo` with no `.git` entry, in a directory whose path starts with a fixed temp prefix as text.

    This is step 2 of the break-a-copy recipe. The pytest temp directory has such a path on macOS and on a Linux CI
    host. A Nix build sandbox gives /build, so the copy then goes in /tmp.
    """
    base, made = Path(_without_private(str(repo.parent))), None
    if not str(base).startswith(_TEMP_PREFIXES):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = base = Path(_without_private(os.path.realpath(tempfile.mkdtemp(dir="/tmp"))))
    copy = base / "x"
    shutil.copytree(repo, copy, symlinks=True)
    shutil.rmtree(copy / ".git")
    yield copy
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "cd app && ./gradlew test",
        "cd app/src && ./gradlew test",
        "cd ./api && ./gradlew test",
        "cd 'api' && ./gradlew test",
        "cd api/ && ./gradlew test",
        "cd . && ./gradlew test",
        "cd api; docker compose -f compose.yml config",
    ],
)
def test_a_relative_cd_in_a_temp_copy_with_no_git_entry_reaches_the_gate(copy, shell, command):
    assert not (copy / ".git").exists()
    _assert_permitted(_guard(command, shell, copy), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_cd_and_an_absolute_cd_to_the_same_temp_path_get_the_same_verdict(copy, shell):
    for command in ("cd app && ./gradlew test", f"cd {copy}/app && ./gradlew test"):
        _assert_permitted(_guard(command, shell, copy), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "cd ../x && ./gradlew test",
        "cd .. && ./gradlew test",
        "cd app/../.. && ./gradlew test",
        "cd ~/x && ./gradlew test",
        "cd ~ && ./gradlew test",
        "cd =ls && ./gradlew test",
        "cd a* && ./gradlew test",
        "cd ap? && ./gradlew test",
        "cd [a]pp && ./gradlew test",
        "cd {app,..} && ./gradlew test",
        "cd $HOME && ./gradlew test",
        "cd api app && ./gradlew test",
        "cd app && echo x > f",
        "cd app && touch f",
        "cd app && ./gradlew test > out.log",
    ],
)
def test_a_relative_cd_in_a_temp_copy_with_no_git_entry_keeps_the_other_denies(copy, shell, command):
    _assert_denied(_guard(command, shell, copy), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_cd_deny_message_states_that_the_working_directory_is_in_no_git_tree(copy, shell):
    command = "cd ../x && ./gradlew test"
    result = _guard(command, shell, copy)
    _assert_denied(result, command, shell)
    assert f"The working directory [{copy}] is in no git tree" in result.stderr, (shell, result.stderr)
    assert "toplevel" not in result.stderr, (shell, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_the_cd_deny_message_names_the_git_toplevel_when_there_is_one(repo, shell):
    command = "cd ../outside && ./gradlew test"
    result = _guard(command, shell, repo / "api")
    _assert_denied(result, command, shell)
    # The guard names each path without a /private prefix.
    toplevel = _without_private(str(repo))
    assert f"outside the git toplevel {toplevel} of the working directory" in result.stderr, (shell, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "cd api && echo x > f",
        "cd api && echo x >> build.gradle",
        "cd api && touch f",
        "cd api && rm -rf src",
        "cd api && cp /tmp/x f",
        "cd api && git commit -m x",
        "cd api && ./gradlew test > out.log",
    ],
)
def test_a_write_after_a_cd_to_a_directory_of_the_repository_is_still_denied(repo, shell, command):
    _assert_denied(_guard(command, shell, repo), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_cd_to_a_temp_path_is_still_permitted(repo, shell):
    for command in ("cd /tmp/proof", "cd /tmp/proof && ./gradlew test", "cd $TMPDIR/proof"):
        _assert_permitted(_guard(command, shell, repo), command, shell)


# --- docker compose -f <file> -----------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "docker compose config",
        "docker compose -f compose.yml config",
        "docker compose --file compose.yml config",
        "docker compose -f compose.yml -f compose.override.yml config",
        "docker compose -f deploy/compose.yml config --quiet",
        "nix develop -c docker compose -f compose.yml config",
        "cd api && docker compose -f compose.yml config",
    ],
)
def test_a_compose_file_option_does_not_stop_the_gate_match(repo, shell, command):
    _assert_permitted(_guard(command, shell, repo), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "docker compose -f x.yml up",
        "docker compose -f x.yml run app sh",
        "docker compose --file x.yml down",
        "docker compose -f x.yml -f y.yml rm -f",
        "docker compose -f config up",
        "docker compose -f x.yml",
        "docker compose -f",
        "docker compose -f x.yml config > /Users/x/out.yml",
        "docker -f x.yml compose config",
        "docker compose -p x -f y.yml config",
    ],
)
def test_a_removed_compose_file_option_cannot_hide_a_write(repo, shell, command):
    _assert_denied(_guard(command, shell, repo), command, shell)
