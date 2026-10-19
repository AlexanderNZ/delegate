"""The verifier pair of this repository permits each gate of its own delegation document (#122).

`docs/agents/delegation.md` names the gates of this repository. The verifier
must run them, so the committed verifier guard must permit each one. The
declaration in `.claude/skills/delegate-context/agents.toml` lists the gate
commands, and the committed files in `.claude/agents/` and `.opencode/agents/`
are rendered from it. A gate that the document adds, and that the declaration
or the committed pair does not have, makes this test fail.

The gates are the lines of each fenced block of the section
`## Verification gates`, and each code span that the section names as a gate
("gate: `...`"). The Claude Code guard runs in bash, /bin/sh and zsh, as the
harness does. The OpenCode map cannot hold a gate with a command substitution
(#75), so the OpenCode check skips such a gate.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from delegate.definitions.validate import opencode_bash_action, split_frontmatter

ROOT = Path(__file__).resolve().parents[1]
DELEGATION_DOC = ROOT / "docs" / "agents" / "delegation.md"
CLAUDE_VERIFIER = ROOT / ".claude" / "agents" / "delegate-verifier.md"
OPENCODE_VERIFIER = ROOT / ".opencode" / "agents" / "delegate-verifier.md"

# The docs and the committed agent files are outside the package source, as in a Nix build.
if not DELEGATION_DOC.is_file():
    pytest.skip("the docs are outside the package source, as in a Nix build", allow_module_level=True)

SHELLS = (
    "bash",
    "/bin/sh",
    pytest.param("zsh", marks=pytest.mark.skipif(shutil.which("zsh") is None, reason="zsh is not on PATH")),
)


def _gates_section(text: str) -> str:
    match = re.search(r"^## Verification gates\n(.*?)(?=^## )", text, re.M | re.S)
    assert match, "docs/agents/delegation.md has no section '## Verification gates'"
    return match.group(1)


def _fenced_gates(section: str) -> list[str]:
    blocks = re.findall(r"^```[a-z]*\n(.*?)^```", section, re.M | re.S)
    return [line.strip() for block in blocks for line in block.splitlines() if line.strip()]


def _named_gates(section: str) -> list[str]:
    return re.findall(r"gate: `([^`]+)`", section)


FENCED = _fenced_gates(_gates_section(DELEGATION_DOC.read_text()))
NAMED = _named_gates(_gates_section(DELEGATION_DOC.read_text()))
GATES = [*FENCED, *NAMED]


def test_the_delegation_document_names_gates_in_a_block_and_in_the_text():
    # If the shape of the document changes, the parse must not find no gate and let the test pass.
    assert FENCED, "no gate in a fenced block of '## Verification gates'"
    assert NAMED, "no code span named as a gate in '## Verification gates'"


def _claude_guard() -> str:
    fm, _body, err = split_frontmatter(CLAUDE_VERIFIER.read_text())
    assert err is None, err
    [entry] = [e for e in fm["hooks"]["PreToolUse"] if e["matcher"] == "Bash"]
    return entry["hooks"][0]["command"]


def _run(hook: str, command: str, shell: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    argv, executable = [shutil.which(shell) or shell, "-c", hook], None
    if shell == "/bin/sh" and sys.platform != "darwin":
        argv[0], executable = "sh", shutil.which("bash")
    return subprocess.run(argv, executable=executable, input=payload, capture_output=True, text=True, cwd=ROOT)


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("gate", GATES)
def test_the_committed_claude_code_verifier_permits_each_gate_of_the_document(gate, shell):
    r = _run(_claude_guard(), gate, shell)
    assert r.returncode == 0, (shell, gate, r.stderr)


@pytest.mark.parametrize("gate", [g for g in GATES if "$(" not in g])
def test_the_committed_opencode_verifier_allows_each_gate_without_a_substitution(gate):
    fm, _body, err = split_frontmatter(OPENCODE_VERIFIER.read_text())
    assert err is None, err
    assert opencode_bash_action(fm["permission"], gate) == "allow", gate
