"""The verifier prose agrees with the toplevel rule of #106, and the break-a-copy recipe passes the guard (#113).

Since #106, both verifier guards deny a write in the git toplevel of the
working directory, also in a temp directory. The rendered verifier said that a
write in a temp directory is permitted, which is wider than the guard. Its
recipe also told the verifier to go to the copy first and remove the `.git`
entry of the copy after that. Then the copy is the git toplevel of the working
directory, and the guard denies the removal. The recipe now makes the copy and
removes its `.git` entry in one command, from the working directory of the
verifier, and goes to the copy only after that.

Each recipe test renders the example declaration, takes each step from the
rendered body or from the verifier brief in the order of the text, runs the
rendered Bash guard on it in bash, /bin/sh and zsh with the working directory
of that step, and then runs the step, as the harness does.

OpenCode has no map rule for the tree under review (measured on OpenCode
1.15.10 on 2026-10-19), so the rendered OpenCode verifier and the enforcement
page state that limit.
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

from delegate import adapters
from delegate.cli import delegate
from delegate.definitions.validate import split_frontmatter
from delegate.run.brief import verifier_run_sections

from .pages import outside_the_package
from .support import ScriptedAdapter, git, make_repo, read_journal

ROOT = Path(__file__).resolve().parents[1]
ENFORCEMENT_PAGE = ROOT / "docs" / "explanation" / "the-enforcement-model-and-its-limits.md"

#: Each shell that runs the Bash guard of the harness.
SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)

#: The fixed temp prefixes of the guards, without a /private prefix.
TEMP_TEXT = ("/tmp/", "/var/folders/")

#: The toplevel rule of #106, as the rendered OpenCode verifier states it.
TOPLEVEL_RULE = "outside the git toplevel of your working directory"

#: The full rule of the Claude Code guards since #125, as the rendered verifier states it.
FULL_RULE = "in a temp directory, not inside a git tree, and not holding one"

#: The recipe of the rendered Claude Code verifier, in the order of the text. `<tree>` is the tree to copy, and
#: `/tmp/x` is a new temp path.
BODY_STEPS = ("cp -R <tree> /tmp/x && rm -rf /tmp/x/.git", "cd /tmp/x")


def _body(rendered: Path, harness: str) -> str:
    _fm, body, err = split_frontmatter((rendered / harness / "java-spring-verifier.md").read_text())
    assert err is None, err
    return body


def _section(text: str, title: str) -> str:
    """The text of the section `## <title>` of `text`, up to the next section."""
    return text.split(f"\n## {title}\n", 1)[1].split("\n## ", 1)[0]


def _bash_hook(rendered: Path) -> str:
    fm, _body, err = split_frontmatter((rendered / "claude-code" / "java-spring-verifier.md").read_text())
    assert err is None, err
    (entry,) = [e for e in fm["hooks"]["PreToolUse"] if e["matcher"] == "Bash"]
    return entry["hooks"][0]["command"]


def _without_private(path: str) -> str:
    return path[len("/private"):] if path.startswith("/private/") else path


@pytest.fixture
def base(tmp_path):
    """A real directory whose path starts with a fixed temp prefix as text, after a /private prefix is removed.

    The pytest temp directory has such a path on macOS and on a Linux CI host.
    A Nix build sandbox gives /build, so the directory then goes in /tmp.
    """
    path = Path(os.path.realpath(tmp_path))
    made = None
    if not _without_private(str(path)).startswith(TEMP_TEXT):
        if not os.access("/tmp", os.W_OK):
            pytest.skip("no writable directory with a fixed temp prefix")
        made = path = Path(os.path.realpath(tempfile.mkdtemp(dir="/tmp")))
    # The path as `mkdtemp` gives it on macOS, without the /private prefix of the real path. The guards take only
    # this spelling as a temp path.
    yield Path(_without_private(str(path)))
    if made is not None:
        shutil.rmtree(made, ignore_errors=True)


def _repository(base: Path) -> Path:
    main = base / "main"
    main.mkdir()
    git(main, "init", "-q", "-b", "main")
    (main / "app.py").write_text("print('hi')\n")
    git(main, "add", "app.py")
    git(main, "commit", "-q", "-m", "seed")
    return main


def _guard(hook: str, shell: str, command: str, cwd: Path, base: Path) -> subprocess.CompletedProcess:
    """Run the Bash guard on `command`, with `cwd` as the working directory of the session and of the process."""
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        # The Linux Nix build sandbox's /bin/sh is busybox. Run bash as `sh`, the shell of a NixOS host.
        argv[0], executable = "sh", shutil.which("bash")
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    return subprocess.run(
        argv, executable=executable, input=payload, capture_output=True, text=True,
        env={**os.environ, "TMPDIR": f"{base}/"}, cwd=cwd,
    )


def _follow(hook: str, shell: str, base: Path, start: Path, copy: Path, steps: list[str]) -> None:
    """Give each step to the guard, then run it. A `cd` moves the working directory of the next step."""
    cwd = start
    for command in steps:
        verdict = _guard(hook, shell, command, cwd, base)
        assert verdict.returncode == 0, (shell, str(cwd), command, verdict.stderr)
        ran = subprocess.run(["bash", "-c", command], cwd=cwd, capture_output=True, text=True)
        assert ran.returncode == 0, (command, ran.stderr)
        if command.startswith("cd "):
            cwd = Path(command[len("cd "):])
    assert cwd == copy, "the recipe does not go to the copy"
    assert not (copy / ".git").exists(), "the copy keeps its .git entry"
    for command in (f"echo broken > {copy}/app.py", f"cat {copy}/app.py", f"rm {copy}/app.py"):
        verdict = _guard(hook, shell, command, cwd, base)
        assert verdict.returncode == 0, (shell, str(cwd), command, verdict.stderr)


def _in_order(text: str, steps: list[str]) -> None:
    positions = [text.find(f"`{step}`") for step in steps]
    assert -1 not in positions, (steps, positions)
    assert positions == sorted(positions), "the steps are not in the order of the recipe"


# --- The toplevel rule --------------------------------------------------------


def test_the_claude_code_verifier_states_the_full_rule_for_the_shell_and_for_the_write_tool(rendered):
    body = _body(rendered, "claude-code")

    for title in ("Your shell", "Your Write tool"):
        assert FULL_RULE in _section(body, title)
        # The guards look for the .git entry from each target, not only from the working directory (#125).
        assert "from each write target" in _section(body, title)
    # The old text gave the temp rule alone, which is wider than the guard.
    assert "a write whose every path argument is in a temp directory." not in body


def test_the_opencode_verifier_states_the_toplevel_rule_for_the_write_tool(rendered):
    assert TOPLEVEL_RULE in _section(_body(rendered, "opencode"), "Your write tool")


# --- The recipe ---------------------------------------------------------------


@pytest.mark.parametrize("shell", SHELLS)
def test_each_step_of_the_recipe_of_the_rendered_verifier_passes_the_guard(rendered, base, shell):
    # The tree is a git worktree, so its .git entry is a pointer file.
    tree = base / "wt"
    git(_repository(base), "worktree", "add", "-q", "-b", "feature", str(tree))
    copy = base / "x"
    body = _section(_body(rendered, "claude-code"), "Your shell")
    _in_order(body, list(BODY_STEPS))

    steps = [step.replace("<tree>", str(tree)).replace("/tmp/x", str(copy)) for step in BODY_STEPS]
    _follow(_bash_hook(rendered), shell, base, tree, copy, steps)


@pytest.mark.parametrize("shell", SHELLS)
def test_each_step_of_the_recipe_of_the_verifier_brief_passes_the_guard(rendered, base, shell):
    # The engine clones the branch into <scratch>/copy, and the copy is the working directory of the verifier.
    scratch = base / "delegate-verify-x"
    scratch.mkdir()
    copy = scratch / "copy"
    git(base, "clone", "-q", str(_repository(base)), str(copy))
    brief = verifier_run_sections(copy, scratch / "verdict.json", "full")
    proof = scratch / "proof"
    steps = [f"cp -R {copy} {proof} && rm -rf {proof}/.git", f"cd {proof}"]
    _in_order(brief, steps)

    _follow(_bash_hook(rendered), shell, base, copy, proof, steps)


def test_the_engine_removes_the_second_copy_of_a_red_proof_and_keeps_the_other_files_of_the_verifier(tmp_path, capsys):
    # The verifier follows the recipe of its brief: it makes the second copy, breaks it, and writes a note beside its
    # report. The engine moves the files beside the report into the evidence directory, but not a copy of the tree.
    seen: dict[str, Path] = {}

    def red_proof(request):
        (command,) = re.findall(r"`(cp -R [^`]*)`", request.prompt)
        subprocess.run(["bash", "-c", command], cwd=request.cwd, check=True, capture_output=True)
        proof = Path(command.split(" && ", 1)[0].split()[-1])
        (proof / "feature.txt").write_text("broken by the verifier\n")
        (request.report_path.parent / "notes.txt").write_text("the red proof\n")
        seen["proof"] = proof

    scripted = ScriptedAdapter(during_verifier=red_proof)
    adapters.register("scripted", scripted)
    try:
        repo = make_repo(tmp_path)
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
        out, err = capsys.readouterr()
    finally:
        adapters.unregister("scripted")

    assert (code, err) == (0, "")
    journal = [line.removeprefix("journal ") for line in out.splitlines() if line.startswith("journal ")]
    evidence = Path(journal[0]).parent / "verifier" / "a"
    assert (evidence / "notes.txt").read_text() == "the red proof\n"
    kept = sorted(p.relative_to(evidence).as_posix() for p in evidence.rglob("*"))
    assert not [name for name in kept if name.endswith("feature.txt") or name.endswith("workflow.toml")], kept
    assert not seen["proof"].exists()
    assert read_journal(out)[-1]["event"] == "run-end"


@pytest.mark.parametrize("shell", SHELLS)
def test_the_guard_denies_the_removal_of_the_git_entry_after_a_cd_to_the_copy(rendered, base, shell):
    # The old order of the recipe: the copy is the git toplevel of the working directory when the removal comes.
    tree = base / "wt"
    git(_repository(base), "worktree", "add", "-q", "-b", "feature", str(tree))
    copy = base / "x"
    shutil.copytree(tree, copy)

    verdict = _guard(_bash_hook(rendered), shell, f"rm -rf {copy}/.git", copy, base)

    assert verdict.returncode == 2, verdict.stderr
    assert (copy / ".git").exists()


def test_the_brief_and_the_body_tell_the_verifier_not_to_go_to_the_copy_first(rendered, tmp_path):
    brief = verifier_run_sections(tmp_path / "copy", tmp_path / "verdict.json", "full")
    for text in (_section(_body(rendered, "claude-code"), "Your shell"), brief):
        assert "copy before you remove its `.git` entry" in text


# --- OpenCode -----------------------------------------------------------------


def test_the_opencode_verifier_states_that_its_maps_cannot_deny_the_tree_through_a_link(rendered):
    section = _section(_body(rendered, "opencode"), "Your write tool")

    assert "symbolic link" in section
    assert "OpenCode 1.15.10" in section
    assert "2026-10-19" in section


# The docs are outside the package source, as in a Nix build.
@outside_the_package
def test_the_enforcement_page_states_the_opencode_limit_with_its_evidence():
    section = ENFORCEMENT_PAGE.read_text().split("## The limits of the guards", 1)[1]
    (item,) = [line for line in section.splitlines() if "the real path of the project" in line]

    assert "OpenCode 1.15.10" in item
    assert "2026-10-19" in item
    assert "/var/folders" in item
