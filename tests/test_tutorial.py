"""The tutorial: a new reader can follow it word for word and reach a verdict.

The pages are outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

import re
import shlex
import subprocess
from pathlib import Path

import pytest

from delegate import adapters, delegate

from .support import ScriptedAdapter
from .pages import repository_root

ROOT = repository_root(Path(__file__))
README = ROOT / "README.md"  # its presence marks a source tree with the docs
TUTORIAL = ROOT / "docs" / "tutorial.md"
SAMPLE = ROOT / "examples" / "tutorial"

outside_the_package = pytest.mark.skipif(
    not README.is_file(), reason="the docs are outside the package source, as in a Nix build"
)

# The two steps that need a network or a package index. A test cannot follow them.
NOT_FOLLOWED = ("uv tool install", "git clone")

# What the scripted adapter commits for the sample ticket, in place of a live specialist.
WORDS_AFTER = (
    "def count_words(text: str) -> int:\n"
    "    return len(text.split())\n"
    "\n"
    "\n"
    "def count_lines(text: str) -> int:\n"
    "    return len(text.splitlines())\n"
)
TESTS_AFTER = (
    "import unittest\n"
    "\n"
    "from words import count_lines, count_words\n"
    "\n"
    "\n"
    "class WordsTest(unittest.TestCase):\n"
    "    def test_count_words(self):\n"
    "        self.assertEqual(count_words('one two  three'), 3)\n"
    "\n"
    "    def test_count_lines(self):\n"
    "        self.assertEqual(count_lines('a\\nb\\nc\\n'), 3)\n"
)


def commands(markdown: str) -> list[str]:
    """The commands of the bash blocks of a page, in order. A trailing backslash joins two lines."""
    found: list[str] = []
    for block in re.findall(r"```bash\n(.*?)```", markdown, flags=re.DOTALL):
        found += block.replace("\\\n", " ").splitlines()
    return [line.strip() for line in found if line.strip() and not line.strip().startswith("#")]


@outside_the_package
def test_an_agent_that_follows_the_tutorial_word_for_word_reaches_an_accept_verdict(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "delegate-kit").symlink_to(ROOT)  # the clone that the tutorial's git clone step makes
    scripted = ScriptedAdapter(files={"words.py": WORDS_AFTER, "test_words.py": TESTS_AFTER})
    adapters.register("claude-code", scripted)  # the scripted adapter in place of the live harness
    cwd = work
    delegate_exits: list[int] = []
    outputs: list[str] = []
    try:
        for command in commands(TUTORIAL.read_text()):
            if command.startswith(NOT_FOLLOWED):
                continue
            if command.startswith("cd "):
                cwd = (cwd / command.removeprefix("cd ")).resolve()
            elif command.startswith("delegate "):
                monkeypatch.chdir(cwd)
                try:
                    delegate_exits.append(delegate.main(shlex.split(command)[1:]))
                except SystemExit as stop:  # `delegate --help` exits through argparse
                    delegate_exits.append(stop.code or 0)
            else:
                done = subprocess.run(["bash", "-ec", command], cwd=cwd, capture_output=True, text=True)
                assert done.returncode == 0, (command, done.stdout, done.stderr)
                outputs.append(done.stdout)
    finally:
        adapters.unregister("claude-code")
    sample = work / "sample"

    assert delegate_exits and set(delegate_exits) == {0}
    # The workflow names the agents that bootstrap wrote.
    assert (sample / ".claude" / "agents" / "python-specialist.md").is_file()
    assert (sample / ".claude" / "agents" / "python-verifier.md").is_file()
    assert [call.agent for call in scripted.calls] == ["python-specialist"]
    assert [call.agent for call in scripted.verifier_calls] == ["python-verifier"]
    assert "count_lines" in scripted.calls[0].prompt  # the text of ticket.md reached the specialist
    verdicts = [out for out in outputs if '"verdict"' in out]
    assert len(verdicts) == 1 and '"verdict": "ACCEPT"' in verdicts[0]
    log = subprocess.run(["git", "-C", str(sample), "log", "--format=%s", "main"], capture_output=True, text=True).stdout
    assert "scripted work (python-specialist)" in log.splitlines()  # the last step of the tutorial merged the run branch
    # The engine builds in a worktree that starts from main, so the agent files must be in the first commit.
    first = subprocess.run(["git", "-C", str(sample), "rev-list", "--max-parents=0", "main"], capture_output=True, text=True).stdout.strip()
    tracked = subprocess.run(["git", "-C", str(sample), "ls-tree", "-r", "--name-only", first], capture_output=True, text=True).stdout.split()
    assert {".claude/agents/python-specialist.md", ".claude/agents/python-verifier.md", "workflow.toml"} <= set(tracked)


@outside_the_package
def test_the_sample_workflow_passes_the_dry_run(capsys):
    code = delegate.main(["run", str(SAMPLE / "workflow.toml"), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    assert "ticket 1 " in out


@outside_the_package
def test_the_tutorial_prose_keeps_each_sentence_to_25_words_or_fewer():
    text = re.sub(r"```.*?```", "", TUTORIAL.read_text(), flags=re.DOTALL)
    prose = [line for line in text.splitlines() if line.strip() and not line.startswith(("#", "|", "<!--"))]
    sentences = re.split(r"(?<=[.:?!])\s+", " ".join(prose))
    too_long = [s for s in sentences if len(s.split()) > 25]
    assert too_long == []
