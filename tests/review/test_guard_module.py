"""The compiler of the verifier guard is its own module of the definitions context (#336).

`definitions/render.py` held two programs: the compiler of the guards (the Bash guard and the Write guard of a
verifier, the push guard of a specialist, and the OpenCode permission maps) and the renderer of the agent files. The
guards are now in `definitions/guard.py`, and the renderer imports them. The move changes no rendered file.

The snapshots in `tests/fixtures/render-snapshots/` are the files that `agent-definitions render` wrote for each
declaration before the move. When a later change of the renderer or of the guard changes a rendered file on purpose,
render the declaration again into its snapshot directory, and read the diff.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from delegate.cli.agent_definitions import main
from delegate.definitions import guard
from delegate.definitions.declaration import load_declaration

TESTS = Path(__file__).resolve().parents[1]
SNAPSHOTS = TESTS / "fixtures" / "render-snapshots"

#: Each declaration of the snapshot test, and the directory of its snapshot.
DECLARATIONS = {
    "java-spring": TESTS.parent / "examples" / "java-spring.toml",
    "guard-forms": SNAPSHOTS / "guard-forms.toml",
}


def files_under(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


@pytest.mark.parametrize("name", sorted(DECLARATIONS))
def test_render_writes_the_same_bytes_as_before_the_guard_moved(tmp_path, name):
    out = tmp_path / name

    code = main(["render", str(DECLARATIONS[name]), "-o", str(out)])

    assert code == 0
    expected = files_under(SNAPSHOTS / name)
    written = files_under(out)
    assert sorted(written) == sorted(expected)
    assert [file for file in expected if written[file] != expected[file]] == []


def frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])


def bash_hook(path: Path) -> str:
    [entry] = [e for e in frontmatter(path)["hooks"]["PreToolUse"] if e["matcher"] == "Bash"]
    return entry["hooks"][0]["command"]


def test_the_guard_module_builds_the_bash_guard_of_each_rendered_verifier():
    agents = {agent.key: agent for agent in load_declaration(DECLARATIONS["guard-forms"])}
    rendered = SNAPSHOTS / "guard-forms" / "claude-code"

    for key, file in (("forms", "forms-verifier.md"), ("python", "python-verifier.md"), ("reviewer", "reviewer.md")):
        agent = agents[key]
        hook = guard.verifier_bash_hook(
            tuple(agent.gate_commands), tuple(agent.get_only_commands), tuple(agent.gate_extra_flags)
        )
        assert hook == bash_hook(rendered / file), key


def test_the_guard_module_builds_the_push_guard_of_each_rendered_specialist():
    rendered = SNAPSHOTS / "java-spring" / "claude-code" / "java-spring-specialist.md"

    assert guard.GIT_PUSH_HOOK == bash_hook(rendered)
    assert "git push is blocked for specialist agents" in guard.GIT_PUSH_HOOK


def test_the_guard_module_builds_the_opencode_bash_map_of_each_rendered_verifier():
    rendered = SNAPSHOTS / "java-spring" / "opencode" / "java-spring-verifier.md"
    permission = frontmatter(rendered)["permission"]

    assert guard.verifier_bash_permission(("./gradlew test", "./gradlew check")) == permission["bash"]
    assert permission["bash"]["./gradlew test"] == "allow"
    assert permission["bash"]["*>*"] == "deny"
    assert guard.verifier_external_directory_permission() == permission["external_directory"]
