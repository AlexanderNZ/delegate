"""A path or an argument that the guard reads as text cannot expand in the shell (#230).

#175 made the guard deny a word after a gate or a declared read when the shell
can expand that word. The specialist of #175 found the same hole in the other
places where the guard reads a word as text. The temp-path test of a write
target reads the text before the first `{`, so `touch <temp dir>/{a,<home>/x}`
passed when no git tree was below that text, and the brace expansion wrote
outside the temp directory. A plain read command, a git read option and the
path of `git -C` were not tested either: bash runs `-delete` in
`find . {-delete,-print}`, and zsh runs the glob qualifier of
`ls *(e:'touch x':)`. The comments of #230 add a redirection target with a glob
qualifier, and the zsh numeric glob `<->`, which the guard read as two
redirections.

Each case uses a real git repository and an empty temp directory with no git
tree in it, renders a declaration, and runs the rendered hook in bash, /bin/sh
and zsh, as the harness does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

from delegate.cli.agent_definitions import main as agent_definitions_main
from delegate.definitions.declaration import parse_declaration
from delegate.definitions.render import render_claude_code
from delegate.definitions.validate import split_frontmatter
from delegate.shared.tiers import load_tiers

from .pages import ROOT, outside_the_package
from .support import git

#: Each shell that runs the Bash guard.
SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

#: The fixed temp prefixes of the guard, without a /private prefix.
TEMP_TEXT = ("/tmp/", "/var/folders/")

#: A path in a home directory, as text. The guard denies a write to it.
HOME = "/Users/nobody/x"

DECLARATION = {
    "agents": {
        "web": {
            "description": "A web app.",
            "gateCommands": ["npm test"],
            "gateExtraFlags": ["-t <value>"],
            "getOnlyCommands": ["tea issues"],
        }
    }
}


def _hook(declaration: dict[str, object]) -> str:
    [agent] = parse_declaration(declaration)
    fm, _body, err = split_frontmatter(render_claude_code(agent, load_tiers())["web-verifier.md"])
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


HOOK = _hook(DECLARATION)


def _without_private(path: str | Path) -> str:
    text = str(path)
    return text[len("/private"):] if text.startswith("/private/") else text


class Place:
    """A git repository at `<base>/main` and an empty directory at `<base>/empty`, with no git tree in it.

    `base` is the real path of a temp directory, and the `TMPDIR` of the hook.
    `empty` is the text of the empty directory, with no /private prefix.
    """

    def __init__(self, base: Path):
        self.base = base
        self.main = base / "main"
        (self.main / "src").mkdir(parents=True)
        (self.main / "a.txt").write_text("x\n")
        (self.main / "src" / "x.py").write_text("x = 1\n")
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "add", ".")
        git(self.main, "commit", "-q", "-m", "seed")
        (base / "empty").mkdir()
        self.empty = _without_private(base / "empty")

    def env(self) -> dict[str, str]:
        return {**os.environ, "TMPDIR": f"{self.base}/"}


@pytest.fixture
def place(tmp_path, git_identity):
    """A place whose path starts with a fixed temp prefix as text, after a /private prefix is removed.

    The pytest temp directory has such a path on macOS and on a Linux CI host. A
    Nix build sandbox gives /build, so the place then goes in /tmp.
    """
    base = Path(os.path.realpath(tmp_path))
    made = None
    if not _without_private(base).startswith(TEMP_TEXT):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = base = Path(os.path.realpath(tempfile.mkdtemp(dir="/tmp")))
    yield Place(base)
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


def _guard(hook: str, place: Place, command: str, shell: str) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with the repository as the working directory of the hook input and the process."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(place.main)})
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(
        argv, executable=executable, input=payload, capture_output=True, text=True, env=place.env(), cwd=place.main,
    )


# --- The commands of the issue --------------------------------------------------
#
# Each command is a template: {E} is the empty temp directory, and {H} is a
# path in a home directory. The second item is the word that the deny message
# names.

#: A write target, a cp destination, a cd path and an output option. The temp-path test read only the text before
#: the "{", and no git tree was below that text.
TEMP_PATH_COMMANDS = [
    ("touch {E}/{{a,{H}}}", "{E}/{{a,{H}}}"),
    ("rm {E}/{{a,{H}}}", "{E}/{{a,{H}}}"),
    ("mkdir {E}/{{a,{H}}}", "{E}/{{a,{H}}}"),
    ("cp a.txt {E}/{{a,{H}}}", "{E}/{{a,{H}}}"),
    ("echo x > {E}/{{a,{H}}}", "{E}/{{a,{H}}}"),
    ("git log --output={E}/{{a,{H}}}", "--output={E}/{{a,{H}}}"),
    ("cd {E}/{{a,b}}", "{E}/{{a,b}}"),
]

#: A plain read command, a git read option, and the path of git -C.
READ_COMMANDS = [
    ("find . {{-delete,-print}}", "{{-delete,-print}}"),
    ("rg x {{--pre=touch,.}}", "{{--pre=touch,.}}"),
    ("git grep x {{-Ocat,.}}", "{{-Ocat,.}}"),
    ("git show {{--output={H}/o,HEAD}}", "{{--output={H}/o,HEAD}}"),
    ("git -C {{{H}/r,.}} status", "{{{H}/r,.}}"),
    ("ls *(e:'touch x':)", "*(e:'touch x':)"),
    ("cat *(e:'touch x':)", "*(e:'touch x':)"),
    ("grep -r x *(e:'touch x':)", "*(e:'touch x':)"),
]

#: The commands of the comment of #230: a redirection target with a zsh glob qualifier, also after a gate, and the
#: zsh numeric glob "<->", which the guard read as two redirections.
COMMENT_COMMANDS = [
    ("cat /dev/null > {E}/*(e:'touch {H}':)", "{E}/*(e:'touch {H}':)"),
    ("npm test -- -t x > {E}/*(e:'touch {H}':)", "{E}/*(e:'touch {H}':)"),
    ("npm test -- -t a<->{E}/x", "a<->{E}/x"),
]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(("command", "word"), [*TEMP_PATH_COMMANDS, *READ_COMMANDS, *COMMENT_COMMANDS])
def test_a_word_that_can_expand_is_denied_and_named(place, shell, command, word):
    command = command.format(E=place.empty, H=HOME)
    word = word.format(E=place.empty, H=HOME)

    result = _guard(HOOK, place, command, shell)

    assert result.returncode == 2, (shell, command, result.stderr)
    assert f"The word {word} " in result.stderr, (shell, command, result.stderr)
    assert "can expand in the shell" in result.stderr, (shell, command, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
def test_an_output_path_of_a_declared_read_that_can_expand_is_denied_and_named(place, shell):
    """The issue names this command too. The word check of #175 already denies it after the entry `tea issues`."""
    command = f"tea issues -o {place.empty}/{{a,{HOME}}}"

    result = _guard(HOOK, place, command, shell)

    assert result.returncode == 2, (shell, command, result.stderr)
    assert f"The word {place.empty}/{{a,{HOME}}} after the read command [tea issues] can expand in the shell" in (
        result.stderr
    ), (shell, command, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    ("command", "word"),
    [
        # bash and zsh divide an unquoted expansion at each character of $IFS, so one word gives two paths.
        ("touch {E}/a$IFS{H}", "{E}/a$IFS{H}"),
        ("cp a.txt {E}/a$IFS{H}", "{E}/a$IFS{H}"),
        ("rg x a$IFS--pre=touch", "a$IFS--pre=touch"),
        ("find . -name x$IFS-delete", "x$IFS-delete"),
        ("git show HEAD$IFS--output={H}/o", "HEAD$IFS--output={H}/o"),
        ("ls $TMPDIR$IFS--color", "$TMPDIR$IFS--color"),
        ("touch $TMPDIRX", "$TMPDIRX"),
    ],
)
def test_a_word_with_an_expansion_outside_single_quotes_is_denied_and_named(place, shell, command, word):
    command = command.format(E=place.empty, H=HOME)
    word = word.format(E=place.empty, H=HOME)

    result = _guard(HOOK, place, command, shell)

    assert result.returncode == 2, (shell, command, result.stderr)
    assert f"The word {word} holds $ or a backtick outside single quotes" in result.stderr, (
        shell,
        command,
        result.stderr,
    )


# --- The quoted forms are literal -----------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "touch '{E}/{{a,b}}'",
        "mkdir '{E}/{{a,b}}'",
        "rm '{E}/{{a,b}}'",
        "cp a.txt '{E}/{{a,b}}'",
        "echo x > '{E}/{{a,b}}'",
        "cd '{E}'",
        "git log --output='{E}/{{a,b}}'",
        "tea issues -o '{E}/{{a,b}}'",
        "ls '*(e)'",
        "cat 'src/*(e:x:)'",
        "find . -name '*.py'",
        'find . -name "*.py"',
        "find . -name \\*.py",
        "rg 'a$' src",
        "grep -rn x --include='*.py' .",
        "git log --format='%H %s' -n 1",
        "git diff 'HEAD~1'",
        "git -C '{E}' status",
    ],
)
def test_a_quoted_word_is_literal(place, shell, command):
    command = command.format(E=place.empty)

    result = _guard(HOOK, place, command, shell)

    assert result.returncode == 0, (shell, command, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "ls",
        "ls -la src",
        "cat a.txt src/x.py",
        "grep -rn x src 2>&1",
        "rg -n 'x = 1' src > /dev/null",
        "git status --short",
        "git log --oneline -5",
        "git diff main...HEAD -- src/x.py",
        "git show HEAD:src/x.py",
        "touch $TMPDIR/out.txt",
        'touch "$TMPDIR/out.txt"',
        "echo x > $TMPDIR/out.txt",
        "git log --output=$TMPDIR/log.txt",
        "npm test -- -t x > $TMPDIR/out.txt",
        "npm test -- -t 'a*' 2>&1",
        "tea issues -o $TMPDIR/x.json",
        "mktemp -p $TMPDIR xXXXXXX",
        'cd "$TMPDIR"',
        "ls $TMPDIR",
    ],
)
def test_a_command_with_no_expansion_is_still_permitted(place, shell, command):
    command = command.format(E=place.empty)

    result = _guard(HOOK, place, command, shell)

    assert result.returncode == 0, (shell, command, result.stderr)


# --- The gates and the declared reads of this repository -----------------------


def _own_gates() -> tuple[list[str], list[str]]:
    declaration = tomllib.loads((ROOT / ".claude/skills/delegate-context/agents.toml").read_text())
    [agent] = declaration["agents"].values()
    return agent["gateCommands"], agent["getOnlyCommands"]


@outside_the_package
@pytest.mark.parametrize("shell", SHELLS)
def test_the_gates_and_the_declared_reads_of_this_repository_are_still_permitted(place, tmp_path, shell):
    out = tmp_path / "rendered"
    assert agent_definitions_main(["render", str(ROOT / ".claude/skills/delegate-context/agents.toml"), "-o", str(out)]) == 0
    fm, _body, err = split_frontmatter((out / "claude-code" / "delegate-verifier.md").read_text())
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    hook = bash["hooks"][0]["command"]
    gates, reads = _own_gates()
    commands = [
        *gates,
        "nix develop -c python3 -m pytest -rs -q -n auto",
        "nix develop -c python3 -m pytest -rs -n auto src/x.py",
        *[f"{read} 230 --comments" for read in reads if read.endswith("view")],
        *[f"{read} --state open --limit 20" for read in reads if read.endswith("list")],
    ]
    for command in commands:
        result = _guard(hook, place, command, shell)
        assert result.returncode == 0, (shell, command, result.stderr)
