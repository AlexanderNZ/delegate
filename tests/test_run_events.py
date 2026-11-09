"""The journal schema: one module names each event and its fields, and each journal says which version it uses (#189).

`delegate.run.events` holds a constant for each event name, a `TypedDict` for the fields of each event, and the
schema version. `docs/reference/run.md` holds the same names in its journal table, and the cases below compare the two
in both directions. A journal that was written before the version has no `schema` in `run-start`: it is version 0, and
`status`, `watch` and `--resume` still read it.

The reference is outside the package source, as the README is, so a Nix build that copies only the package skips the
cases that read it.
"""

import json
import re
from pathlib import Path

import pytest

from delegate.cli import delegate
from delegate.run import events as schema

from .pages import repository_root
from .support import finished_run, journal_path, make_repo, read_journal, read_journal_file, resume_main, run_main
from .test_run_multi import HEAD, ticket

ROOT = repository_root(Path(__file__))
REFERENCE = ROOT / "docs" / "reference" / "run.md"

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)

# The fields that every line of a journal has. The reference states them before the table, not in each row.
LINE_FIELDS = {"seq", "time", "event"}
# The words in backticks that the prose of a row names and that are not a field of the event: the fields of the objects
# in a list, a value, a mode, and a command. The table has no markup to tell them from a field, so each row names its own.
PROSE: dict[str, set[str]] = {
    "adapter-result": {"tool", "mode"},
    "prepare-start": {"prepare"},
    "step-end": {"economy", "failed"},
    "cleanup": {"built"},
    "run-end": {"abandoned"},
    "stack-change": {"resume"},
}


def module_events() -> dict[str, set[str]]:
    """The events of the module: the event name with the names of its fields, except the fields of every line."""
    return {name: set(typed.__annotations__) - LINE_FIELDS for name, typed in schema.EVENTS.items()}


def without_values(text: str) -> str:
    """The text without each parenthesised part that follows a word in backticks, such as `role` (`specialist`, or `verifier`).

    That part holds the values or the meaning of a field, not other fields. A parenthesis can hold another one.
    """
    kept, depth, start = [], 0, 0
    for index, char in enumerate(text):
        if depth == 0 and char == "(" and text[:index].endswith("` "):
            depth, start = 1, index
        elif depth and char == "(":
            depth += 1
        elif depth and char == ")":
            depth -= 1
        elif depth == 0:
            kept.append(char)
    return "".join(kept)


def documented_events(text: str) -> dict[str, set[str]]:
    """The events of the journal table of the reference: the event name with the field names of its row.

    A field is a word in backticks that is not in the parentheses after another field, and that `PROSE` does not name.
    """
    events: dict[str, set[str]] = {}
    lines = text.splitlines()
    for line in lines[lines.index("| `event` | Fields |") + 2 :]:
        if not line.startswith("| `"):
            break
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", line)]
        name = cells[1].strip("`")
        words = re.findall(r"`([a-z_]+)`", without_values(cells[2]))
        events[name] = set(words) - PROSE.get(name, set())
    return events


def differences(documented: dict[str, set[str]], in_module: dict[str, set[str]]) -> list[str]:
    """The names that only one side has, in both directions."""
    found = [f"event {name} is not in the reference" for name in sorted(in_module.keys() - documented.keys())]
    found += [f"event {name} is not in the module" for name in sorted(documented.keys() - in_module.keys())]
    for name in sorted(in_module.keys() & documented.keys()):
        found += [f"{name}: field {field} is not in the reference" for field in sorted(in_module[name] - documented[name])]
        found += [f"{name}: field {field} is not in the module" for field in sorted(documented[name] - in_module[name])]
    return found


def test_the_journal_of_a_new_run_names_the_schema_version_in_run_start(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    run_id = finished_run(repo, capsys)

    run_start = read_journal_file(repo, run_id)[0]
    assert run_start["event"] == schema.RUN_START
    assert run_start["schema"] == schema.SCHEMA_VERSION
    assert isinstance(schema.SCHEMA_VERSION, int) and schema.SCHEMA_VERSION >= 1
    assert schema.schema_version(read_journal_file(repo, run_id)) == schema.SCHEMA_VERSION


def test_the_schema_version_of_a_journal_with_no_schema_is_0():
    events = [{"seq": 1, "time": "2026-03-02T09:00:00+00:00", "event": "run-start", "run_id": "r1"}]

    assert schema.schema_version(events) == 0


@outside_the_package
def test_the_events_of_the_module_are_the_events_of_the_reference_table_with_the_same_fields():
    assert differences(documented_events(REFERENCE.read_text()), module_events()) == []


@outside_the_package
def test_an_event_or_a_field_that_only_the_module_has_is_a_difference():
    in_module = module_events()
    in_module["new-event"] = {"ticket"}
    in_module["step-end"] = in_module["step-end"] | {"new_field"}

    assert differences(documented_events(REFERENCE.read_text()), in_module) == [
        "event new-event is not in the reference",
        "step-end: field new_field is not in the reference",
    ]


@outside_the_package
def test_an_event_or_a_field_that_only_the_reference_has_is_a_difference():
    in_module = module_events()
    del in_module["cleanup"]
    in_module["step-end"] = in_module["step-end"] - {"reason"}

    assert differences(documented_events(REFERENCE.read_text()), in_module) == [
        "event cleanup is not in the module",
        "step-end: field reason is not in the module",
    ]


def test_each_event_name_is_a_constant_of_the_module():
    constants = {value for key, value in vars(schema).items() if key.isupper() and isinstance(value, str) and key not in {"SCHEMA_KEY"}}

    assert set(schema.EVENTS) <= constants
    assert schema.RUN_START == "run-start" and schema.STEP_END == "step-end" and schema.RUN_END == "run-end"


def test_every_event_of_a_real_run_is_in_the_module_with_only_fields_of_the_module(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert (code, err) == (0, "")

    for event in read_journal(out):
        assert event["event"] in schema.EVENTS, event["event"]
        assert set(event) - LINE_FIELDS <= set(schema.EVENTS[event["event"]].__annotations__), event["event"]


def without_schema(repo, run_id) -> None:
    """Write the journal of the run again as a journal of an earlier version of the kit wrote it: no `schema` in `run-start`."""
    path = journal_path(repo, run_id)
    lines = path.read_text().splitlines(keepends=True)
    first = json.loads(lines[0])
    assert first.pop("schema") == schema.SCHEMA_VERSION
    lines[0] = json.dumps(first) + "\n"
    path.write_text("".join(lines))


def test_status_and_watch_read_a_journal_with_no_schema(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished_run(repo, capsys)
    without_schema(repo, run_id)
    assert schema.schema_version(read_journal_file(repo, run_id)) == 0

    code = delegate.main(["status", run_id, "--repo", str(repo)])
    status = capsys.readouterr()
    assert (code, status.err) == (0, "")
    assert "a" in status.out and "built" in status.out

    code = delegate.main(["watch", run_id, "--repo", str(repo), "--poll-seconds", "0.02"])
    watched = capsys.readouterr()
    assert (code, watched.err) == (0, "")
    assert "run-end" in watched.out


def test_a_resume_reads_a_journal_with_no_schema_and_writes_no_schema_to_it(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished_run(repo, capsys)
    path = journal_path(repo, run_id)
    kept = []
    for line in path.read_text().splitlines(keepends=True):
        kept.append(line)
        if json.loads(line)["event"] == "run-branch-advance":
            break
    path.write_text("".join(kept))
    without_schema(repo, run_id)
    scripted.calls.clear(), scripted.verifier_calls.clear()

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    assert "resume" in [e["event"] for e in events]
    assert events[-1]["result"] == "built"
    assert schema.schema_version(events) == 0
    assert sum("schema" in e for e in events) == 0
