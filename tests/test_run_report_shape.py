"""The `## Report` section of each brief gives the exact JSON shape of the report: each field, its type, and one example.

Agents wrote `"ticket": 13` and `"judgement_calls": [...]`, because the brief
named the fields but not their types. The engine then rejected reports of
correct work. Each case runs `delegate run` end to end with a scripted adapter
that reads the brief it gets, as an agent does.
"""

import json
import re

import pytest

from delegate import adapters
from delegate.cli import delegate
from delegate.run.reports import SPECIALIST_OPTIONAL, SPECIALIST_REQUIRED, VERIFIER_REQUIRED

from .support import ScriptedAdapter, make_repo, read_journal, valid_verdict

EXAMPLE = re.compile(r"^```json\n(.*?)^```", re.DOTALL | re.MULTILINE)


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def report_section(prompt):
    """The text of the last `## Report` section of a brief, up to the next section."""
    return prompt.rsplit("## Report\n\n", 1)[1].split("\n## ", 1)[0]


def example_of(prompt):
    """The example object of the `## Report` section, as the text of the brief gives it."""
    match = EXAMPLE.search(report_section(prompt))
    assert match is not None, report_section(prompt)
    return match.group(1)


def test_the_specialist_brief_names_the_json_type_of_each_report_field(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    run(repo, capsys)

    (call,) = scripted.calls
    lines = report_section(call.prompt).splitlines()
    for name, kind in {**SPECIALIST_REQUIRED, **SPECIALIST_OPTIONAL}.items():
        assert any(line.startswith(f"- `{name}`: {kind}") for line in lines), (name, kind)
    assert "- `ticket`: string" in lines
    assert "- `judgement_calls`: string" in lines


def test_the_verifier_brief_names_the_json_type_of_each_report_field(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    run(repo, capsys)

    (call,) = scripted.verifier_calls
    lines = report_section(call.prompt).splitlines()
    for name, kind in VERIFIER_REQUIRED.items():
        assert any(line.startswith(f"- `{name}`: {kind}") for line in lines), (name, kind)
    assert "- `gate_output`: string" in lines
    assert "- `findings`: list of strings" in lines


def test_a_specialist_and_a_verifier_that_copy_the_example_of_their_brief_build_the_ticket(tmp_path, capsys, scripted):
    scripted.report_text = lambda request, head: example_of(request.prompt)
    scripted.verifier_report_text = lambda request: example_of(request.prompt)
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    (validation,) = [e for e in events if e["event"] == "report-validation"]
    assert (validation["valid"], validation["reason"]) == (True, None)
    (verdict,) = [e for e in events if e["event"] == "verdict"]
    assert (verdict["mode"], verdict["verdict"]) == ("full", "ACCEPT")
    assert next(e for e in events if e["event"] == "run-end")["result"] == "built"


def test_a_fixup_specialist_and_a_fixup_verifier_that_copy_the_example_of_their_brief_build_the_ticket(
    tmp_path, capsys, scripted
):
    rejection = json.dumps(valid_verdict("REJECT", ["feature.txt:1 The file is not a CSV file."]))
    scripted.fixup_report_text = lambda request, head: example_of(request.prompt)
    scripted.verifier_report_text = lambda request: (
        rejection if len(scripted.verifier_calls) == 1 else example_of(request.prompt)
    )
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    (fixup,) = [e for e in events if e["event"] == "fixup-report"]
    assert (fixup["valid"], fixup["reason"]) == (True, None)
    verdicts = [(e["mode"], e["verdict"]) for e in events if e["event"] == "verdict"]
    assert verdicts == [("full", "REJECT"), ("fix-up", "ACCEPT")]
    assert next(e for e in events if e["event"] == "run-end")["result"] == "built"
