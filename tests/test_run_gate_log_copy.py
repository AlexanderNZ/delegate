"""A brief after red gates names a copy of the full gate log in the scratch directory of the session (#246).

The brief gives only the end of the output of each red gate. When a test runner prints many failures, that end can
hold only the last of them. The engine keeps the full output in the gate log of the run, outside the worktree, and a
host can refuse a read there. So for each red gate the engine copies the gate log into the scratch directory of the
session before it writes the brief, and the brief names the copy, with the gate command and the size in lines.

Each case runs `delegate.main` against a real temporary git repository. Each gate is a real shell command that prints
a marker line, then more than `GATE_OUTPUT_TAIL_CHARS` characters, and fails. The scripted adapter plays the agents.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from delegate.cli import delegate

from .support import WORKFLOW, make_repo, read_journal, state_dir

MARKER = "GATE-LOG-MARKER-2461 the first failure"

# The marker line, then the numbers 1 to 2000: 2001 lines, and more characters than the brief keeps. The shell computes
# the number of the marker, so the gate command, which the brief also names, does not hold the marker line.
LOUD = 'echo "GATE-LOG-MARKER-$((2400 + 61)) the first failure"; seq 1 2000'
LOUD_LINES = 2001


@pytest.fixture
def scripted(scripted):
    """The scripted adapter of conftest, with the default files: ticket a writes `feature.txt`."""
    scripted.files_by_ticket = {}
    return scripted


def with_gate(gate: str, text: str = WORKFLOW) -> str:
    """`text` with `gate` as the one gate of the stack, in a TOML literal string."""
    return text.replace('gates = ["test -f feature.txt"]', f"gates = ['{gate}']")


def run(repo: Path, capsys) -> tuple[int, str, str]:
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def copies_named(prompt: str, directory: Path) -> list[Path]:
    """Each path in backticks in `prompt` that is a file under `directory`."""
    return [Path(text) for text in re.findall(r"`([^`\n]+)`", prompt) if Path(text).parent == directory and Path(text).is_file()]


def copy_line(prompt: str, copy: Path) -> str:
    return next(line for line in prompt.splitlines() if f"`{copy}`" in line)


def test_a_continuation_after_a_red_gate_names_a_copy_of_the_full_gate_log_in_the_scratch_directory(tmp_path, capsys, scripted):
    gate = f"test -f second.txt || {{ {LOUD}; exit 1; }}"
    scripted.continuation_files = [{"second.txt": "second\n"}]
    repo = make_repo(tmp_path, with_gate(gate))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (continuation,) = scripted.continuation_calls
    assert MARKER not in continuation.prompt  # the tail in the brief lost the first failure
    assert "seq 1 2000" in continuation.prompt.split("```")[1]  # the tail stays in the brief
    run_id = str(read_journal(out)[0]["run_id"])
    directory = state_dir(repo) / "runs" / run_id / "scratch" / "a" / "specialist"
    (copy,) = copies_named(continuation.prompt, directory)
    assert copy.read_text().splitlines()[0] == MARKER
    assert len(copy.read_text().splitlines()) == LOUD_LINES
    line = copy_line(continuation.prompt, copy)
    assert f"`{gate}`" in line and f"{LOUD_LINES} lines" in line


def test_a_fixup_after_red_chain_gates_in_economy_mode_names_a_copy_of_the_full_gate_log_in_its_scratch_directory(
    tmp_path, capsys, scripted
):
    # The gate is red until the fix-up specialist adds fixup-1.txt.
    gate = f"test -f fixup-1.txt || {{ {LOUD}; exit 1; }}"
    repo = make_repo(tmp_path, with_gate(gate, WORKFLOW.replace('mode = "assure"', 'mode = "economy"')))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (fixup,) = scripted.fixup_calls
    assert MARKER not in fixup.prompt
    run_id = str(read_journal(out)[0]["run_id"])
    directory = state_dir(repo) / "runs" / run_id / "scratch" / "a" / "fix-up"
    (copy,) = copies_named(fixup.prompt, directory)
    assert copy.read_text().splitlines()[0] == MARKER
    line = copy_line(fixup.prompt, copy)
    assert f"`{gate}`" in line and f"{LOUD_LINES} lines" in line


def test_a_timed_out_gate_gets_a_copy_of_its_full_log_and_the_brief_still_names_the_limit(tmp_path, capsys, scripted):
    gate = f"test -f second.txt || {{ {LOUD}; sleep 30; }}"
    scripted.continuation_files = [{"second.txt": "second\n"}]
    repo = make_repo(tmp_path, with_gate(gate).replace("gate-timeout = 600", "gate-timeout = 0.05"))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (continuation,) = scripted.continuation_calls
    assert "gate-timeout of 0.05 minutes" in continuation.prompt
    run_id = str(read_journal(out)[0]["run_id"])
    directory = state_dir(repo) / "runs" / run_id / "scratch" / "a" / "specialist"
    (copy,) = copies_named(continuation.prompt, directory)
    assert copy.read_text().splitlines()[0] == MARKER
