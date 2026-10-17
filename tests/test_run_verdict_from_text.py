"""The engine reads a missing verdict from the final text of the verifier (#98).

On a host whose permission mode refuses a shell redirect to a file (Claude Code
in `dontAsk` mode, also with a `Bash(printf *)` allow rule), a verifier that has
no Write tool cannot write its verdict file. It can still end its final message
with the verdict object, as the brief asks. The engine then takes the last such
object, writes it to the report path, and checks it as it checks a file.
"""

from __future__ import annotations

import json
from pathlib import Path

from delegate import adapters
from delegate.cli import delegate
from delegate.run.reports import verifier_example

from .support import ScriptedAdapter, make_repo, read_journal


def run_with(tmp_path, capsys, adapter):
    adapters.register("scripted", adapter)
    try:
        repo = make_repo(tmp_path)
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
        captured = capsys.readouterr()
    finally:
        adapters.unregister("scripted")
    return code, read_journal(captured.out), captured.err


def final_text_with(verdict: dict[str, object]) -> str:
    return "I checked each criterion.\n\nVerdict: ACCEPT\n\n```json\n" + json.dumps(verdict, indent=2) + "\n```\n"


def test_a_verifier_that_wrote_no_file_but_ends_with_the_verdict_object_gives_the_verdict(tmp_path, capsys, git_identity):
    adapter = ScriptedAdapter(write_verifier_report=False, verifier_final_text=final_text_with(verifier_example("full")))

    code, events, err = run_with(tmp_path, capsys, adapter)

    assert code == 0, err
    (report,) = [event for event in events if event["event"] == "verify-report"]
    assert report["valid"] is True
    assert report["source"] == "final-text"
    (verdict,) = [event for event in events if event["event"] == "verdict"]
    assert verdict["verdict"] == "ACCEPT"
    assert json.loads(open(str(report["path"])).read())["verdict"] == "ACCEPT"


def test_a_verifier_that_wrote_its_file_has_no_source_field(tmp_path, capsys, git_identity):
    code, events, err = run_with(tmp_path, capsys, ScriptedAdapter(verifier_final_text=final_text_with(verifier_example("full"))))

    assert code == 0, err
    (report,) = [event for event in events if event["event"] == "verify-report"]
    assert "source" not in report


def test_an_invalid_verdict_object_in_the_final_text_fails_the_step(tmp_path, capsys, git_identity):
    wrong_mode = {**verifier_example("full"), "mode": "fix-up"}
    adapter = ScriptedAdapter(write_verifier_report=False, verifier_final_text=final_text_with(wrong_mode))

    code, events, _ = run_with(tmp_path, capsys, adapter)

    assert code == 1
    (report,) = [event for event in events if event["event"] == "verify-report"]
    assert report["valid"] is False
    assert "mode" in str(report["reason"])


def test_a_final_text_with_no_verdict_object_fails_the_step_as_before(tmp_path, capsys, git_identity):
    adapter = ScriptedAdapter(write_verifier_report=False, verifier_final_text="Verdict: ACCEPT")

    code, events, _ = run_with(tmp_path, capsys, adapter)

    assert code == 1
    assert not any(event["event"] == "verdict" for event in events)


def test_the_last_verdict_object_of_the_final_text_is_the_verdict(tmp_path, capsys, git_identity):
    # A verifier can quote an earlier draft; the object at the end of its message is its verdict.
    draft = {**verifier_example("full"), "verdict": "REJECT", "findings": ["src/a.py:1 draft"]}
    text = "A first draft:\n" + json.dumps(draft) + "\n\nThe final verdict:\n" + json.dumps(verifier_example("full"))
    adapter = ScriptedAdapter(write_verifier_report=False, verifier_final_text=text)

    code, events, err = run_with(tmp_path, capsys, adapter)

    assert code == 0, err
    (verdict,) = [event for event in events if event["event"] == "verdict"]
    assert verdict["verdict"] == "ACCEPT"


def test_a_verdict_object_with_a_nested_verdict_key_is_read_whole(tmp_path, capsys, git_identity):
    # An object inside the verdict object that also has a `verdict` key is not the verdict.
    verdict = verifier_example("full")
    verdict["criteria"] = [{"criterion": "c", "evidence": "e", "verdict": "ACCEPT"}]
    verdict = {**verdict, "verdict": "REJECT", "findings": ["src/a.py:1 a finding"]}
    adapter = ScriptedAdapter(write_verifier_report=False, verifier_final_text="Verdict:\n" + json.dumps(verdict))

    _, events, _ = run_with(tmp_path, capsys, adapter)

    # The first round reads the outer object: a valid REJECT (a fix-up round then starts).
    report = next(event for event in events if event["event"] == "verify-report")
    assert report["valid"] is True
    found = next(event for event in events if event["event"] == "verdict")
    assert found["verdict"] == "REJECT"


def test_a_verifier_that_ended_failed_gets_no_recovered_file(tmp_path, capsys, git_identity):
    adapter = ScriptedAdapter(
        write_verifier_report=False, verifier_end_state="failed", verifier_exit_status=1,
        verifier_final_text=final_text_with(verifier_example("full")),
    )

    code, events, _ = run_with(tmp_path, capsys, adapter)

    assert code == 1
    (result,) = [event for event in events if event["event"] == "verify-result"]
    assert not list(Path(str(result["event_stream"])).parent.glob("*.json"))
