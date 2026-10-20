"""The committed agent pair of this repository is the render of the current code (#162).

The repository commits its own specialist-verifier pair in `.claude/agents/`
and `.opencode/agents/`. The pair is a render of the declaration in
`.claude/skills/delegate-context/agents.toml`. When the renderer changes, as a
change to the verifier guard does, the committed pair must be rendered again.
Before #162, no test failed when the pair was an older render, so the own
verifier of this repository kept a guard that the renderer had fixed.

This test renders the declaration with `agent-definitions render` and compares
each written file, byte for byte, with the committed file.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from delegate.cli.agent_definitions import main

from .pages import ROOT, outside_the_package

DECLARATION = Path(".claude/skills/delegate-context/agents.toml")

#: The subdirectory that `agent-definitions render` writes for each harness, and the committed directory of that harness.
COMMITTED = {
    "claude-code": Path(".claude/agents"),
    "opencode": Path(".opencode/agents"),
}

# The committed agents are outside the package source, as in a Nix build.
pytestmark = outside_the_package


def assert_committed_pair_is_the_render(root: Path, rendered: Path) -> None:
    """Each file that the render wrote to `rendered` is byte-identical to the committed file under `root`."""
    drifted = []
    for harness, committed in COMMITTED.items():
        written = {path.name: path.read_bytes() for path in sorted((rendered / harness).iterdir())}
        present = {path.name: path.read_bytes() for path in sorted((root / committed).iterdir())}
        for name in sorted(written.keys() | present.keys()):
            if written.get(name) != present.get(name):
                drifted.append((committed / name).as_posix())

    assert not drifted, (
        f"the committed agent files {', '.join(drifted)} are not the render of the current code. "
        f"Run `agent-definitions render {DECLARATION.as_posix()} -o <dir>`, "
        "copy <dir>/claude-code/* to .claude/agents/ and <dir>/opencode/* to .opencode/agents/, and commit them."
    )


@pytest.fixture
def rendered(tmp_path: Path) -> Path:
    out = tmp_path / "rendered"
    assert main(["render", str(ROOT / DECLARATION), "-o", str(out)]) == 0
    return out


def test_the_committed_agent_pair_is_the_render_of_the_current_code(rendered):
    assert_committed_pair_is_the_render(ROOT, rendered)


def test_one_changed_line_of_a_committed_agent_file_fails_the_comparison(rendered, tmp_path):
    # A repository whose committed pair is the render, so that only the changed line differs.
    copy = tmp_path / "copy"
    for harness, committed in COMMITTED.items():
        shutil.copytree(rendered / harness, copy / committed)
    verifier = copy / COMMITTED["opencode"] / "delegate-verifier.md"
    lines = verifier.read_text().splitlines(keepends=True)
    lines[-1] = lines[-1].rstrip("\n") + " (changed by hand)\n"
    verifier.write_text("".join(lines))

    with pytest.raises(AssertionError) as failure:
        assert_committed_pair_is_the_render(copy, rendered)

    message = str(failure.value)
    assert ".opencode/agents/delegate-verifier.md" in message
    assert ".claude/agents/delegate-verifier.md" not in message
    assert f"agent-definitions render {DECLARATION.as_posix()}" in message
