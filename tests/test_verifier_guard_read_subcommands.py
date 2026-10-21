"""A read command of a declaration does not permit a write subcommand after it (#164).

The guard of 1.9.0 matched a `getOnlyCommands` entry by prefix. With the entry
`tea issues`, it permitted `tea issues create -t x`, which writes to the issue
tracker. The guard now permits, after the words of the entry, only options
(a word that starts with "-"), the word after an option (its value), numbers,
paths (a word with a "/"), and redirections. Each other word can be a
subcommand, and it is a deny. An entry with a trailing " *" is a prefix on
purpose: the guard permits each word after it, and the rendered verifier
states that the entry is a prefix.

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


def _declaration(*get_only: str, **extra: object) -> dict[str, object]:
    return {"agents": {"web": {"description": "A web app.", "getOnlyCommands": list(get_only), **extra}}}


def _files(declaration: dict[str, object]) -> tuple[dict[str, str], dict[str, str]]:
    [agent] = parse_declaration(declaration)
    tiers = load_tiers()
    return render_claude_code(agent, tiers), render_opencode(agent, tiers)


def _hook(declaration: dict[str, object]) -> str:
    fm, _body, err = split_frontmatter(_files(declaration)[0]["web-verifier.md"])
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


@pytest.fixture(scope="module")
def read_hook() -> str:
    """The hook of a declaration with the read command `tea issues`."""
    return _hook(_declaration("tea issues"))


@pytest.fixture(scope="module")
def prefix_hook() -> str:
    """The hook of a declaration with `tea issues` and the prefix entry `tea issues list *`."""
    return _hook(_declaration("tea issues", "tea issues list *"))


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


def _guard(hook: str, command: str, shell: str, cwd: Path) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True, cwd=cwd)


# --- the acceptance of the issue ---------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "tea issues",
        "tea issues 12",
        "tea issues --state open",
        "tea issues --state=open --limit 5",
        "tea issues 12 --repo o/r",
        "tea issues --state open 2>&1",
        "tea issues 12 > /dev/null",
        "git status && tea issues 12",
    ],
)
def test_a_read_command_with_options_values_and_numbers_is_permitted(read_hook, worktree, shell, command):
    r = _guard(read_hook, command, shell, worktree)
    assert r.returncode == 0, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    ("command", "word"),
    [
        ("tea issues create -t x", "create"),
        ("tea issues close 12", "close"),
        ("tea issues edit 12 --title x", "edit"),
        ("tea issues 12 delete", "delete"),
        ("tea issues --state open comment", "comment"),
        ("tea issues --repo o/r create -t x", "create"),
        ("tea issues 'create' -t x", "create"),
        ("tea issues c\\reate -t x", "create"),
        # "--" ends the options: the word after it is no value of an option.
        ("tea issues -- create", "create"),
        ("git status && tea issues create -t x", "create"),
    ],
)
def test_a_bare_word_after_a_read_command_is_denied(read_hook, worktree, shell, command, word):
    r = _guard(read_hook, command, shell, worktree)
    assert r.returncode == 2, (shell, command, r.stdout, r.stderr)
    assert f"The word {word} after the read command [tea issues]" in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_read_command_that_lists_a_cli_group_denies_its_write_subcommand(worktree, shell):
    hook = _hook(_declaration("gh issue", "gh issue view"))
    for command in ("gh issue view 164 --comments", "gh issue view 164 --json title,body", "gh issue"):
        r = _guard(hook, command, shell, worktree)
        assert r.returncode == 0, (shell, command, r.stdout, r.stderr)
    for command in ("gh issue create --title x", "gh issue close 164", "gh issue comment 164 --body x"):
        r = _guard(hook, command, shell, worktree)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command", ["tea issues list --state all", "tea issues list", "tea issues list mine --state all", "tea issues 12"]
)
def test_an_entry_with_a_trailing_star_is_a_prefix(prefix_hook, worktree, shell, command):
    r = _guard(prefix_hook, command, shell, worktree)
    assert r.returncode == 0, (shell, command, r.stdout, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_prefix_entry_keeps_the_get_only_rule_and_opens_no_other_subcommand(prefix_hook, worktree, shell):
    for command in ("tea issues list -X POST", "tea issues list --method POST", "tea issues create -t x"):
        r = _guard(prefix_hook, command, shell, worktree)
        assert r.returncode == 2, (shell, command, r.stdout, r.stderr)


# --- the declaration and the rendered files ----------------------------------


def test_the_rendered_verifier_states_that_an_entry_with_a_star_is_a_prefix():
    claude_code, _opencode = _files(_declaration("tea issues", "tea issues list *"))
    body = split_frontmatter(claude_code["web-verifier.md"])[1]
    assert "`tea issues list` is a prefix" in body, body
    assert "`tea issues` is a prefix" not in body, body


def test_a_verifier_with_no_prefix_entry_states_no_prefix():
    claude_code, _opencode = _files(_declaration("tea issues"))
    assert "is a prefix" not in split_frontmatter(claude_code["web-verifier.md"])[1]


def test_the_opencode_verifier_permits_no_read_command_of_either_form():
    _claude_code, opencode = _files(_declaration("tea issues", "tea issues list *"))
    perm = split_frontmatter(opencode["web-verifier.md"])[0]["permission"]
    for command in ("tea issues", "tea issues 12", "tea issues create -t x", "tea issues list --state all"):
        assert opencode_bash_action(perm, command) == "deny", command


@pytest.mark.parametrize(
    "bad",
    ["tea issues list*", "tea * list", "tea issues * *", "*", "git *", "rm *"],
)
def test_an_entry_with_a_star_in_another_form_or_on_a_write_is_refused(bad):
    with pytest.raises(ValueError, match="getOnlyCommands"):
        parse_declaration(_declaration(bad))


def test_a_gate_command_takes_no_star():
    with pytest.raises(ValueError, match="gateCommands"):
        parse_declaration({"agents": {"web": {"description": "d", "gateCommands": ["npm test *"]}}})
