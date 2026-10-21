"""A word after a gate cannot make the gate write (#163).

The guard of 1.9.0 matched a gate by prefix, so it did not examine the words
after the gate, and some of them make the gate write: `npm run lint -- --fix`
rewrites source files, `npm test -- -u` updates snapshots, `npx tsc --outDir
<dir>` writes in that directory, and `./gradlew test spotlessApply` runs a task
that formats the source. After a gate, the guard now permits only a path that
exists in the git toplevel or in a temp directory, the separator `--`, a
redirection, and a flag of the `gateExtraFlags` of the declaration.

Each case uses a real git repository in a temp directory, renders a
declaration, and runs the rendered hook in bash, /bin/sh and zsh, as the
harness does.
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

from delegate.cli.agent_definitions import main as agent_definitions_main
from delegate.definitions.declaration import parse_declaration
from delegate.definitions.render import render_claude_code, render_opencode
from delegate.definitions.validate import opencode_bash_action, split_frontmatter
from delegate.shared.tiers import load_tiers

from .support import git

#: Each shell that runs the Bash guard.
SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

#: The fixed temp prefixes of the guard, without a /private prefix.
TEMP_TEXT = ("/tmp/", "/var/folders/")

GATES = ["npm run lint", "npm test", "npx tsc", "./gradlew test", "python3 -m pytest"]

#: The four commands of the report. Each one exits 0 in the guard of 1.9.0.
REPORTED = [
    ("cd app && npm run lint -- --fix", "--fix", "npm run lint"),
    ("cd app && npm test -- -u", "-u", "npm test"),
    ("cd app && npx tsc --outDir /Users/x/o", "--outDir", "npx tsc"),
    ("cd api && ./gradlew test spotlessApply", "spotlessApply", "./gradlew test"),
]


def _declaration(**extra: object) -> dict[str, object]:
    return {"agents": {"web": {"description": "A web app.", "gateCommands": list(GATES), **extra}}}


def _files(declaration: dict[str, object]) -> tuple[dict[str, str], dict[str, str]]:
    [agent] = parse_declaration(declaration)
    tiers = load_tiers()
    return render_claude_code(agent, tiers), render_opencode(agent, tiers)


def _hook(declaration: dict[str, object]) -> str:
    fm, _body, err = split_frontmatter(_files(declaration)[0]["web-verifier.md"])
    assert err is None, err
    [bash] = [entry for entry in fm["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


def _opencode_permission(declaration: dict[str, object]) -> dict:
    fm, _body, err = split_frontmatter(_files(declaration)[1]["web-verifier.md"])
    assert err is None, err
    return fm["permission"]


HOOK = _hook(_declaration())


@pytest.fixture(scope="module")
def flags_hook() -> str:
    """The hook of a declaration with gateExtraFlags: a flag alone, a flag with a value word, and a short flag."""
    return _hook(_declaration(gateExtraFlags=["--reporter", "-k <value>", "-q"]))


class Repo:
    """A git repository at `<base>/main` and a copy of it with no `.git` entry at `<base>/proof`.

    `base` is the real path of a temp directory, and the `TMPDIR` of the hook.
    """

    def __init__(self, base: Path):
        self.base = base
        self.main = base / "main"
        for name in ("app/src/a.test.ts", "api/build.gradle", "tests/test_x.py"):
            (self.main / name).parent.mkdir(parents=True, exist_ok=True)
            (self.main / name).write_text("x\n")
        git(self.main, "init", "-q", "-b", "main")
        git(self.main, "add", ".")
        git(self.main, "commit", "-q", "-m", "seed")
        self.copy = base / "proof"
        shutil.copytree(self.main, self.copy, symlinks=True, ignore=shutil.ignore_patterns(".git"))

    def env(self) -> dict[str, str]:
        return {**os.environ, "TMPDIR": f"{self.base}/"}


@pytest.fixture
def repo(tmp_path, git_identity):
    """A repository whose path starts with a fixed temp prefix as text, after a /private prefix is removed.

    The pytest temp directory has such a path on macOS and on a Linux CI host. A
    Nix build sandbox gives /build, so the repository then goes in /tmp.
    """
    base = Path(os.path.realpath(tmp_path))
    made = None
    if not _without_private(base).startswith(TEMP_TEXT):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = base = Path(os.path.realpath(tempfile.mkdtemp(dir="/tmp")))
    yield Repo(base)
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


def _without_private(path: str | Path) -> str:
    text = str(path)
    return text[len("/private"):] if text.startswith("/private/") else text


def _guard(hook: str, repo: Repo, command: str, shell: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the hook input and of the process."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(
        argv, executable=executable, input=payload, capture_output=True, text=True, env=repo.env(), cwd=cwd,
    )


def _assert_permitted(result: subprocess.CompletedProcess, command: str, shell: str) -> None:
    assert result.returncode == 0, (shell, command, result.stderr)


def _assert_denied(result: subprocess.CompletedProcess, command: str, shell: str) -> None:
    assert result.returncode == 2, (shell, command, result.stderr)
    assert "verifier guard" in result.stderr, (shell, command, result.stderr)


# --- The reported commands -----------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(("command", "word", "gate"), REPORTED)
def test_a_word_after_a_gate_that_can_make_it_write_is_denied(repo, shell, command, word, gate):
    result = _guard(HOOK, repo, command, shell, repo.main)

    _assert_denied(result, command, shell)
    assert f"The word {word} after the gate [{gate}]" in result.stderr, (shell, command, result.stderr)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "npm test src/missing.test.ts",
        "npm test -- app/src/a.test.ts --fix",
        "npm test -- ../main/app/src/a.test.ts",
        "npm test -- /etc/hosts",
        "npm test -- app/src/*.ts",
        "npm test -- '$HOME'",
        "npm test -- ~/x",
        "npm test --update-snapshot",
        "nix develop -c npm test -- -u",
        "direnv exec . npm run lint -- --fix",
        "env -i npm test -- -u",
        "npm test -- --reporter=dot",
        "npm testx",
    ],
)
def test_another_word_after_a_gate_is_denied(repo, shell, command):
    _assert_denied(_guard(HOOK, repo, command, shell, repo.main), command, shell)


# --- The permitted forms -------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        *GATES,
        "cd app && npm test -- src/a.test.ts",
        "python3 -m pytest tests/test_x.py",
        "npm test app/src/a.test.ts",
        "npm test -- app/src/a.test.ts tests/test_x.py",
        "npm test -- 'app/src/a.test.ts'",
        "npm test --",
        "cd app && npm test -- src/a.test.ts 2>&1",
        "npm test -- app/src/a.test.ts > /dev/null",
        "nix develop -c npm test -- app/src/a.test.ts",
        "cd app && npx tsc",
        "cd api && ./gradlew test",
    ],
)
def test_a_gate_with_paths_in_the_git_toplevel_is_permitted(repo, shell, command):
    _assert_permitted(_guard(HOOK, repo, command, shell, repo.main), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
def test_a_gate_with_a_path_in_a_temp_copy_is_permitted(repo, shell):
    proof = _without_private(repo.copy)
    for command, cwd in (
        ("npm test -- app/src/a.test.ts", repo.copy),
        (f"cd {proof}/app && npm test -- src/a.test.ts", repo.main),
        (f"npm test -- {proof}/app/src/a.test.ts", repo.main),
    ):
        _assert_permitted(_guard(HOOK, repo, command, shell, cwd), command, shell)


# --- gateExtraFlags ------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "npm test -- --reporter",
        "npm test -- --reporter=dot app/src/a.test.ts",
        "npm test -- -k cart",
        "npm test -- -k=cart -q app/src/a.test.ts",
        "cd app && npm test -- -q src/a.test.ts",
        "./gradlew test -q",
    ],
)
def test_a_flag_of_gate_extra_flags_is_permitted_after_a_gate(flags_hook, repo, shell, command):
    _assert_permitted(_guard(flags_hook, repo, command, shell, repo.main), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "./gradlew test -q spotlessApply",
        "npm test -- --reporter dot",
        "npm test -- -kcart",
        "npm test -- -u",
        "cd app && npm run lint -- --fix",
        "npm test -- -q --fix",
    ],
)
def test_a_flag_of_gate_extra_flags_gives_no_other_word(flags_hook, repo, shell, command):
    _assert_denied(_guard(flags_hook, repo, command, shell, repo.main), command, shell)


@pytest.mark.parametrize(
    "bad",
    ["-k", ["fix"], [""], ["--fix *"], ["-k; rm -rf /"], ["--"], ["-"], ["-k <value> <value>"], ["-k <path>"], [3]],
)
def test_the_declaration_refuses_a_gate_extra_flags_entry_that_is_not_a_flag(bad):
    with pytest.raises(ValueError, match="gateExtraFlags"):
        parse_declaration(_declaration(gateExtraFlags=bad))


def test_the_declaration_carries_its_gate_extra_flags():
    [agent] = parse_declaration(_declaration(gateExtraFlags=["--reporter=dot", "-k <value>"]))
    assert agent.gate_extra_flags == ["--reporter=dot", "-k <value>"]
    [plain] = parse_declaration(_declaration())
    assert plain.gate_extra_flags == []


# --- The OpenCode map ----------------------------------------------------------


def test_the_opencode_map_permits_each_gate_only_as_its_exact_text():
    perm = _opencode_permission(_declaration(gateExtraFlags=["-q"]))
    for gate in GATES:
        assert opencode_bash_action(perm, gate) == "allow", gate
    for command in (
        "npm run lint -- --fix",
        "npm test -- -u",
        "npx tsc --outDir /Users/x/o",
        "./gradlew test spotlessApply",
        "./gradlew test -q",
        "npm test -- src/a.test.ts",
    ):
        assert opencode_bash_action(perm, command) == "deny", command


# --- A gate that ends with " *" takes free arguments (#169) ---------------------
#
# After #163, real gate commands failed, and a declaration had no way to permit
# them: a flake ref is not a path (`nix eval --raw .#x`), and a task name is a
# bare word (`./gradlew test jacocoTestReport -x processTestAot`). A gate that
# ends with " *" now permits each word after it, as each gate did before #163.
# The segment rules for redirections, temp writes and git trees still apply.

FREE_GATES = ["./gradlew *", "nix eval *", "npm test"]


def _free_declaration() -> dict[str, object]:
    return {"agents": {"web": {"description": "A web app.", "gateCommands": list(FREE_GATES)}}}


@pytest.fixture(scope="module")
def free_hook() -> str:
    return _hook(_free_declaration())


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "./gradlew test jacocoTestReport -x processTestAot",
        "./gradlew test jacocoTestReport jacocoTestCoverageVerification -x processTestAot",
        "cd api && ./gradlew test spotlessCheck --info",
        "nix eval --raw .#x",
        "nix eval --raw .#packages.x86_64-linux.default.passthru.contentKey",
        "nix develop -c ./gradlew test -x processTestAot",
        "./gradlew test -x processTestAot 2>&1",
        "./gradlew test -x processTestAot > /dev/null",
    ],
)
def test_a_gate_that_ends_with_a_star_permits_each_word_after_it(free_hook, repo, shell, command):
    _assert_permitted(_guard(free_hook, repo, command, shell, repo.main), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command", ["./gradlew", "nix eval", "npm test", "npm test 3", "npm test -- 12 app/src/a.test.ts"]
)
def test_a_gate_alone_and_a_number_after_a_gate_are_permitted(free_hook, repo, shell, command):
    _assert_permitted(_guard(free_hook, repo, command, shell, repo.main), command, shell)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    ("command", "word"),
    [("npm test -- -u", "-u"), ("npm test 3a", "3a"), ("npm test -- 3 --fix", "--fix")],
)
def test_a_gate_with_no_star_keeps_the_word_check(free_hook, repo, shell, command, word):
    result = _guard(free_hook, repo, command, shell, repo.main)

    _assert_denied(result, command, shell)
    assert f"The word {word} after the gate [npm test]" in result.stderr, (shell, command, result.stderr)


#: A push after a gate. The text is in two parts, so that the push guard of the specialist that edits this file does
#: not read it as a push.
PUSH_AFTER_GATE = "./gradlew test; git " + "push"


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize(
    "command",
    [
        "./gradlew test > /Users/x/out.txt",
        "./gradlew test >> api/build.gradle",
        "nix eval --raw .#x > app/out.txt",
        "./gradlew test 2> /etc/out",
        "./gradlew test | tee /Users/x/out.txt",
        PUSH_AFTER_GATE,
        "./gradlewx test",
        "nix evaluate --raw .#x",
        "nix build .#x",
        "npm testx",
    ],
)
def test_a_gate_that_ends_with_a_star_keeps_the_segment_rules(free_hook, repo, shell, command):
    _assert_denied(_guard(free_hook, repo, command, shell, repo.main), command, shell)


def test_the_opencode_map_permits_a_star_gate_with_its_star_and_a_plain_gate_exactly():
    perm = _opencode_permission(_free_declaration())
    bash = perm["bash"]
    assert bash["./gradlew *"] == "allow"
    assert bash["nix eval *"] == "allow"
    assert bash["npm test"] == "allow"
    assert "npm test *" not in bash

    for command in ("./gradlew", "./gradlew test -x processTestAot", "nix eval --raw .#x", "npm test"):
        assert opencode_bash_action(perm, command) == "allow", command
    for command in ("npm test -- --run", "./gradlew test > /Users/x/o", "./gradlewx test", "nix build .#x"):
        assert opencode_bash_action(perm, command) == "deny", command


def test_the_declaration_carries_a_gate_that_ends_with_a_star():
    [agent] = parse_declaration(_free_declaration())
    assert agent.gate_commands == FREE_GATES


@pytest.mark.parametrize(
    "bad",
    ["*", " *", "npm test  *", "npm test **", "npm *test", 'uvx "zensical==$(cat .zensical-version)" build *'],
)
def test_the_declaration_refuses_a_star_gate_that_is_not_plain_words(bad):
    with pytest.raises(ValueError, match="gateCommands"):
        parse_declaration({"agents": {"web": {"description": "A web app.", "gateCommands": [bad]}}})


@pytest.mark.parametrize("bad", ["git *", "git " + "push *", "rm *", "cp *"])
def test_the_declaration_refuses_a_star_gate_that_opens_a_write(bad):
    with pytest.raises(ValueError, match="opens a write command"):
        parse_declaration({"agents": {"web": {"description": "A web app.", "gateCommands": [bad]}}})


def test_the_rendered_verifier_names_each_star_gate_and_says_that_its_words_are_not_checked():
    claude_code, opencode = _files(_free_declaration())
    for files in (claude_code, opencode):
        body = split_frontmatter(files["web-verifier.md"])[1]
        assert "The gate `./gradlew` takes free arguments." in body
        assert "The gate `nix eval` takes free arguments." in body
        assert "`npm test` takes free arguments" not in body
        assert "does not check the words after such a gate" in body
        assert "Do not put a word after such a gate that can make the gate write" in body

    for files in _files(_declaration()):
        assert "takes free arguments" not in split_frontmatter(files["web-verifier.md"])[1]


def test_the_validator_accepts_a_rendered_pair_with_a_star_gate(tmp_path):
    declaration = tmp_path / "agents.toml"
    declaration.write_text(
        '[agents.web]\ndescription = "A web app."\ngateCommands = ["./gradlew *", "nix eval *", "npm test"]\n'
    )
    out = tmp_path / "rendered"
    assert agent_definitions_main(["render", str(declaration), "-o", str(out)]) == 0
    assert agent_definitions_main(["validate", str(out)]) == 0
