"""The text of the lines of the feed of `delegate watch` is a pure module (#363).

`delegate.run.feed_text` makes the parts of a line from an event and the few facts that the view keeps (the time since the
agent started, its tokens, the id of the run). It has no state and no terminal, so each case gives an event and reads the
parts. The parts are `(role, text)` pairs, as `live.LiveView` paints them. The expected text of each case is a literal
of the spec in `design/docs/components.md` § Feed. The tests of `watch` check that the view draws the same frames.
"""

from __future__ import annotations

from importlib import import_module

import pytest

from delegate.run import output

feed_text = import_module("delegate.run.feed_text")

RUN_ID = "20261114T090000Z-ab12"
FAILED = output.glyphs().failed


def text_of(spans):
    return "".join(text for _, text in spans)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (45, "45s"), (59.9, "59s"), (60, "1m"), (2160, "36m"), (3599, "59m"), (3600, "1h00m"), (38520, "10h42m"), (-5, "0s")],
)
def test_a_duration_has_the_short_form_of_the_feed(seconds, expected):
    assert feed_text.duration(seconds) == expected


def test_the_result_of_an_agent_names_the_role_the_state_the_time_and_the_tokens():
    spans = feed_text.agent_result("specialist", "ended", 125, "12k new · 14k cache")

    assert spans == [("", "specialist ended"), ("", " · "), ("", "2m"), ("", " · "), ("dim", "12k new · 14k cache")]


def test_the_result_of_an_agent_with_no_start_and_no_tokens_is_the_role_and_the_state_only():
    assert feed_text.agent_result("verifier", "timed out", None, None) == [("", "verifier timed out")]


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"valid": True}, "report ok"),
        ({"valid": True, "status": "committed"}, "report ok"),
        ({"valid": True, "status": "blocked"}, "report blocked"),
        ({"valid": False, "status": "blocked"}, "report not valid"),
    ],
)
def test_the_line_of_a_report_says_ok_the_status_or_not_valid(event, expected):
    assert feed_text.report(event) == expected


def test_a_gate_that_passed_has_its_name_and_its_time():
    event = {"command": "python3 -m pytest -q", "green": True}

    assert feed_text.gate_item(event, "gate-result", 90) == ("pytest 1m", False)


def test_a_gate_that_failed_says_red_and_a_gate_that_ran_out_of_time_says_so():
    event = {"command": "python3 -m pytest -q", "green": False}

    assert feed_text.gate_item(event, "gate-result", 3) == ("pytest 3s red", True)
    assert feed_text.gate_item({**event, "timed_out": True}, "gate-result", 3) == ("pytest 3s timed out", True)


def test_a_gate_with_no_start_has_no_time_and_a_skipped_gate_says_skipped():
    event = {"command": "python3 -m pytest -q", "green": True}

    assert feed_text.gate_item(event, "gate-result", None) == ("pytest", False)
    assert feed_text.gate_item(event, "gate-skip", None) == ("pytest skipped", False)


def test_the_gates_of_a_round_say_green_and_list_each_gate_in_dim():
    spans = feed_text.gates([("pytest 1m", False), ("ruff 2s", False)])

    assert spans == [("ok", "gates green"), ("dim", " ("), ("dim", "pytest 1m"), ("dim", ", "), ("dim", "ruff 2s"), ("dim", ")")]


def test_the_gates_of_a_round_say_red_and_paint_the_red_gate_bad():
    spans = feed_text.gates([("pytest 1m red", True), ("ruff 2s", False)])

    assert spans[0] == ("bad", "gates red")
    assert ("bad", "pytest 1m red") in spans
    assert ("dim", "ruff 2s") in spans
    assert text_of(spans) == "gates red (pytest 1m red, ruff 2s)"


def test_an_accepted_verdict_is_a_mark_and_bold_accept():
    spans = feed_text.verdict({"verdict": "ACCEPT"})

    assert text_of(spans).endswith("ACCEPT")
    assert spans[-1] == ("bold", "ACCEPT")
    assert spans[0][0] == "ok"


@pytest.mark.parametrize(
    ("findings", "expected"),
    [([], "REJECT · 0 findings"), (["a"], "REJECT · 1 finding"), (["a", "b"], "REJECT · 2 findings")],
)
def test_a_rejected_verdict_counts_its_findings_in_warn_bold(findings, expected):
    assert feed_text.verdict({"verdict": "REJECT", "findings": findings}) == [("warn bold", expected)]


def test_the_verdict_of_a_chain_names_its_stack_and_the_verdict_of_a_ticket_names_none():
    assert feed_text.verdict_subject({"verdict": "ACCEPT", "stack": "python"}) == "stack python"
    assert feed_text.verdict_subject({"verdict": "ACCEPT"}) is None


def test_the_end_of_a_built_step_is_its_state_and_a_stop_adds_its_reason():
    assert feed_text.step_end({"state": "built"}) == "built"
    assert feed_text.step_end({"state": "built", "reason": "ignored"}) == "built"
    assert feed_text.step_end({"state": "failed", "reason": "gates red"}) == "failed · gates red"
    assert feed_text.step_end({"state": "failed"}) == "failed"


def test_the_end_of_a_run_is_the_result_the_time_the_reason_and_the_tokens():
    spans = feed_text.run_end({"result": "failed", "reason": "gates red"}, 3700, "1k new · 2k cache")

    assert spans == [
        ("bad", "run failed"), ("", " · "), ("", "1h01m"), ("", " · "), ("", "reason: gates red"), ("", " · "), ("dim", "1k new · 2k cache"),
    ]


def test_the_end_of_a_built_run_is_ok_bold_and_has_no_reason_and_no_tokens_when_it_has_none():
    assert feed_text.run_end({"result": "built"}, 30, None) == [("ok bold", "run built"), ("", " · "), ("", "30s")]


def test_a_pause_before_a_gate_names_the_gate_and_the_command_that_goes_on():
    event = {"event": "paused", "ticket": "7", "before": "gate", "command": "python3 -m pytest -q"}

    role, spans = feed_text.own_line("paused", event, "ab12")

    assert role == "warn"
    assert text_of(spans) == "paused · before the gate pytest of ticket 7 · go on with delegate run --resume ab12"


@pytest.mark.parametrize(
    ("event", "where"),
    [
        ({"ticket": "7", "before": "verifier", "stack": "python"}, "the verifier of stack python"),
        ({"ticket": "7", "before": "verifier"}, "the verifier of ticket 7"),
        ({"ticket": "7", "before": "agent"}, "ticket 7"),
        ({"ticket": "7", "before": "gate"}, "ticket 7"),
    ],
)
def test_a_pause_names_where_the_run_stopped(event, where):
    _, spans = feed_text.own_line("paused", event, "ab12")

    assert text_of(spans) == f"paused · before {where} · go on with delegate run --resume ab12"


def test_a_halt_names_the_agent_the_message_and_the_command_in_bad():
    event = {"role": "specialist", "ticket": "7", "message": "the harness failed"}

    role, spans = feed_text.own_line("halted", event, "ab12")

    assert role == "bad"
    assert text_of(spans) == "halted · the specialist of ticket 7 · the harness failed · go on with delegate run --resume ab12"


def test_a_crash_names_where_the_error_the_file_of_the_traceback_and_the_command():
    event = {
        "ticket": "7", "phase": "verifier", "error": "OSError", "message": "no room at /var/tmp/run/file.txt",
        "traceback": "/state/runs/x/crash-7.txt", "retry": True,
    }

    role, spans = feed_text.own_line("crashed", event, "ab12")

    assert role == "bad"
    assert text_of(spans) == (
        "crashed · the verifier of ticket 7 · OSError: no room at file.txt · traceback crash-7.txt"
        " · go on with delegate run --resume ab12 --retry 7"
    )


def test_a_crash_with_no_traceback_and_no_retry_has_neither_part():
    event = {"ticket": "7", "phase": "agent", "error": "OSError", "message": "boom"}

    _, spans = feed_text.own_line("crashed", event, "ab12")

    assert text_of(spans) == "crashed · the agent of ticket 7 · OSError: boom · go on with delegate run --resume ab12"


def test_the_start_of_a_run_names_its_id_its_mode_and_the_count_of_its_tickets():
    one = {"run_id": RUN_ID, "mode": "chain", "tickets": ["a"]}
    two = {**one, "tickets": ["a", "b"]}

    assert feed_text.own_line("run-start", one, "ab12") == ("", [("", f"run {RUN_ID} started · chain · 1 ticket")])
    assert feed_text.own_line("run-start", two, "ab12")[1] == [("", f"run {RUN_ID} started · chain · 2 tickets")]


@pytest.mark.parametrize(
    ("kind", "event", "expected"),
    [
        ("resume", {}, "run resumed"),
        ("step-start", {"agent": "claude"}, "started · claude"),
        ("retry", {"phase": "verifier"}, "retry at the verifier"),
        ("stack-change", {"stack": "python", "changes": ["gates", "agent_timeout"]}, "stack python changed: gates, agent_timeout"),
        ("continuation-limit", {"limit": 3}, "continuation limit 3 reached"),
        ("hotspot-finding", {"matches": ["a"]}, "hotspot finding · 1 path"),
        ("hotspot-finding", {"matches": ["a", "b"]}, "hotspot finding · 2 paths"),
        ("rebase", {"result": "clean"}, "rebase clean"),
        ("verify-start", {"round": 0}, "verifier started"),
        ("verify-start", {"round": 2}, "scoped verifier started · round 2"),
        ("verify-start", {"round": 0, "tickets": ["a", "b"]}, "verifier started (#a, #b)"),
        ("invariant-violation", {"kind": "journal-changed"}, "invariant violation: another writer changed the journal"),
        ("invariant-violation", {"kind": "worktree"}, "invariant violation: the verifier changed the worktree"),
        ("fixup-refused", {"round": 1}, "fix-up round 1 refused"),
        ("skip", {"reason": "built already"}, "skipped · built already"),
        ("skip", {}, "skipped"),
        ("unskip", {"outlook": "the run goes on"}, "not skipped · the run goes on"),
        ("an-event-of-a-later-version", {}, "an-event-of-a-later-version"),
    ],
)
def test_an_event_with_a_line_of_its_own_has_its_text(kind, event, expected):
    role, spans = feed_text.own_line(kind, event, "ab12")

    assert (role, text_of(spans)) == ("", expected)


def test_a_fix_up_and_a_continuation_are_warn_lines():
    assert feed_text.own_line("fixup-start", {"round": 2}, "ab12") == ("", [("warn", "fix-up round 2 started")])
    assert feed_text.own_line("continuation", {"count": 1, "limit": 3, "trigger": "no report"}, "ab12") == (
        "", [("warn", "continuation 1 of 3"), ("", " · "), ("", "no report")],
    )


def test_a_stall_a_time_limit_and_a_warning_have_their_text():
    assert feed_text.stall(5) == [("", f"{FAILED} stall · no change for 5m")]
    assert feed_text.time_limit(90) == [("", "time limit · 90m used · the run goes on")]
    assert feed_text.warning("#7", "idle for 9m") == [("", f"{FAILED} #7 idle for 9m")]
    assert feed_text.warning("", "idle for 9m") == [("", f"{FAILED} idle for 9m")]
