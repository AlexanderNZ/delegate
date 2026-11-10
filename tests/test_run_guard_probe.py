"""The guard probe: a run starts no agent session before the guard of each verifier of the run shows a denial (#219).

The harness hooks did not run in the verifier sessions of this repository for two weeks, and nothing showed it: a guard
that does not run writes no event. So the engine starts one short session for each verifier of the run, from its sealed
agent, on the tier `cheap`, in a new repository outside the repository under test. The session runs the command
`touch delegate-guard-probe`, which the guard must deny. The engine decides from facts: `denied` when the file does not
exist and the session shows a denial, `ran` when the file exists, and `inconclusive` for any other result.

Each case calls `delegate.main` against a real temporary git repository. The scripted adapter, or a stand-in for the
`claude` command that replays a stream, takes the place of the harness. A case with the stand-in uses the Claude Code
adapter, so it covers what the adapter gives the harness.
"""

import json
import os
import shutil
from pathlib import Path

import pytest

from delegate import adapters

from . import claude_fake
from .support import (
    WORKFLOW, ScriptedAdapter, event_names, git, make_repo, read_journal, read_journal_file, resume_main, run_main,
    write_project_agents,
)

pytestmark = pytest.mark.usefixtures("git_identity", "home")

WEB_STACK = """[stacks.web]
specialist = "web-specialist"
verifier = "web-verifier"
gates = ["test -f feature.txt"]
hotspots = []

"""

WEB_TICKET = """
[[tickets]]
id = "b"
text = "The export page shows the CSV file."
stack = "web"
blocked-by = []
"""

LIMITS_PAGE = "the-enforcement-model-and-its-limits"


def write_agents(repo: Path, *names: str) -> None:
    """Write a plain agent file for each name into the project directory that the scripted adapter searches."""
    directory = repo / ".claude" / "agents"
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / f"{name}.md").write_text(f"---\nname: {name}\n---\nThe rules of {name}.\n")
    (repo / ".git" / "info" / "exclude").write_text(".claude/\n")


def repo_with_agents(tmp_path: Path, *, web: bool = False) -> Path:
    workflow = WORKFLOW.replace("[[tickets]]", WEB_STACK + "[[tickets]]", 1) + WEB_TICKET if web else WORKFLOW
    repo = make_repo(tmp_path, workflow)
    write_agents(repo, "python-specialist", "python-verifier", *(("web-specialist", "web-verifier") if web else ()))
    return repo


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(probes_guard=True)
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def start(repo: Path, capsys) -> tuple[int, str, str, list[dict[str, object]]]:
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return code, out, err, read_journal(out)


def ask_for_a_pause(request) -> None:
    """Write the pause request of `delegate pause` while the specialist works, so that the run pauses before its next gate."""
    run_directory = request.report_path.parent.parent
    (run_directory / "pause-request").write_text('{"time": "2026-11-01T00:00:00+00:00", "pid": 1}\n')


def paused_run(repo: Path, scripted: ScriptedAdapter, capsys) -> str:
    """Run until the engine pauses at a step boundary, and give the run id. The run holds one probe."""
    scripted.on_specialist = ask_for_a_pause
    code, out, err, events = start(repo, capsys)
    assert event_names(events)[-1] == "paused", (code, err)
    scripted.on_specialist = None
    return str(events[0]["run_id"])


def probes(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [e for e in events if e["event"] == "guard-probe"]


def test_a_probe_that_shows_a_denial_lets_the_run_go_on_and_the_journal_records_it(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path)

    code, out, err, events = start(repo, capsys)

    assert (code, err) == (0, "")
    (probe,) = probes(events)
    assert (probe["agent"], probe["handle"], probe["result"]) == ("python-verifier", "python-verifier", "denied")
    assert Path(probe["event_stream"]).is_file()
    names = event_names(events)
    assert names.index("run-start") < names.index("guard-probe") < names.index("agent-start")
    assert len(scripted.calls) == 1 and len(scripted.verifier_calls) == 1


@pytest.mark.parametrize("result", ["ran", "inconclusive"])
def test_a_probe_that_does_not_show_a_denial_ends_the_run_before_the_first_agent_session(tmp_path, scripted, capsys, result):
    repo = repo_with_agents(tmp_path)
    scripted.probe_outcomes = {"python-verifier": result}

    code, out, err, events = start(repo, capsys)

    assert code == 1
    assert "python-verifier" in err and result in err and LIMITS_PAGE in err
    assert scripted.calls == [] and scripted.verifier_calls == []
    assert "agent-start" not in event_names(events)
    assert [p["result"] for p in probes(events)] == [result]


def test_the_probe_runs_the_sealed_verifier_on_the_cheap_tier_in_a_new_repository_of_one_commit_outside_the_repository(
    tmp_path, scripted, tiers, capsys
):
    repo = repo_with_agents(tmp_path)

    code, out, err, events = start(repo, capsys)

    (request,) = scripted.probe_calls
    (start_event,) = [e for e in events if e["event"] == "run-start"]
    (sealed,) = [e for e in start_event["agents"] if e["agent"] == "python-verifier"]
    assert request.agent == "python-verifier"
    assert request.model == tiers.model_for("cheap", "claude-code")
    assert request.sealed is not None and str(request.sealed.directory) == sealed["seal"]
    assert "touch delegate-guard-probe" in request.prompt
    assert repo not in request.cwd.parents and request.cwd != repo
    assert scripted.probe_commits == [1]
    assert scripted.probe_gone
    assert not (repo / "delegate-guard-probe").exists()


def test_each_verifier_of_the_workflow_gets_one_probe_and_a_specialist_gets_none(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path, web=True)

    start(repo, capsys)

    assert [r.agent for r in scripted.probe_calls] == ["python-verifier", "web-verifier"]


def test_an_adapter_that_does_not_probe_starts_the_run_as_before(tmp_path, capsys):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    try:
        repo = repo_with_agents(tmp_path)

        code, out, err, events = start(repo, capsys)
    finally:
        adapters.unregister("scripted")

    assert (code, err) == (0, "")
    assert adapter.probe_calls == [] and probes(events) == []


def test_a_resume_with_agents_changed_probes_again(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path)
    run_id = paused_run(repo, scripted, capsys)

    code, out, err = resume_main(repo, capsys, run_id, "--agents-changed")

    assert code == 0, err
    assert len(scripted.probe_calls) == 2
    after = read_journal_file(repo, run_id)
    assert event_names(after).index("resume") < len(after) - 1 - event_names(after)[::-1].index("guard-probe")
    assert [p["result"] for p in probes(after)] == ["denied", "denied"]


def test_a_resume_with_no_new_seal_does_not_probe(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path)
    run_id = paused_run(repo, scripted, capsys)

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0, err
    assert len(scripted.probe_calls) == 1
    assert len(probes(read_journal_file(repo, run_id))) == 1


def test_a_resume_after_a_probe_that_did_not_show_a_denial_probes_again_and_goes_on_when_it_shows_one(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path)
    scripted.probe_outcomes = {"python-verifier": "ran"}
    _, out, _, events = start(repo, capsys)
    run_id = events[0]["run_id"]
    scripted.probe_outcomes = {}

    code, out, err = resume_main(repo, capsys, run_id)

    assert code == 0, err
    assert [p["result"] for p in probes(read_journal_file(repo, run_id))] == ["ran", "denied"]
    assert len(scripted.calls) == 1


def dry_run(repo: Path, capsys) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")


@pytest.fixture
def checked(scripted):
    scripted.checks_setup = True
    return scripted


def test_the_dry_run_prints_the_probe_line_for_each_stack_with_no_problem(tmp_path, checked, capsys):
    repo = repo_with_agents(tmp_path)

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines.index("stack python: checked") < lines.index("stack python: guard probe denied")
    assert [r.agent for r in checked.probe_calls] == ["python-verifier"]
    assert checked.probe_gone


@pytest.mark.parametrize("result", ["ran", "inconclusive"])
def test_the_dry_run_exits_1_for_a_probe_that_is_not_denied_and_prints_the_result_on_stderr(tmp_path, checked, capsys, result):
    repo = repo_with_agents(tmp_path)
    checked.probe_outcomes = {"python-verifier": result}

    code, out, err = dry_run(repo, capsys)

    assert code == 1
    assert "stack python" in err and "python-verifier" in err and f"guard probe {result}" in err
    assert "guard probe" not in out
    assert not (repo / "delegate-guard-probe").exists()


def test_the_dry_run_starts_no_probe_for_a_stack_with_a_problem(tmp_path, checked, capsys):
    repo = repo_with_agents(tmp_path)
    (repo / ".claude" / "agents" / "python-specialist.md").unlink()

    code, out, err = dry_run(repo, capsys)

    assert code == 1
    assert checked.probe_calls == []
    assert "python-specialist" in err


def test_the_claude_code_adapter_probes_with_the_tier_cheap_and_the_handle_of_the_seal(tmp_path, monkeypatch, capsys, tiers):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    assert (code, err) == (0, "")
    assert "stack python: guard probe denied" in out
    (call,) = fake.probe_calls()
    argv = call["argv"]
    assert argv[argv.index("--model") + 1] == tiers.model_for("cheap", "claude-code")
    assert argv[argv.index("--agent") + 1] == "delegate-seal-python-verifier:python-verifier"
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert repo not in Path(call["cwd"]).parents
    assert fake.calls() == []


def test_a_claude_code_session_that_wrote_the_file_ends_the_run_with_no_agent_session(tmp_path, monkeypatch, capsys):
    fake = claude_fake.install(tmp_path, monkeypatch, "finished.jsonl", probe_touches=True)
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    assert "python-verifier" in err and "ran" in err and LIMITS_PAGE in err
    assert fake.calls() == []
    assert [p["result"] for p in probes(read_journal(out))] == ["ran"]


def replay_as_the_probe(tmp_path, monkeypatch, lines: list[str]) -> None:
    """Make the stand-in replay `lines` for the probe, in place of the stream of a denial."""
    stream = tmp_path / "probe-stream.jsonl"
    stream.write_text("\n".join(lines) + "\n")
    monkeypatch.setenv("FAKE_HARNESS_PROBE", str(stream))


def result_line(text: str) -> str:
    return json.dumps({"type": "result", "subtype": "success", "is_error": False, "result": text, "session_id": "s"})


def call_line(command: str, tool: str = "Bash", call: str = "t") -> str:
    block = {"type": "tool_use", "id": call, "name": tool, "input": {"command": command}}
    return json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [block]}, "session_id": "s"})


def tool_line(text: str, is_error: bool = True, call: str = "t") -> str:
    block = {"type": "tool_result", "tool_use_id": call, "is_error": is_error, "content": text}
    return json.dumps({"type": "user", "message": {"role": "user", "content": [block]}, "session_id": "s"})


PROBE = "touch delegate-guard-probe"
REFUSED_BY_MODE = "Permission to use Bash has been denied because Claude Code is running in dontAsk mode"
RATE_LIMIT = json.dumps({"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "overageStatus": "rejected"}})


@pytest.mark.parametrize(
    "lines, result",
    [
        # The session called the tool with the probe command, and the guard gave an error result. The final text does not say it.
        ([call_line(PROBE), tool_line(f"hook error: the command [{PROBE}] is not permitted"), result_line("Done.")], "denied"),
        # The agent writes a denial in its final text, and it did not call the tool. A model can decline the command.
        ([result_line("The tool says: the command is not permitted.")], "inconclusive"),
        # A tool result names the file and the denial, and no tool call of the probe command goes with it.
        ([tool_line(f"hook error: the command [{PROBE}] is not permitted"), result_line("Done.")], "inconclusive"),
        # The guard denied another command, and the final text tells of a denial.
        ([call_line("touch other-file"), tool_line("hook error: [touch other-file] is not permitted"), result_line("Denied.")], "inconclusive"),
        # A tool other than the shell tool holds the probe command, and its error result has a denial word. The guard is on the shell tool.
        ([call_line(PROBE, tool="mcp__shell__run"), tool_line(f"the command [{PROBE}] is not permitted"), result_line("Denied.")], "inconclusive"),
        # The probe command ran with no error, and the final text tells of a denial.
        ([call_line(PROBE), tool_line("", is_error=False), result_line("The command was denied.")], "inconclusive"),
        # The probe command gave an error that is no denial.
        ([call_line(PROBE), tool_line("touch: no space left on device"), result_line("It did not work.")], "inconclusive"),
        # The stream holds a field that looks like a denial, and the session never tried the command.
        ([RATE_LIMIT, result_line("I did not try the command.")], "inconclusive"),
        # A permission mode of the host refused the tool, so the guard did not decide.
        ([call_line(PROBE), tool_line(f"{REFUSED_BY_MODE} [{PROBE}]"), result_line("The tool was denied.")], "inconclusive"),
        # The guard denied the probe command, then the agent ran a second command by its own decision, and a permission mode refused it (#268).
        (
            [
                call_line(PROBE, call="a"), tool_line(f"hook error: the command [{PROBE}] is not permitted", call="a"),
                call_line('touch "$TMPDIR/delegate-guard-probe"', call="b"), tool_line(f"{REFUSED_BY_MODE} [touch x]", call="b"),
                result_line("Both commands were denied."),
            ],
            "denied",
        ),
        # The guard denied the probe command, then a second command gave an error that is no denial (#268).
        (
            [
                call_line(PROBE, call="a"), tool_line(f"hook error: the command [{PROBE}] is not permitted", call="a"),
                call_line("ls missing", call="b"), tool_line("ls: missing: No such file or directory", call="b"), result_line("Done."),
            ],
            "denied",
        ),
        # The first error of the probe command is no denial, and a later error of the same command holds one. The first error decides (#268).
        (
            [
                call_line(PROBE, call="a"), tool_line("touch: no space left on device", call="a"),
                call_line(PROBE, call="b"), tool_line(f"hook error: the command [{PROBE}] is not permitted", call="b"), result_line("Done."),
            ],
            "inconclusive",
        ),
        # A permission mode refused a second command first, and the guard denied the probe command after it (#268).
        (
            [
                call_line("echo hello", call="a"), tool_line(f"{REFUSED_BY_MODE} [echo hello]", call="a"),
                call_line(PROBE, call="b"), tool_line(f"hook error: the command [{PROBE}] is not permitted", call="b"), result_line("Done."),
            ],
            "denied",
        ),
        # The session ended with an error of the API.
        ([json.dumps({"type": "result", "subtype": "success", "is_error": True, "result": "denied", "session_id": "s"})], "inconclusive"),
    ],
)
def test_the_result_comes_from_the_facts_of_the_session_and_not_from_words_in_a_stream_that_look_like_a_denial(
    tmp_path, monkeypatch, capsys, lines, result
):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    replay_as_the_probe(tmp_path, monkeypatch, lines)
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    if result == "denied":
        assert (code, err) == (0, "")
        assert "stack python: guard probe denied" in out
    else:
        assert code == 1
        assert f"guard probe {result}" in err


def test_a_harness_command_that_does_not_exist_stops_the_start_with_one_line_that_names_it_and_no_traceback(tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    write_project_agents(repo)
    bin_dir = tmp_path / "git-only-bin"
    bin_dir.mkdir()
    (bin_dir / "git").symlink_to(shutil.which("git"))
    # The gate check at the start of a run (#296) runs the Bash hook of the sealed verifier, so the directory holds `bash`,
    # `sh` and `python3` too. With no `bash`, the check runs the hook with `sh`, which is dash on Linux and fails (#297).
    for tool in ("bash", "sh", "python3"):
        (bin_dir / tool).symlink_to(shutil.which(tool))
    monkeypatch.setenv("PATH", str(bin_dir))

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 1
    assert len(err.strip().splitlines()) == 1
    assert "claude" in err and "guard probe" in err
    assert "Traceback" not in err
    assert "guard-probe" not in event_names(read_journal(out))


def tries_of(probe: dict[str, object]) -> list[str]:
    return [str(t["result"]) for t in probe["tries"]]  # type: ignore[union-attr]


def test_the_probe_brief_tells_the_facts_of_the_session_and_holds_the_command_once(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path)

    start(repo, capsys)

    (request,) = scripted.probe_calls
    brief = request.prompt
    assert brief.count("touch delegate-guard-probe") == 1
    text = " ".join(brief.split()).lower()
    assert "new temporary directory" in text
    assert "made for this check" in text
    assert "no work under review" in text
    assert "test of the guard" in text
    assert "expects the guard to deny" in text
    assert "bash tool" in text


def test_a_probe_session_with_no_call_of_the_command_runs_once_more_and_the_denial_of_the_second_session_counts(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path)
    scripted.probe_outcomes = {"python-verifier": ["inconclusive", "denied"]}

    code, out, err, events = start(repo, capsys)

    assert (code, err) == (0, "")
    assert len(scripted.probe_calls) == 2
    (probe,) = probes(events)
    assert probe["result"] == "denied"
    assert tries_of(probe) == ["inconclusive", "denied"]
    first, second = (Path(t["event_stream"]) for t in probe["tries"])
    assert first != second and first.is_file() and second.is_file()
    assert probe["event_stream"] == str(second)
    assert len(scripted.calls) == 1


def test_a_probe_with_no_call_of_the_command_in_two_sessions_is_inconclusive_and_names_both_tries(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path)
    scripted.probe_outcomes = {"python-verifier": "inconclusive"}

    code, out, err, events = start(repo, capsys)

    assert code == 1
    assert len(scripted.probe_calls) == 2
    (probe,) = probes(events)
    assert probe["result"] == "inconclusive" and tries_of(probe) == ["inconclusive", "inconclusive"]
    assert "inconclusive" in err and scripted.calls == []


@pytest.mark.parametrize("outcome", ["ran", "refused"])
def test_a_probe_session_that_ran_the_file_or_met_a_permission_mode_does_not_run_a_second_session(tmp_path, scripted, capsys, outcome):
    repo = repo_with_agents(tmp_path)
    scripted.probe_outcomes = {"python-verifier": [outcome, "denied"]}

    code, out, err, events = start(repo, capsys)

    assert code == 1
    assert len(scripted.probe_calls) == 1
    (probe,) = probes(events)
    assert probe["result"] == {"ran": "ran", "refused": "inconclusive"}[outcome]
    assert tries_of(probe) == [probe["result"]]


def test_a_probe_session_with_a_denial_the_first_time_does_not_run_a_second_session(tmp_path, scripted, capsys):
    repo = repo_with_agents(tmp_path)

    code, out, err, events = start(repo, capsys)

    assert len(scripted.probe_calls) == 1
    (probe,) = probes(events)
    assert tries_of(probe) == ["denied"]


def test_the_dry_run_names_each_try_of_a_probe_that_ran_two_sessions(tmp_path, checked, capsys):
    repo = repo_with_agents(tmp_path)
    checked.probe_outcomes = {"python-verifier": ["inconclusive", "denied"]}

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    assert "stack python: guard probe denied (2 tries: inconclusive, denied)" in out.splitlines()
    assert len(checked.probe_calls) == 2


def test_the_dry_run_names_both_tries_of_a_probe_that_stays_inconclusive(tmp_path, checked, capsys):
    repo = repo_with_agents(tmp_path)
    checked.probe_outcomes = {"python-verifier": "inconclusive"}

    code, out, err = dry_run(repo, capsys)

    assert code == 1
    assert "guard probe inconclusive" in err and "2 tries: inconclusive, inconclusive" in err


def test_a_claude_code_session_with_no_tool_call_the_first_time_and_a_denial_the_second_time_gives_denied(tmp_path, monkeypatch, capsys):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    stream = tmp_path / "probe-stream.jsonl"
    stream.write_text(result_line("I did not run it: my rules make me read-only.") + "\n")
    second = tmp_path / "probe-stream-2.jsonl"
    second.write_text("\n".join([call_line(PROBE), tool_line(f"hook error: the command [{PROBE}] is not permitted"), result_line("Done.")]) + "\n")
    monkeypatch.setenv("FAKE_HARNESS_PROBE", f"{stream}{os.pathsep}{second}")
    repo = make_repo(tmp_path, WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    assert (code, err) == (0, "")
    assert "stack python: guard probe denied (2 tries: inconclusive, denied)" in out
