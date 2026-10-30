"""A report that is valid JSON except for raw control characters in its strings is a valid report (#197).

A verifier wrote its verdict with a raw tab and a raw newline in a string. A strict
parse refused it, and the run failed for one character. Each case runs `delegate run`
end to end with a scripted adapter that writes the text of the report.
"""

import json

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, make_repo, read_journal, valid_report, valid_verdict


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


def raw_controls(value: dict[str, object]) -> str:
    """The JSON text of `value`, with each escaped tab and newline of a string written as the raw character.

    The objects of the tests have no other tab and no other newline, so the text has no raw control character
    outside a string.
    """
    return json.dumps(value).replace("\\t", "\t").replace("\\n", "\n")


def test_a_verdict_with_a_raw_tab_and_a_raw_newline_in_a_finding_is_valid_and_the_event_says_so(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    scripted.verdict = "ACCEPT"

    def verdict_text(request):
        return raw_controls(valid_verdict("ACCEPT", ["feature.txt:1 a\tb\nc"]))

    scripted.verifier_report_text = verdict_text

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (report,) = [e for e in read_journal(out) if e["event"] == "verify-report"]
    assert report["valid"] is True
    assert report["lenient_parse"] is True
    (verdict,) = [e for e in read_journal(out) if e["event"] == "verdict"]
    assert verdict["verdict"] == "ACCEPT"
    assert verdict["findings"] == ["feature.txt:1 a\tb\nc"]


def test_a_specialist_report_with_a_raw_control_character_in_the_summary_is_valid(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    def report_text(request, head):
        return raw_controls(valid_report("a", "delegate/a", head, summary="line one\nline\ttwo"))

    scripted.report_text = report_text

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (validation,) = [e for e in read_journal(out) if e["event"] == "report-validation"]
    assert validation["valid"] is True
    assert validation["lenient_parse"] is True


def test_a_report_that_needs_no_lenient_parse_has_no_lenient_parse_field(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    events = read_journal(out)
    reports = [e for e in events if e["event"] in ("report-validation", "verify-report")]
    assert reports
    assert all("lenient_parse" not in e for e in reports)


def test_a_verdict_with_a_missing_closing_brace_is_still_invalid_with_the_message_of_before(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)
    scripted.verifier_report_text = json.dumps(valid_verdict("ACCEPT"))[:-1]

    code, out, _ = run(repo, capsys)

    assert code == 1
    (report,) = [e for e in read_journal(out) if e["event"] == "verify-report"]
    assert report["valid"] is False
    assert "is not valid JSON: Expecting ',' delimiter" in str(report["reason"])
    assert "lenient_parse" not in report
