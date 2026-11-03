"""`agent-definitions schema`: the declaration schema as JSON (#172).

A consumer that declares agents in its own language listed the keys of a
declaration by hand, and each new key (`contextLoad`, `gateExtraFlags`) broke
its schema check. The command prints the keys as data. Each case drives
`main(argv)` and reads stdout, or compares the output with what the parser
does.
"""

import dataclasses
import json
import re

import pytest

from delegate.cli import agent_definitions, delegate
from delegate.definitions.declaration import KNOWN_KEYS, Agent, parse_declaration


def schema(capsys, *argv: str) -> dict:
    code = agent_definitions.main(["schema", *argv])
    assert code == 0
    return json.loads(capsys.readouterr().out)


def camel(snake: str) -> str:
    head, *tail = snake.split("_")
    return head + "".join(word.capitalize() for word in tail)


def test_the_schema_lists_gate_extra_flags_and_context_load_with_their_types(capsys):
    keys = schema(capsys)["keys"]

    assert keys["gateExtraFlags"]["type"] == "array"
    assert keys["gateExtraFlags"]["items"] == "string"
    assert keys["gateExtraFlags"]["since"] == "1.10.0"
    assert keys["contextLoad"]["type"] == "string"
    assert keys["contextLoad"]["values"] == ["blocks", "read"]
    assert keys["contextLoad"]["default"] == "blocks"
    assert keys["contextLoad"]["since"] == "1.8.0"


def test_the_schema_has_a_version_field_of_1(capsys):
    assert schema(capsys)["version"] == 1


def test_only_the_description_is_required(capsys):
    keys = schema(capsys)["keys"]

    assert [name for name, key in keys.items() if key["required"]] == ["description"]
    assert keys["description"]["default"] is None


def test_every_key_has_a_type_a_requirement_a_default_and_a_version_of_the_kit(capsys):
    keys = schema(capsys)["keys"]

    for name, key in keys.items():
        assert key["type"] in ("string", "integer", "boolean", "array"), name
        assert isinstance(key["required"], bool), name
        assert "default" in key, name
        assert re.fullmatch(r"\d+\.\d+\.\d+", key["since"]), name


def test_a_key_of_the_declaration_parser_that_the_schema_lacks_fails_this_test(capsys):
    keys = schema(capsys)["keys"]

    assert set(KNOWN_KEYS) - set(keys) == set()
    assert set(keys) - set(KNOWN_KEYS) == set()


def test_each_field_of_an_agent_is_a_key_of_the_schema(capsys):
    keys = schema(capsys)["keys"]

    fields = {camel(f.name) for f in dataclasses.fields(Agent)} - {"key"}

    assert fields == set(keys)


def test_the_default_of_each_key_is_what_the_parser_gives_when_the_key_is_absent(capsys):
    keys = schema(capsys)["keys"]
    agent = parse_declaration({"agents": {"app": {"description": "An app."}}})[0]

    for name, key in keys.items():
        if not key["required"]:
            assert getattr(agent, _attribute(name)) == key["default"], name


def _attribute(name: str) -> str:
    return re.sub(r"([A-Z])", lambda m: "_" + m.group(1).lower(), name)


@pytest.mark.parametrize(
    ("name", "value"),
    [("contextLoad", "read"), ("outputLanguage", "ste"), ("gateExtraFlags", ["--info"]), ("trackedFileBuild", True), ("maxTurns", 5)],
)
def test_the_parser_accepts_a_key_of_the_schema(capsys, name, value):
    assert name in schema(capsys)["keys"]

    agent = parse_declaration({"agents": {"app": {"description": "An app.", name: value}}})[0]

    assert getattr(agent, _attribute(name)) == value


def test_the_umbrella_command_prints_the_same_schema(capsys):
    expected = schema(capsys)

    assert delegate.main(["schema"]) == 0

    assert json.loads(capsys.readouterr().out) == expected
