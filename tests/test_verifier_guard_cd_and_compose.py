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


@pytest.mark.parametrize("shell", SHELLS)
def test_a_relative_cd_is_denied_when_the_working_directory_is_in_no_repository(repo, shell):
    plain = repo.parent / "outside"
    command = "cd api && ./gradlew test"
    _assert_denied(_guard(command, shell, plain), command, shell)


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
