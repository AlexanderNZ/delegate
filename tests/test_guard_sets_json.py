"""`agent-definitions render --sets-json`: the guard sets of each verifier as JSON (#237).

A consumer read the gate set and the read set from the shell text of the
rendered guard, and v1.14.0 changed that text. The JSON output is the stable
interface: each case drives `main(argv)` and reads the sets from stdout.
"""

import json

import pytest

from delegate.cli import agent_definitions

DECLARATION = """\
[agents.app]
description = "A small app."
tier = "standard"
skills = ["tdd"]
gateCommands = ["make test", "./gradlew *", "uvx \\"zensical==$(cat .zensical-version)\\" build --strict"]
getOnlyCommands = ["gh issue view", "tea issues list *"]

[agents.solo]
description = "A specialist with no verifier."
pair = false
gateCommands = ["make test"]

[agents.review]
description = "A verifier with no specialist."
pair = false
readOnly = true
getOnlyCommands = ["gh pr view"]
"""


@pytest.fixture
def declaration(tmp_path):
    path = tmp_path / "agents.toml"
    path.write_text(DECLARATION)
    return path


def sets_json(capsys, *argv: str) -> tuple[int, dict]:
    code = agent_definitions.main(["render", *argv, "--sets-json"])
    return code, json.loads(capsys.readouterr().out)


def test_the_json_names_the_plain_gate_the_free_gate_and_the_read_command_of_the_verifier(capsys, declaration):
    code, sets = sets_json(capsys, str(declaration))

    assert code == 0
    app = sets["agents"]["app-verifier"]
    assert app["gates"] == ["make test", "./gradlew", 'uvx "zensical==$(cat .zensical-version)" build --strict']
    assert app["freeGates"] == ["./gradlew"]
    assert app["reads"] == ["gh issue view", "tea issues list"]
    assert app["readPrefixes"] == ["tea issues list"]


def test_the_json_has_a_version_field_of_1_and_only_the_agents_that_carry_a_guard(capsys, declaration):
    code, sets = sets_json(capsys, str(declaration))

    assert code == 0
    assert sets["version"] == 1
    assert sorted(sets["agents"]) == ["app-verifier", "review"]
    assert sets["agents"]["review"] == {"gates": [], "freeGates": [], "reads": ["gh pr view"], "readPrefixes": []}


def test_the_sets_json_writes_no_agent_file(capsys, declaration, tmp_path):
    before = sorted(p.name for p in tmp_path.rglob("*"))

    code, _sets = sets_json(capsys, str(declaration))

    assert code == 0
    assert sorted(p.name for p in tmp_path.rglob("*")) == before


def test_render_with_both_the_out_directory_and_the_sets_json_is_a_usage_error(capsys, declaration, tmp_path):
    with pytest.raises(SystemExit) as stop:
        agent_definitions.main(["render", str(declaration), "-o", str(tmp_path / "out"), "--sets-json"])

    assert stop.value.code == 2
    assert "--sets-json" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_render_with_neither_the_out_directory_nor_the_sets_json_is_a_usage_error(capsys, declaration):
    with pytest.raises(SystemExit) as stop:
        agent_definitions.main(["render", str(declaration)])

    assert stop.value.code == 2
    assert "--out" in capsys.readouterr().err
