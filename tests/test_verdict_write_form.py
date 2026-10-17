"""The verifier brief names one command form that writes the verdict, and the verifier guard permits it (#98).

The guard divides a command at each newline, so it refuses every heredoc, and
it refuses each `$(` and each backtick, also inside quotes. A verdict names files
and code, so its text often holds both. The brief therefore tells the verifier to
write the verdict with `printf '%s\\n' '<json>' > <path>`, with `$`, the backtick
and the apostrophe written as JSON escapes. A JSON parser reads them back as the
same characters.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from delegate.definitions.render import VERIFIER_BASH_HOOK
from delegate.run.brief import verifier_run_sections
from delegate.run.reports import read_verifier_report, verifier_example

# The characters that the brief tells the verifier to write as JSON escapes.
ESCAPES: dict[str, str] = {
    "$": "\\u0024", "`": "\\u0060", "'": "\\u0027", "<": "\\u003c", ">": "\\u003e", "&": "\\u0026",
}


def guard(command: str, tmp: Path) -> subprocess.CompletedProcess:
    """Run the rendered verifier guard on a Bash tool input, as Claude Code does."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(
        ["bash", "-c", VERIFIER_BASH_HOOK], input=payload, capture_output=True, text=True,
        env={**os.environ, "TMPDIR": f"{tmp}/"},
    )


def the_form(verdict: dict[str, object], path: Path) -> str:
    """The command that a verifier writes when it follows the brief."""
    text = json.dumps(verdict)  # one line, as the brief asks
    for character, escape in ESCAPES.items():
        text = text.replace(character, escape)
    return f"printf '%s\\n' '{text}' > {path}"


@pytest.mark.parametrize("mode", ["full", "fix-up"])
def test_the_verifier_brief_names_the_printf_form_and_the_escapes(mode, tmp_path):
    brief = verifier_run_sections(tmp_path / "copy", tmp_path / "verdict.json", mode)

    assert "printf '%s\\n' '<the JSON object>' >" in brief
    assert all(escape in brief for escape in ESCAPES.values())
    assert "heredoc" in brief
    assert "one line" in brief


def test_a_verdict_with_a_backtick_a_substitution_and_an_apostrophe_passes_the_guard_and_reads_back_unchanged(tmp_path):
    verdict = verifier_example("full")
    evidence = "`src/x.py:3` runs $(git log) && a > b, and the user's <file>"
    verdict["criteria"] = [{"criterion": "the guard", "evidence": evidence}]
    # The guard sees a report path in a temp directory, as the engine gives it. The same command then runs
    # against a file of this test, so the test needs no write in /tmp (the Nix build sandbox has its own TMPDIR).
    checked = guard(the_form(verdict, Path("/tmp/delegate-verify-x/verdict.json")), tmp_path)
    path = tmp_path / "verdict.json"
    subprocess.run(["bash", "-c", the_form(verdict, path)], check=True)

    assert checked.returncode == 0, checked.stderr
    report = read_verifier_report(path, "full")
    assert report.criteria[0]["evidence"] == evidence


def test_a_heredoc_that_writes_the_verdict_is_refused_by_the_guard(tmp_path):
    # Why the brief names the printf form: the guard reads each line of a heredoc body as a command.
    command = "cat > /tmp/delegate-verify-x/verdict.json <<'EOF'\n{\"verdict\": \"ACCEPT\"}\nEOF"

    assert guard(command, tmp_path).returncode == 2

