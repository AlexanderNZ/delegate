# How to add a harness adapter

Use this page when you want the engine to drive a harness that has no adapter yet. An adapter is a small Python object. It starts the headless command of a harness, and turns the event stream of the harness into a result that the engine understands.

You do not read the engine for this. You need the interface below, one recorded stream for each end state, and the contract test of this page.

## The interface

An adapter is one object behind one interface. It lives in `agent_definitions/adapters.py`.

The engine gives the adapter an `AdapterRequest`. It has these fields:

- `agent`: the name of the agent to run.
- `model`: the model, which the engine took from the tier column of the adapter. The adapter never chooses a model.
- `prompt`: the brief for the agent.
- `cwd`: the working directory, which is a worktree or a temporary copy.
- `report_path`: where the agent writes its JSON report.
- `resume_session`: the session id of an earlier run to resume, or `None`.

The adapter gives back an `AdapterResult`. It has these fields:

- `exit_status`: the exit status of the harness process.
- `end_state`: `finished`, `failed`, or `capped`. Any other value is an error.
- `session_id`: the session id that the harness gave, or `None`.
- `event_stream`: the path of the captured stream.

The adapter class has two attributes and one method:

- `tier_column`: the name of the column of the tier table that holds the models of this harness.
- `supports_resume`: true when the harness can resume a session. The engine then sets `resume_session` for a continuation.
- `run`: takes the request and returns the result.

The module also gives you three more parts. `AdapterError` is for a harness that cannot run the agent at all. `stream_path` gives a file for the stream, beside the report. `read_events` reads a line-delimited JSON stream. `register` adds an adapter to the registry under a name, as the tests of the engine do. See [the adapter interface](../reference/run.md#the-adapter-interface) for the rules of each part.

## Steps

### 1. Measure the harness

Run the headless command of the harness on a real machine. Write down these facts:

- The command, and the option for the model, for the agent, and for the session.
- How the harness takes the prompt. Put it on standard input when you can, so it is not in the arguments.
- The event that ends a run, and how it shows a normal end, an output limit, and an error.
- Whether the harness selects a named agent. If it cannot, send the body of the agent file as part of the prompt.

Record one stream for each end state. Write the version of the harness and the date in a manifest, because a harness changes.

### 2. Write the adapter

This example drives a harness `myharness`. The prompt goes on standard input. The stream has one JSON event on each line.

```python myharness.py
"""The MyHarness adapter: drive `myharness run --json`."""

from __future__ import annotations

import subprocess

from agent_definitions.adapters import (
    CAPPED, FAILED, FINISHED, AdapterError, AdapterRequest, AdapterResult, read_events, stream_path,
)

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
        stream = stream_path(request.report_path)
        with stream.open("w") as out:
            process = subprocess.run(
                command, input=request.prompt, stdout=out, stderr=subprocess.PIPE, text=True, cwd=request.cwd
            )
        if not stream.read_text().strip():
            raise AdapterError(
                f"{COMMAND} exited with status {process.returncode} and wrote no event for agent {request.agent!r}: "
                f"{process.stderr.strip()}"
            )
        events = read_events(stream)
        session_id = next((str(event["session"]) for event in events if "session" in event), None)
        results = [event for event in events if event.get("type") == "result"]
        end_state = END_STATE_OF_STATUS.get(str(results[-1].get("status")), FAILED) if results else FAILED
        if process.returncode != 0 and end_state == FINISHED:
            end_state = FAILED
        return AdapterResult(process.returncode, end_state, session_id, stream)
```

The adapter fails loud. A harness that writes no event raises `AdapterError` with a message that names the agent. A stream with no `result` event ends `failed`. A harness that exits with an error cannot end `finished`.

### 3. Record the streams and write the contract test

A contract test turns a recorded stream into a result and checks the fields of the result. The harness process is the system boundary, so the test replaces it with a stand-in command that replays the recording. The adapter starts the stand-in as a subprocess, as it starts the real command.

Keep the recorded streams in `tests/fixtures/`.

```json tests/fixtures/finished.jsonl
{"type": "session", "session": "ses-1"}
{"type": "text", "text": "The work is done."}
{"type": "result", "status": "done"}
```

```json tests/fixtures/capped.jsonl
{"type": "session", "session": "ses-2"}
{"type": "text", "text": "The work is not"}
{"type": "result", "status": "length"}
```

The manifest holds the version of the harness and the date of the recording, so a reader knows when to measure again.

```json tests/fixtures/manifest.json
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

```python tests/test_myharness_adapter.py
"""The contract of the MyHarness adapter: it turns a recorded stream into a result."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from agent_definitions.adapters import AdapterError, AdapterRequest

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
    fields: dict[str, object] = dict(
        agent="python-specialist", model="gateway/large-model", prompt="## Task\nTicket a\n", cwd=cwd,
        report_path=tmp_path / "reports" / "a.specialist.json",
    )
    fields.update(changes)
    return AdapterRequest(**fields)  # type: ignore[arg-type]


def test_a_finished_stream_gives_finished_and_the_session_id_and_the_captured_stream(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, FIXTURES / "finished.jsonl")

    result = MyHarnessAdapter().run(request(tmp_path))

    assert (result.exit_status, result.end_state, result.session_id) == (0, "finished", "ses-1")
    assert result.event_stream.read_text() == (FIXTURES / "finished.jsonl").read_text()


def test_a_stream_that_stops_at_the_output_limit_gives_capped_and_keeps_the_session_id(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, FIXTURES / "capped.jsonl")

    result = MyHarnessAdapter().run(request(tmp_path))

    assert (result.end_state, result.session_id) == ("capped", "ses-2")


def test_a_finished_stream_with_a_failing_exit_status_gives_failed(tmp_path, monkeypatch):
    install(tmp_path, monkeypatch, FIXTURES / "finished.jsonl", exit_status=3)

    result = MyHarnessAdapter().run(request(tmp_path))

    assert (result.exit_status, result.end_state) == (3, "failed")


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


def test_each_recording_is_in_the_manifest_with_the_harness_version_and_the_date():
    manifest = json.loads((FIXTURES / "manifest.json").read_text())

    assert manifest["harness_version"] and manifest["recorded"]
    assert sorted(manifest["fixtures"]) == sorted(path.name for path in FIXTURES.glob("*.jsonl"))
```

Three more cases belong in the file for a real harness. Give one case for a stream that is cut off before its last event. Give one for a line that is not JSON. Give one for the second run with one report path, which must not overwrite the first stream.

### 4. Connect the adapter to the kit

The engine finds a built-in adapter by name. Make these changes in the package `agent_definitions`:

1. Put the adapter module beside `claude_code.py` and `opencode.py`.
2. Add a branch for the name in `adapters.get`.
3. Add the name to `ADAPTERS` in `workflow.py`, so a workflow can name it.
4. Add a column that `tier_column` names to each tier in `tiers.toml`.

Then check the model. A test runs `delegate run` with the stand-in command and a workflow that names the adapter. It reads the model from the call of the stand-in. The model must be the model of the tier column for the mode. See `tests/test_run_opencode.py` in the repository for the pattern.

A user who cannot change the kit can still add a column in a tier file of their own. See [how to point the tiers at a gateway](point-the-tiers-at-a-gateway.md).

### 5. Run the harness for real, and write the reference page

The recorded streams show that the adapter reads them. They do not show that the real harness gives them. Run the adapter once against the real harness on a real machine. Write the reference page of the adapter. Put in it the version of the harness, the date, the command, the end states, the recorded streams, and the result of the run. Link the page from the README. See [the OpenCode adapter reference](../reference/opencode-adapter.md) for a page of this kind.

## The contract-test pattern

Each adapter has three kinds of test:

1. **Contract tests** replay a recorded stream through a stand-in command and check the fields of the result. They also check the command: the model and the agent of the request, and the prompt on standard input.
2. **A model test** runs `delegate run` through `main(argv)` and checks that the model on the command comes from the tier column of the adapter.
3. **A live run** on a real machine, noted once in the reference page.

A test never imports a private function and never checks a log line. The stand-in replaces only the harness process.
