"""The engine reads a missing verdict from the final text of the verifier (#98).

On a host whose permission mode refuses a shell redirect to a file (Claude Code
in `dontAsk` mode, also with a `Bash(printf *)` allow rule), a verifier that has
no Write tool cannot write its verdict file. It can still end its final message
with the verdict object, as the brief asks. The engine then takes the last such
object, writes it to the report path, and checks it as it checks a file.
"""

from __future__ import annotations

import json

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
