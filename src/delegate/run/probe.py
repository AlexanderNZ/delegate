"""The guard probe: a short session that shows that the harness runs the guard of a sealed agent (#219).

The harness hooks did not run in the verifier sessions of this repository from 2026-10-16 to 2026-10-31, and nothing
showed it: a guard that does not run writes no event. So a run starts no agent session before the guard probe of each
verifier of the run shows a denial. The probe is one session of the sealed verifier, through the harness port, on the tier
`cheap`. It runs in a new temporary directory outside the repository, which is a git repository with one commit. Its
brief asks for one command, `touch delegate-guard-probe`, which the guard must deny.

The engine decides from facts, not from the opinion of the agent:

- `ran`: the file `delegate-guard-probe` exists after the session. The guard did not stop the command.
- `denied`: the file does not exist, the session ended `finished` with no permission-mode refusal, and the event stream or
  the final text shows a denial of the command: a line of the stream that names the file and holds a word of `DENIAL`, or a
  final text that holds such a word. The stream alone is not searched for a word, because a stream holds fields that look
  like a denial (the rate-limit event of Claude Code has `"overageStatus":"rejected"`).
- `inconclusive`: any other result, for example a session that did not try the command, that ended `failed`, that ran past
  its time limit, or that a permission mode of the host refused (then the guard did not decide).

The module asks the port for the repository and the adapter for the session. It starts no process itself. `delegate.cli`
uses it for the dry run, and the engine uses it at the start of a run.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..ports.harness import FINISHED, Adapter, AdapterRequest, AdapterResult, SealedAgent
from ..ports.vcs import VersionControl

# The file that the command makes, the command, and the tier of the session.
PROBE_FILE = "delegate-guard-probe"
PROBE_COMMAND = f"touch {PROBE_FILE}"
PROBE_TIER = "cheap"

# The time limit of the session, in seconds. A session that runs longer ends, and its result is `inconclusive`.
PROBE_TIMEOUT = 300.0

# The directory of the event streams of the probes of a run, under the run directory.
PROBE_DIRECTORY = "probes"

# The results of a probe.
DENIED = "denied"
RAN = "ran"
INCONCLUSIVE = "inconclusive"

# The words with which a guard and a permission system tell a person that a command did not run.
DENIAL = re.compile(r"denied|not permitted|not allowed|blocked|refused|rejected|forbidden", re.IGNORECASE)

# The page that tells what the guard of an agent does not cover.
LIMITS_PAGE = "docs/explanation/the-enforcement-model-and-its-limits.md"

BRIEF = f"""\
This is a short check of your tool setup. It has no ticket and no report.

Run this command one time in your working directory, with your Bash tool, exactly as it is:

    {PROBE_COMMAND}

Then give the output or the error of the tool in your final text, word for word. Run no other command.
"""


@dataclass(frozen=True)
class Probe:
    """The result of one guard probe: the agent, its handle, `denied`, `ran` or `inconclusive`, and the event stream of the session."""

    agent: str
    handle: str
    result: str
    event_stream: Path


def stream_path(run_directory: Path, agent: str) -> Path:
    """The file for the event stream of a probe of `agent`: in `<run directory>/probes/`, and never over an earlier stream."""
    directory = run_directory / PROBE_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{agent}.stream.jsonl"
    number = 1
    while path.exists():
        number += 1
        path = directory / f"{agent}.stream-{number}.jsonl"
    return path


def _text_of(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def _result_of(session: AdapterResult, file_exists: bool) -> str:
    """The result that the facts of the session give: `ran`, `denied` or `inconclusive`."""
    if file_exists:
        return RAN
    if session.end_state != FINISHED or session.ended_timeout or session.reason is not None or session.refused:
        return INCONCLUSIVE
    in_stream = any(PROBE_FILE in line and DENIAL.search(line) for line in _text_of(session.event_stream).splitlines())
    return DENIED if in_stream or DENIAL.search(session.final_text or "") else INCONCLUSIVE


def probe_guard(adapter: Adapter, vcs: VersionControl, sealed: SealedAgent, model: str, stream: Path) -> Probe:
    """Run the guard probe of the sealed agent `sealed` through `adapter`, with `model`, and give the result.

    The session runs in a new temporary directory that holds a git repository with one commit, and the directory goes
    when the session ends. `stream` is the file for the event stream. An `AdapterError` of the adapter and a `VcsError`
    of the port go up: the harness or the repository could not run the probe, which is no result.
    """
    scratch = Path(tempfile.mkdtemp(prefix="delegate-guard-probe-"))
    try:
        vcs.make_scratch_repository(scratch)
        request = AdapterRequest(
            agent=sealed.agent, model=model, prompt=BRIEF, cwd=scratch, report_path=stream.with_name(f"{sealed.agent}.probe-report.json"),
            event_stream=stream, agent_timeout=PROBE_TIMEOUT, sealed=sealed,
        )
        session = adapter.run(request)
        result = _result_of(session, (scratch / PROBE_FILE).exists())
    finally:
        vcs.stop_file_watcher(scratch)
        shutil.rmtree(scratch, ignore_errors=True)
    return Probe(sealed.agent, sealed.handle, result, session.event_stream)


def refusal(agent: str, result: str) -> str:
    """The text for a probe that did not show a denial: the result, the verifier, and the limits page."""
    return (
        f"guard probe {result}, not denied: the harness did not show that it runs the guard of the sealed verifier {agent!r}; "
        f"see the limits page, {LIMITS_PAGE}"
    )
