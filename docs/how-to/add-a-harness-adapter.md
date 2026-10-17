# How to add a harness adapter

Use this page when you want the [**engine**](../glossary.md#engine) to drive a [**harness**](../glossary.md#harness) that has no [**adapter**](../glossary.md#adapter) yet. An adapter is a small Python object. It starts the [**headless**](../glossary.md#headless) command of a harness, and turns the [**event stream**](../glossary.md#event-stream) of the harness into a result that the engine understands.

An adapter also reads a stream of its harness: it gives the tokens so far, as two counts, and the time of the newest event. `delegate status` uses them to show what an agent does while it runs.

You do not read the engine for this. You need the interface below, one recorded stream for each [**end state**](../glossary.md#end-state), and the [**contract test**](../glossary.md#contract-test) of this page.

## The interface

An adapter is one object behind one interface. The interface is the harness [**port**](../glossary.md#port), in `delegate/ports/harness.py`. The adapters are in the package `delegate/adapters/`.

The engine gives the adapter an `AdapterRequest`. It has these fields:

- `agent`: the name of the agent to run.
- `model`: the model, which the engine took from the [**tier**](../glossary.md#tier) column of the adapter. The adapter never chooses a model.
- `prompt`: the [**brief**](../glossary.md#brief) for the agent.
- `cwd`: the working directory, which is a [**worktree**](../glossary.md#worktree) or a [**temporary copy**](../glossary.md#temporary-copy).
- `report_path`: where the agent writes its JSON report.
- `event_stream`: the file where the adapter writes the event stream of the harness. The engine chooses it, and writes it to the [**journal**](../glossary.md#journal) before the agent starts, so `delegate status` can read the stream while the agent runs. Its directory exists.
- `resume_session`: the [**session**](../glossary.md#session) id of an earlier run to resume, or `None`.
- `idle_timeout` and `report_valid`: the idle timeout in seconds, and a function that gives true when the file at `report_path` is valid. When the check is true and the harness wrote no event for the idle timeout, the adapter ends the session. The helper `run_harness` does this for you.

The adapter gives back an `AdapterResult`. It has these fields:

- `exit_status`: the exit status of the harness process.
- `end_state`: `finished`, `failed`, or `capped`. Any other value is an error. The stream of the session decides it. The exit status does not change it: the engine weighs the exit status itself.
- `session_id`: the session id that the harness gave, or `None`.
- `event_stream`: the path of the captured stream, which is the `event_stream` of the request.
- `ended_idle`: true when the adapter ended the session at the idle timeout. `run_harness` tells you.
- `refused`: one pair for each tool call that the permission settings of the harness refused, in the order of the stream: the tool, and the name that the harness gives that setting (for example `don't ask`). Leave it empty when your harness does not write such a refusal. The engine copies it into the result event, and `delegate watch` shows it as a problem.
- `final_text`: the last text of the agent in the stream, or `None`. The engine reads the [**verdict**](../glossary.md#verdict) from it when a [**verifier**](../glossary.md#verifier) writes no [**report**](../glossary.md#report) file. Leave it `None` when your harness stream has no such text.

The adapter class has two attributes and two methods:

- `tier_column`: the name of the column of the [**tier table**](../glossary.md#tier-table) that holds the models of this harness.
- `supports_resume`: true when the harness can resume a session. The engine then sets `resume_session` for a [**continuation**](../glossary.md#continuation).
- `run`: takes the request and returns the result.
- `activity`: takes the path of a stream that the adapter wrote, and returns an `Activity` with three fields. `tokens_new` is the new tokens that the agent used so far: the input, the cache writes, and the output. `tokens_cache` is the cache reads so far. `last_event` is the time (UTC) of the newest event that has a time, or `None`. The stream can be the stream of an agent that still runs, so it can end in half a line.

The port module also gives you `AdapterError`, for a harness that cannot run the agent at all. The module `delegate/adapters/process.py` gives you the helper `run_harness`. It starts the command with the prompt on standard input, and writes the standard output to the event stream of the request. It also ends an idle session after a valid report. It returns the exit status, the standard error, and `ended_idle`. The module `delegate/adapters/streams.py` gives you the helper `read_events`. It reads a line-delimited JSON stream, and ignores half a line at the end. A missing file, or a bad line before the end, raises `AdapterError`. The package `delegate.adapters` holds the registry: `register` adds an adapter under a name, as the tests of the engine do. See [the adapter interface](../reference/run.md#the-adapter-interface) for the rules of each part.

## Steps

### 1. Measure the harness

Run the headless command of the harness on a real machine. Write down these facts:

- The command, and the option for the model, for the agent, and for the session.
- How the harness takes the prompt. Put it on standard input when you can, so it is not in the arguments.
- The event that ends a [**run**](../glossary.md#run), and how it shows a normal end, an output limit, and an error.
- Whether the harness selects a named agent. If it cannot, send the body of the agent file as part of the prompt.

Record one stream for each end state. Write the version of the harness and the date in a manifest, because a harness changes.

### 2. Write the adapter

This example drives a harness `myharness`. The prompt goes on standard input. The stream has one JSON event on each line. Each event has a `time`, and a `usage` event gives the tokens of one model call: `new` for the new tokens, and `cache` for the cache reads.

```python title="myharness.py"
"""The MyHarness adapter: drive `myharness run --json`."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from delegate.adapters.process import run_harness
from delegate.adapters.streams import read_events
from delegate.ports.harness import CAPPED, FAILED, FINISHED, Activity, AdapterError, AdapterRequest, AdapterResult

# The command that starts the harness.
COMMAND: str = "myharness"

# The `status` of the last `result` event, mapped to the end state of the run.
END_STATE_OF_STATUS: dict[str, str] = {"done": FINISHED, "length": CAPPED}


class MyHarnessAdapter:
    """Run one agent through `myharness run`."""

    tier_column: str = "myharness"
    supports_resume: bool = True

    def run(self, request: AdapterRequest) -> AdapterResult:
        command = [COMMAND, "run", "--json", "--model", request.model, "--agent", request.agent]
        if request.resume_session is not None:
            command += ["--session", request.resume_session]
        stream = request.event_stream
        process = run_harness(command, request)
        if not stream.read_text().strip():
            raise AdapterError(
                f"{COMMAND} exited with status {process.returncode} and wrote no event for agent {request.agent!r}: "
                f"{process.stderr.strip()}"
            )
        events = read_events(stream)
        session_id = next((str(event["session"]) for event in events if "session" in event), None)
        results = [event for event in events if event.get("type") == "result"]
        end_state = END_STATE_OF_STATUS.get(str(results[-1].get("status")), FAILED) if results else FAILED
        return AdapterResult(process.returncode, end_state, session_id, stream, process.ended_idle)

    def activity(self, stream: Path) -> Activity:
        events = read_events(stream)
        usages = [event for event in events if event.get("type") == "usage"]
        new = sum(int(event["new"]) for event in usages)
        cache = sum(int(event["cache"]) for event in usages)
        times = [datetime.fromisoformat(str(event["time"])) for event in events if "time" in event]
        return Activity(new, cache, times[-1] if times else None)
```

The adapter fails loud. A harness that writes no event raises `AdapterError` with a message that names the agent. A stream with no `result` event ends `failed`. The exit status stays beside the end state and does not change it: the engine weighs the exit status itself.

### 3. Record the streams and write the contract test

A contract test turns a recorded stream into a result and checks the fields of the result. The harness process is the system boundary, so the test replaces it with a stand-in command that replays the recording. The adapter starts the stand-in as a subprocess, as it starts the real command.

Keep the recorded streams in `tests/fixtures/`.

```json title="tests/fixtures/finished.jsonl"
{"type": "session", "session": "ses-1", "time": "2026-10-08T19:55:10+00:00"}
{"type": "text", "text": "The work is done.", "time": "2026-10-08T19:55:11+00:00"}
{"type": "usage", "new": 1200, "cache": 3400, "time": "2026-10-08T19:55:11+00:00"}
{"type": "result", "status": "done", "time": "2026-10-08T19:55:12+00:00"}
```

```json title="tests/fixtures/capped.jsonl"
{"type": "session", "session": "ses-2", "time": "2026-10-08T19:56:00+00:00"}
{"type": "text", "text": "The work is not", "time": "2026-10-08T19:56:01+00:00"}
{"type": "usage", "new": 900, "cache": 0, "time": "2026-10-08T19:56:01+00:00"}
{"type": "result", "status": "length", "time": "2026-10-08T19:56:02+00:00"}
```

The manifest holds the version of the harness and the date of the recording, so a reader knows when to measure again.

```json title="tests/fixtures/manifest.json"
{
  "harness": "MyHarness",
  "harness_version": "1.2.3",
  "recorded": "2026-10-08",
  "fixtures": {
    "finished.jsonl": {"end_state": "finished"},
    "capped.jsonl": {"end_state": "capped"}
  }
}
```

The test file below holds the cases that each adapter needs. Add a case for each end state and each error that your harness can give.

```python title="tests/test_myharness_adapter.py"
"""The contract of the MyHarness adapter: it turns a recorded stream into a result."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from delegate.ports.harness import Activity, AdapterError, AdapterRequest

from myharness import MyHarnessAdapter

FIXTURES: Path = Path(__file__).parent / "fixtures"


def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stream: Path, exit_status: int = 0) -> Path:
    """Put a stand-in `myharness` first on PATH. It records its call, replays the stream, and exits with the status."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "call.json"
    stand_in = bin_dir / "myharness"
    stand_in.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}, open({str(log)!r}, 'w'))\n"
        f"sys.stdout.write(open({str(stream)!r}).read())\n"
        f"sys.exit({exit_status})\n"
    )
    stand_in.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return log


def request(tmp_path: Path, **changes: object) -> AdapterRequest:
    cwd = tmp_path / "work"
    cwd.mkdir(exist_ok=True)
    (tmp_path / "reports").mkdir(exist_ok=True)  # the engine names a stream in a directory that exists
    fields: dict[str, object] = dict(
        agent="python-specialist", model="gateway/large-model", prompt="## Task\nTicket a\n", cwd=cwd,
        report_path=tmp_path / "reports" / "a.specialist.json", event_stream=tmp_path / "reports" / "a.specialist.stream.jsonl",
    )
    fields.update(changes)
    return AdapterRequest(**fields)  # type: ignore[arg-type]


def test_a_finished_stream_gives_finished_and_the_session_id_and_the_captured_stream(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, FIXTURES / "finished.jsonl")

    result = MyHarnessAdapter().run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (0, "finished", "ses-1")
    assert result.event_stream == tmp_path / "reports" / "a.specialist.stream.jsonl"
    assert result.event_stream.read_text() == (FIXTURES / "finished.jsonl").read_text()


def test_a_stream_that_stops_at_the_output_limit_gives_capped_and_keeps_the_session_id(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, FIXTURES / "capped.jsonl")

    result = MyHarnessAdapter().run(request(tmp_path))

    assert (result.end_state, result.session_id) == ("capped", "ses-2")


def test_a_finished_stream_with_a_failing_exit_status_stays_finished_and_keeps_the_exit_status(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, FIXTURES / "finished.jsonl", exit_status=143)

    result = MyHarnessAdapter().run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.ended_idle) == (143, "finished", False)


def test_the_command_carries_the_model_and_the_agent_of_the_request_and_the_prompt_goes_on_stdin(tmp_path, monkeypatch):
    log = install(tmp_path, monkeypatch, FIXTURES / "finished.jsonl")

    MyHarnessAdapter().run(request(tmp_path, model="gateway/medium-model", agent="python-verifier", prompt="SECRET-BRIEF\n"))

    call = json.loads(log.read_text())
    assert call["argv"][call["argv"].index("--model") + 1] == "gateway/medium-model"
    assert call["argv"][call["argv"].index("--agent") + 1] == "python-verifier"
    assert call["stdin"] == "SECRET-BRIEF\n" and "SECRET-BRIEF" not in " ".join(call["argv"])
    assert "--session" not in call["argv"]


def test_a_continuation_resumes_the_session_of_the_last_run(tmp_path, monkeypatch):
    log = install(tmp_path, monkeypatch, FIXTURES / "finished.jsonl")

    MyHarnessAdapter().run(request(tmp_path, resume_session="ses-1"))

    argv = json.loads(log.read_text())["argv"]
    assert argv[argv.index("--session") + 1] == "ses-1"


def test_a_harness_that_writes_no_event_raises_and_names_the_agent(tmp_path, monkeypatch):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    install(tmp_path, monkeypatch, empty)

    with pytest.raises(AdapterError, match="python-specialist"):
        MyHarnessAdapter().run(request(tmp_path))


def test_the_activity_of_a_stream_gives_its_tokens_and_the_time_of_its_newest_event():
    activity = MyHarnessAdapter().activity(FIXTURES / "finished.jsonl")

    assert activity == Activity(1200, 3400, datetime(2026, 10, 8, 19, 55, 12, tzinfo=timezone.utc))


def test_the_activity_of_a_live_stream_that_ends_in_half_a_line_reads_the_complete_lines(tmp_path):
    lines = (FIXTURES / "finished.jsonl").read_text().splitlines(keepends=True)
    live = tmp_path / "live.jsonl"
    live.write_text("".join(lines[:3]) + lines[3][:20])

    assert MyHarnessAdapter().activity(live) == Activity(1200, 3400, datetime(2026, 10, 8, 19, 55, 11, tzinfo=timezone.utc))


def test_each_recording_is_in_the_manifest_with_the_harness_version_and_the_date():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())

    assert manifest["harness_version"] and manifest["recorded"]
    assert sorted(manifest["fixtures"]) == sorted(path.name for path in FIXTURES.glob("*.jsonl"))
```

Three more cases belong in the file for a real harness. Give one case for a stream that is cut off before its last event. Give one for a line that is not JSON. Give one for the activity of a stream that has no event with a time.

### 4. Connect the adapter to the kit

The command-line driver finds a built-in adapter by name and hands it to the engine. The engine imports the port only. Make these changes in the package `delegate`:

1. Put the adapter module in `delegate/adapters/`, beside `claude_code.py` and `opencode.py`.
2. Add a branch for the name in `get` in `delegate/adapters/__init__.py`.
3. Add the name to `ADAPTERS` in `run/workflow.py`, so a [**workflow**](../glossary.md#workflow) can name it.
4. Add a column that `tier_column` names to each tier in `tiers.toml`.

Then check the model. A test runs `delegate run` with the stand-in command and a workflow that names the adapter. It reads the model from the call of the stand-in. The model must be the model of the tier column for the [**mode**](../glossary.md#mode). See `tests/test_run_opencode.py` in the repository for the pattern.

A user who cannot change the kit can still add a column in a [**tier file**](../glossary.md#tier-file) of their own. See [how to point the tiers at a gateway](point-the-tiers-at-a-gateway.md).

### 5. Run the harness for real, and write the reference page

The recorded streams show that the adapter reads them. They do not show that the real harness gives them. Run the adapter once against the real harness on a real machine. Write the reference page of the adapter. Put in it the version of the harness, the date, the command, the end states, the recorded streams, and the result of the run. Link the page from the README. See [the OpenCode adapter reference](../reference/opencode-adapter.md) for a page of this kind.

## The contract-test pattern

Each adapter has three kinds of test:

1. **Contract tests** replay a recorded stream through a stand-in command and check the fields of the result. They also check the command: the model and the agent of the request, and the prompt on standard input. And they check the activity of a recorded stream. They also check the activity of a part of a stream that ends in half a line, as a live stream can.
2. **A model test** runs `delegate run` through `main(argv)` and checks that the model on the command comes from the tier column of the adapter.
3. **A live run** on a real machine, noted once in the reference page.

A test never imports a private function and never checks a log line. The stand-in replaces only the harness process.
