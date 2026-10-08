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


@pytest.fixture
def git_identity(monkeypatch):
    """The rebase writes commits, so git needs a committer. This sets the environment of the process, which is the real boundary."""
    for key, value in (("NAME", "Scratch"), ("EMAIL", "scratch@example.invalid")):
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)


@pytest.fixture
def scripted(git_identity):
    """A scripted adapter registered as `scripted`. Each ticket a to d writes its own file."""
    from agent_definitions import adapters

    from .support import ScriptedAdapter

    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abcd"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")
