"""A write target with a `..` component has its own deny message, and the prose states the shortest output prefix (#165).

The report of #165 found two texts that were not exact:

1. The rendered Claude Code verifier said that the hook denies `--output` and
   "each prefix of it", but `git diff --o /Users/x/o` passed: the guard read the
   prefixes only down to `--ou`. The guard now reads each prefix down to `--o`
   as the output option, after a git read command and after a GET-only
   command, and each text states that shortest prefix. A command built on
   argparse accepts a unique prefix, so `agent-definitions render --ou <path>`
   writes to `<path>`; before, the GET-only rule read the prefixes only down to
   `--out`.
2. `touch ../x` in a temp copy was denied with the message "A path argument is
   outside a temp directory.", but the guard denies it for the `..`
   component. The message of a write target with a `..` component now names
   that component.

Each case renders a declaration and runs the rendered hook in bash, /bin/sh and
zsh, as the harness does.
"""

from __future__ import annotations

import json
import os
import re
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

from .pages import ROOT, outside_the_package
from .support import git

SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

#: A GET-only command built on argparse: `render` has the option `--out`, and `--ou` is a unique prefix of it.
GET_ONLY = "agent-definitions render"

DECLARATION = {"agents": {"stack": {"description": "A repository with one stack.", "getOnlyCommands": [GET_ONLY]}}}

#: The fixed temp prefixes of the guard, without a /private prefix.
TEMP_TEXT = ("/tmp/", "/var/folders/")

#: Each prefix of `--output` that is a long option: "--" alone is the end of the options.
OUTPUT_PREFIXES = tuple("--output"[:n] for n in range(3, len("--output") + 1))

#: A path outside each temp directory and outside each git tree of a test.
OUTSIDE = "/Users/x/o"

#: The texts that state the git output rule, and the texts that state the GET-only output rule.
SKILL = ROOT / "skills" / "agent-definitions" / "SKILL.md"
ENFORCEMENT_PAGE = ROOT / "docs" / "explanation" / "the-enforcement-model-and-its-limits.md"
COMMITTED_VERIFIER = ROOT / ".claude" / "agents" / "delegate-verifier.md"
GIT_RULE = re.compile(r"denies `--output` and each prefix of it down to `(--[a-z]+)`")
GET_ONLY_RULE = re.compile(r"each prefix of `--output` down to `(--[a-z]+)`")


def _verifier() -> tuple[str, str]:
    [agent] = parse_declaration(DECLARATION)
    fm, body, err = split_frontmatter(render_claude_code(agent, load_tiers())["stack-verifier.md"])
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"], body


HOOK, BODY = _verifier()


def _guard(command: str, shell: str, cwd: Path, tmpdir: Path | None = None) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the hook input and of the process."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", HOOK], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    env = None if tmpdir is None else {**os.environ, "TMPDIR": f"{tmpdir}/"}
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True, env=env, cwd=cwd)


@pytest.fixture
def repo(tmp_path, git_identity) -> Path:
    """A git repository with one tracked file."""
    root = Path(os.path.realpath(tmp_path)) / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    (root / "a.txt").write_text("a\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-q", "-m", "a")
    return root


@pytest.fixture
def copy(tmp_path) -> Path:
    """A temp copy with no `.git` entry, in a directory whose path starts with a fixed temp prefix as text.

    The pytest temp directory has such a path on macOS and on a Linux CI host. A
    Nix build sandbox gives /build, so the copy then goes in /tmp.
    """
    base = Path(os.path.realpath(tmp_path))
    made = None
    text = str(base)[len("/private"):] if str(base).startswith("/private/") else str(base)
    if not text.startswith(TEMP_TEXT):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = base = Path(os.path.realpath(tempfile.mkdtemp(dir="/tmp")))
    root = base / "proof"
    (root / "app").mkdir(parents=True)
    yield root
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


# --- A write target with a .. component ----------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command,target",
    [
        ("touch ../x", "../x"),
        ("touch app/../../x", "app/../../x"),
        ("mkdir -p ../x", "../x"),
        ("rm -rf ..", ".."),
        ("cp a.txt ../x", "../x"),
        ("echo x > ../x", "../x"),
        ("echo x >../x", "../x"),
        ("touch /tmp/../x", "/tmp/../x"),
        ("touch '../x'", "'../x'"),
    ],
)
def test_the_deny_message_of_a_write_target_with_a_dotdot_component_names_the_component(copy, shell, command, target):
    # The report of #165: in a temp copy, `touch ../x` was denied as a path outside a temp directory.
    r = _guard(command, shell, copy, tmpdir=copy.parent)

    assert r.returncode == 2, (shell, command, r.stderr)
    assert f"[{command}]" in r.stderr, (shell, command, r.stderr)
    assert f"The write target [{target}] has a .. component." in r.stderr, (shell, command, r.stderr)
    assert "outside a temp directory" not in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_write_target_with_a_dotdot_component_in_a_git_tree_gets_the_same_message(repo, shell):
    command = "touch ../x"
    r = _guard(command, shell, repo)

    assert r.returncode == 2, (shell, command, r.stderr)
    assert "The write target [../x] has a .. component." in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["touch /Users/x/o", "echo x > /Users/x/o", "cp a.txt /Users/x/o", "touch a..b"])
def test_a_write_target_outside_a_temp_directory_with_no_dotdot_component_keeps_its_message(repo, shell, command):
    r = _guard(command, shell, repo)

    assert r.returncode == 2, (shell, command, r.stderr)
    assert ".. component" not in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["touch app/x", "echo x > app/x", "touch app/a..b"])
def test_a_write_target_in_the_temp_copy_with_no_dotdot_component_stays_permitted(copy, shell, command):
    r = _guard(command, shell, copy, tmpdir=copy.parent)

    assert r.returncode == 0, (shell, command, r.stderr)


# --- The prose and the guard agree on the shortest output prefix ---------------------


def _denied_prefixes(template: str, shell: str, cwd: Path) -> list[str]:
    return [p for p in OUTPUT_PREFIXES if _guard(template.format(p), shell, cwd).returncode == 2]


def _stated(rule: re.Pattern, text: str) -> list[str]:
    return rule.findall(" ".join(text.split()))


# The skill, the docs and the committed verifier are outside the package source, as in a Nix build.
@outside_the_package
@pytest.mark.parametrize("shell", SHELLS)
def test_the_prose_and_the_guard_agree_on_the_shortest_git_output_prefix(repo, shell):
    texts = {
        "rendered verifier": BODY,
        "committed verifier": COMMITTED_VERIFIER.read_text(),
        "skill": SKILL.read_text(),
        "enforcement page": ENFORCEMENT_PAGE.read_text(),
    }
    for command in ("git diff {} " + OUTSIDE, "git log {} " + OUTSIDE, "git show {}=" + OUTSIDE + " HEAD"):
        denied = _denied_prefixes(command, shell, repo)
        assert denied, (shell, command)
        # The guard denies the shortest prefix and each longer one.
        assert denied == list(OUTPUT_PREFIXES[OUTPUT_PREFIXES.index(denied[0]):]), (shell, command, denied)

        assert {name: _stated(GIT_RULE, text) for name, text in texts.items()} == {
            name: [denied[0]] for name in texts
        }, (shell, command)


@outside_the_package
@pytest.mark.parametrize("shell", SHELLS)
def test_the_prose_and_the_guard_agree_on_the_shortest_get_only_output_prefix(repo, shell):
    for command in (f"{GET_ONLY} {{}} {OUTSIDE}", f"{GET_ONLY} {{}}={OUTSIDE}"):
        denied = _denied_prefixes(command, shell, repo)
        assert denied, (shell, command)
        assert denied == list(OUTPUT_PREFIXES[OUTPUT_PREFIXES.index(denied[0]):]), (shell, command, denied)

        assert _stated(GET_ONLY_RULE, SKILL.read_text()) == [denied[0]], (shell, command)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("spelling", ["--ou {}", "--ou={}", "--o {}", "--o={}"])
def test_a_short_prefix_of_output_after_a_get_only_command_is_denied_outside_a_temp_directory(repo, shell, spelling):
    # argparse takes `--ou` as the `--out` of `agent-definitions render`, so `render --ou /Users/x/o` writes there.
    command = f"{GET_ONLY} {spelling.format(OUTSIDE)}"
    r = _guard(command, shell, repo)

    assert r.returncode == 2, (shell, command, r.stderr)
    assert "An output path is outside a temp directory." in r.stderr, (shell, command, r.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", [f"{GET_ONLY} --ou /tmp/x", "git diff --o=/tmp/x/f", "git log --o /tmp/x/f"])
def test_a_short_prefix_of_output_with_a_temp_path_is_permitted(repo, shell, command):
    r = _guard(command, shell, repo)

    assert r.returncode == 0, (shell, command, r.stderr)
