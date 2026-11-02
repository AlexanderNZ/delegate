"""The text about a ' *' entry tells the truth, and a quoted word with $ is one word (#238).

Since #175, the guard checks each word after a gate or a getOnlyCommands entry
that ends with ` *`, and it denies a word that the shell can expand. Some text
of the kit still said that the guard permits each word after such an entry
with no check: the rendered verifier prompt, the deny messages of the guard,
the skill and the docs. A consumer's verifier read that text, and the guard
then denied its commands.

The guard also stopped reading a quote when the quoted word held a `$`: it
divided the command at a `|`, `;` or `&` inside `$'...'` or inside a
double-quoted word with `${`. The command was still denied, but the message
named a fragment of the word, not the word.

The guard cases use a real git repository and run the rendered hook in bash,
/bin/sh and zsh, as the harness does.
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

from delegate.cli.agent_definitions import main as agent_definitions_main
from delegate.definitions.validate import split_frontmatter

from .pages import ROOT, outside_the_package
from .support import committed_repo

#: Each shell that runs the Bash guard.
SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

#: The fixed temp prefixes of the guard, without a /private prefix.
TEMP_TEXT = ("/tmp/", "/var/folders/")

DECLARATION = """\
[agents.web]
description = "A web app."
gateCommands = ["npm test", "./gradlew *"]
getOnlyCommands = ["tea issues", "tea pulls list *"]
"""

#: A claim that the guard does not check the words after a ' *' entry. The text
#: "permits each word after ..." is a false claim unless the same sentence
#: gives the exception (#175) or says that the OpenCode map permits also a word
#: that the shell can expand.
FALSE_CLAIMS = (
    re.compile(r"\bdoes? not check (?:the|these|those) words\b"),
    re.compile(
        r"\bpermits? each word after (?:it|the gate|the entry|such a gate|such an entry)\b"
        r"(?![^.]*\b(?:except|also a word)\b)"
    ),
)


def _claims(text: str) -> list[str]:
    """Each false claim in `text`. A line break and a comment mark between two words count as one space."""
    flat = re.sub(r"\s*\n\s*(?:#:?\s*)?", " ", text)
    return [m.group(0) for pattern in FALSE_CLAIMS for m in pattern.finditer(flat)]


def _render(tmp_path: Path) -> Path:
    declaration = tmp_path / "agents.toml"
    declaration.write_text(DECLARATION)
    out = tmp_path / "rendered"
    assert agent_definitions_main(["render", str(declaration), "-o", str(out)]) == 0
    return out


def _hook(out: Path) -> str:
    fm, _body, err = split_frontmatter((out / "claude-code" / "web-verifier.md").read_text())
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> Path:
    return _render(tmp_path_factory.mktemp("star-text"))


@pytest.fixture(scope="module")
def hook(rendered) -> str:
    return _hook(rendered)


def _without_private(path: str | Path) -> str:
    text = str(path)
    return text[len("/private"):] if text.startswith("/private/") else text


@pytest.fixture
def repo(tmp_path, git_identity):
    """A git repository whose path starts with a fixed temp prefix as text, after a /private prefix is removed."""
    base = Path(os.path.realpath(tmp_path))
    made = None
    if not _without_private(base).startswith(TEMP_TEXT):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = base = Path(os.path.realpath(tempfile.mkdtemp(dir="/tmp")))
    yield committed_repo(base / "main", {"a.txt": "x\n", "src/x.py": "x = 1\n"})
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


def _guard(hook: str, repo: Path, command: str, shell: str) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with the repository as the working directory."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo)})
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    base = repo.parent
    return subprocess.run(
        argv, executable=executable, input=payload, capture_output=True, text=True,
        env={**os.environ, "TMPDIR": f"{base}/"}, cwd=repo,
    )


# --- 1. The text about a ' *' entry ---------------------------------------------


def test_the_rendered_verifier_of_each_harness_says_that_the_guard_checks_the_words_after_a_star_gate(rendered):
    for harness in ("claude-code", "opencode"):
        body = split_frontmatter((rendered / harness / "web-verifier.md").read_text())[1]
        assert "The gate `./gradlew` takes free arguments." in body
        assert _claims(body) == [], harness
        assert "except a word that the shell can expand" in body, harness
        assert "single quotes" in body, harness


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    ("command", "entry"),
    [("npm test spotlessApply", "gateCommands"), ("tea issues create", "getOnlyCommands")],
)
def test_the_deny_message_after_a_gate_or_a_read_command_gives_the_exception_of_a_star_entry(
    hook, repo, shell, command, entry
):
    result = _guard(hook, repo, command, shell)

    assert result.returncode == 2, (shell, command, result.stderr)
    assert f"A {entry} entry that ends with * permits each word after" in result.stderr, (shell, result.stderr)
    assert _claims(result.stderr) == [], (shell, result.stderr)
    assert "single quotes" in result.stderr, (shell, result.stderr)


def _documents() -> list[Path]:
    paths = [ROOT / "README.md", ROOT / "AGENTS.md"]
    for pattern in ("docs/**/*.md", "skills/**/*.md", "examples/*.toml", "src/delegate/**/*.py"):
        paths += sorted(ROOT.glob(pattern))
    return [p for p in paths if p.is_file()]


@outside_the_package
def test_no_document_of_the_kit_says_that_the_words_after_a_star_entry_are_not_checked():
    found = {str(p.relative_to(ROOT)): _claims(p.read_text()) for p in _documents()}

    assert {path: claims for path, claims in found.items() if claims} == {}
    assert "skills/agent-definitions/SKILL.md" in found
    assert "src/delegate/definitions/declaration.py" in found


# --- 4. A quoted word with $ is one word ----------------------------------------
#
# The guard denies each of these commands. The deny message names the whole word
# that holds $, not a fragment of the command after a separator in the quotes.

QUOTED_EXPANSIONS = [
    ("git grep -n $'a|b' -- src", "$'a|b'"),
    ("git grep -n $'a\\tb' -- src", "$'a\\tb'"),
    ('echo "${PIPESTATUS[0]} a|b"', '"${PIPESTATUS[0]} a|b"'),
    ("git grep -n $'a;b&c' -- src", "$'a;b&c'"),
    ('git grep -n "${p}|b" -- src', '"${p}|b"'),
    ("git grep -n $'a\\'|b' -- src", "$'a\\'|b'"),
    ("./gradlew test $'a|b'", "$'a|b'"),
    ("tea pulls list $'a;b'", "$'a;b'"),
]


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(("command", "word"), QUOTED_EXPANSIONS)
def test_the_guard_denies_a_quoted_word_with_a_dollar_and_names_the_whole_word(hook, repo, shell, command, word):
    result = _guard(hook, repo, command, shell)

    assert result.returncode == 2, (shell, command, result.stderr)
    assert f"The word {word} " in result.stderr, (shell, command, result.stderr)
    assert "holds $ or a backtick" in result.stderr, (shell, command, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "git grep -n 'a|b' -- src",
        'git grep -n "a|b" -- src',
        "git grep -n 'a${b}' -- src",
        "./gradlew test 'a|b'",
        "git grep -n 'x' -- src | head -n 3",
    ],
)
def test_a_word_in_single_quotes_or_with_no_dollar_stays_permitted(hook, repo, shell, command):
    result = _guard(hook, repo, command, shell)

    assert result.returncode == 0, (shell, command, result.stderr)


#: Before #238, a "${" or a "$'" in the text made the guard divide at each
#: separator, also inside single quotes. The guard still tests the command so, and
#: the command still denies: no command that the guard denied becomes permitted.
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("command", ["git grep -n 'a${b}|c' -- src", "git grep -n 'a|b' -- src $'x'"])
def test_a_command_that_the_guard_denied_before_stays_denied(hook, repo, shell, command):
    result = _guard(hook, repo, command, shell)

    assert result.returncode == 2, (shell, command, result.stderr)


#: A separator after the quoted word still divides the command. The text is in
#: two parts, so that the push guard of the specialist that edits this file does
#: not read it as a push.
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    ("command", "segment"),
    [
        ("git grep -n $'a' -- src; rm -rf src", "rm -rf src"),
        ("git grep -n $'a\\\\' -- src | rm -rf src", "rm -rf src"),
        ("echo \"${p}\" && git " + "push", "git " + "push"),
        ("echo $\\\n'a|b'", None),
    ],
)
def test_a_separator_after_a_quoted_word_with_a_dollar_still_divides_the_command(hook, repo, shell, command, segment):
    result = _guard(hook, repo, command, shell)

    assert result.returncode == 2, (shell, command, result.stderr)
    if segment is not None:
        assert f"[{segment}]" in result.stderr, (shell, command, result.stderr)
