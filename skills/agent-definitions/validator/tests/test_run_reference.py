"""The run reference documents the journal, the specialist and verdict reports and the adapter interface.

The reference is outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

from pathlib import Path

import pytest

from agent_definitions import adapters, delegate
from agent_definitions.reports import (
    SPECIALIST_OPTIONAL, SPECIALIST_REQUIRED, SPECIALIST_STATUSES, VERIFIER_REQUIRED, VERIFIER_VERDICTS,
)

from .support import ScriptedAdapter, make_repo, read_journal

ROOT = Path(__file__).resolve().parents[4]
REFERENCE = ROOT / "docs" / "reference" / "run.md"

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


@outside_the_package
@pytest.mark.parametrize(
    "name",
    [
        *SPECIALIST_REQUIRED, *SPECIALIST_OPTIONAL, *SPECIALIST_STATUSES,
        *VERIFIER_REQUIRED, *VERIFIER_VERDICTS, "criterion", "evidence",  # the verdict report
        "agent", "model", "prompt", "cwd", "report_path",  # the request of an adapter
        "exit_status", "end_state", "session_id", "event_stream",  # its result
        "finished", "failed", "capped", "tier_column",
        "--dry-run", "--repo <dir>", "--tiers <file>",
    ],
)
def test_the_reference_names_each_report_field_adapter_field_and_option(name):
    assert f"`{name}`" in REFERENCE.read_text()


@outside_the_package
@pytest.mark.parametrize("verdicts", [pytest.param([], id="first-pass-accept"), pytest.param(["REJECT", "ACCEPT"], id="fixup-round")])
def test_the_reference_lists_every_event_of_a_real_journal_with_each_of_its_fields(tmp_path, capsys, verdicts):
    adapters.register("scripted", ScriptedAdapter(verdict_sequence=verdicts, verdict_findings=["feature.txt is not a CSV file."] if verdicts else None))
    try:
        repo = make_repo(tmp_path)
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    finally:
        adapters.unregister("scripted")
    events = read_journal(capsys.readouterr().out)
    assert code == 0
    assert ("fixup-start" in [e["event"] for e in events]) == bool(verdicts)
    rows = {line.split("|")[1].strip(): line for line in REFERENCE.read_text().splitlines() if line.startswith("| `")}
    for event in events:
        row = rows[f"`{event['event']}`"]
        for field in event:
            if field not in ("seq", "time", "event"):
                assert f"`{field}`" in row, (event["event"], field)
