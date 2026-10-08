from pathlib import Path

import pytest

from agent_definitions.declaration import load_declaration
from agent_definitions.render import render
from agent_definitions.tiers import load_tiers

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "java-spring.toml"


@pytest.fixture(scope="session")
def tiers():
    return load_tiers()


@pytest.fixture
def java_spring(tiers):
    return load_declaration(EXAMPLE)


@pytest.fixture
def rendered(tmp_path, java_spring, tiers):
    """Render the example to disk: tmp/claude-code/*.md and tmp/opencode/*.md."""
    out = render(java_spring, tiers)
    for harness, files in out.items():
        d = tmp_path / harness
        d.mkdir()
        for name, content in files.items():
            (d / name).write_text(content)
    return tmp_path
