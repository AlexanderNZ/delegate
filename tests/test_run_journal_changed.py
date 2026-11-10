"""The engine decides from the events that it holds in memory, and a change of its journal by another writer stops the run (#291).

The engine reads the journal file one time, when it opens the file at a resume, and decides from the events that it
read and the events that it appended. Before each append it checks that the file has the size that it wrote last. A
specialist has a full shell, so it can append a line to the journal: for example a `resume` event with other seal
hashes. The engine then puts back its own bytes, keeps the bytes of the other writer in a file, writes
`invariant-violation` with the kind `journal-changed` and `run-end` with the result `failed`, and refuses to resume.

Each case calls `delegate.main` against a real temporary git repository, with the scripted adapter in the place of the
harness. The adapter plays the other writer, as the shell of an agent can.
"""

import builtins
import hashlib
import io
import json
from pathlib import Path

import pytest

from delegate import adapters
from delegate.ports.harness import AdapterError
from delegate.run.lock import live_holder_of_run

from .support import ScriptedAdapter, event_names, journal_path, make_repo, read_journal, read_journal_file, resume_main, run_main, state_dir, ticket_of
from .test_run_agents import TAMPERED, make_repo as make_sealed_repo, seal_file
from .test_run_multi import HEAD, ticket

pytestmark = pytest.mark.usefixtures("git_identity")

THREE = HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",))


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def journal_of(request) -> Path:
    """The journal of the run of `request`: the seal directories of a run are in `<run directory>/agents/`."""
    return request.sealed.directory.parent.parent / "journal.jsonl"


def forge_resume(request) -> tuple[Path, int, bytes]:
    """Change the seal of the verifier, and append a `resume` line that records its new hash, as a shell of the agent can.

    Return the journal, its size before the line, and the line.
    """
    tampered = TAMPERED.format(name="python-verifier")
    seal_file(request, "python-verifier").write_text(tampered)
    path = journal_of(request)
    lines = path.read_text().splitlines()
    agents = json.loads(lines[0])["agents"]
    for entry in agents:
        if entry["agent"] == "python-verifier":
            for file in entry["files"]:
                file["sha256"] = hashlib.sha256(tampered.encode()).hexdigest()
    forged = {
        "seq": len(lines) + 1, "time": "2026-11-09T21:00:00+00:00", "event": "resume", "run_id": json.loads(lines[0])["run_id"],
        "built": [], "failed": [], "skipped": [], "open": "a", "agents": agents,
    }
    line = (json.dumps(forged) + "\n").encode()
    size = path.stat().st_size
    with path.open("ab") as handle:
        handle.write(line)
    return path, size, line


def test_a_forged_resume_line_from_the_specialist_does_not_pass_the_seal_check_and_ends_the_run_failed(tmp_path, capsys):
    repo = make_sealed_repo(tmp_path)
    forged: list[tuple[Path, int, bytes]] = []
    adapter = ScriptedAdapter(on_specialist=lambda request: forged.append(forge_resume(request)))
    adapters.register("scripted", adapter)
    try:
        code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
        events = read_journal(out)
        resumed = resume_main(repo, capsys, str(events[0]["run_id"]))
    finally:
        adapters.unregister("scripted")

    ((path, offset, line),) = forged
    assert code == 1
    assert adapter.verifier_calls == []  # the forged hashes did not let the changed verifier run
    assert "resume" not in event_names(events)  # the engine put back its own bytes
    (violation,) = [e for e in events if e["event"] == "invariant-violation"]
    assert violation["kind"] == "journal-changed"
    assert (violation["journal"], violation["offset"]) == (str(path), offset)
    assert (violation["size_written"], violation["size_found"]) == (offset, offset + len(line))
    assert Path(violation["changed_bytes"]).read_bytes() == line
    step_end = next(e for e in events if e["event"] == "step-end")
    assert (step_end["ticket"], step_end["state"]) == ("a", "failed")
    assert step_end["reason"].startswith("invariant violation: ")
    assert event_names(events)[-1] == "run-end"
    assert events[-1]["result"] == "failed"
    assert f"journal {path}" in err and f"byte {offset}" in err
    assert "crashed" not in event_names(events)

    code, out, err = resumed
    assert code == 1
    assert f"journal {path} at byte {offset}" in err and "start a new run" in err
    assert len(forged) == 1  # the refused resume started no specialist
    assert read_journal_file(repo, str(events[0]["run_id"])) == events  # the refusal appends no event


def test_a_rewrite_of_the_seal_hash_in_run_start_that_keeps_the_size_does_not_pass_the_seal_check(tmp_path, capsys):
    # The new hash has the length of the old one, so the file keeps its size and the size check sees no change. The
    # seal check still compares with the hash that the engine wrote, not with the line on the disk.
    repo = make_sealed_repo(tmp_path)
    recorded = sha256_of(repo / ".claude" / "agents" / "python-verifier.md")
    tampered = TAMPERED.format(name="python-verifier")
    new = hashlib.sha256(tampered.encode()).hexdigest()

    def rewrite(request) -> None:
        seal_file(request, "python-verifier").write_text(tampered)
        path = journal_of(request)
        size = path.stat().st_size
        path.write_text(path.read_text().replace(recorded, new))
        assert path.stat().st_size == size

    adapter = ScriptedAdapter(on_specialist=rewrite)
    adapters.register("scripted", adapter)
    try:
        code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    finally:
        adapters.unregister("scripted")

    events = read_journal(out)
    assert code == 1
    assert adapter.verifier_calls == []
    (changed,) = [e for e in events if e["event"] == "agents-changed"]
    assert (changed["agent"], changed["recorded_hash"], changed["new_hash"]) == ("python-verifier", recorded, new)
    assert (events[-1]["event"], events[-1]["result"]) == ("run-end", "failed")


def test_a_journal_that_another_writer_cut_is_put_back_and_the_offset_is_where_the_cut_starts(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)
    cuts: list[tuple[Path, int]] = []

    def cut(request) -> None:
        if ticket_of(request) == "b":
            path = journal_path(repo, next((state_dir(repo) / "runs").iterdir()).name)
            size = path.stat().st_size
            path.write_bytes(path.read_bytes()[: size - 10])
            cuts.append((path, size - 10))

    scripted.on_specialist = cut
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    ((path, offset),) = cuts
    events = read_journal(out)
    assert code == 1
    (violation,) = [e for e in events if e["event"] == "invariant-violation"]
    assert (violation["kind"], violation["offset"], violation["size_found"]) == ("journal-changed", offset, offset)
    assert violation["size_written"] == offset + 10
    assert "changed_bytes" not in violation  # the other writer left no bytes of its own
    assert [(e["ticket"], e["state"]) for e in events if e["event"] == "step-end"] == [("a", "built"), ("b", "failed")]
    assert [e["ticket"] for e in events if e["event"] == "skip"] == ["c"]
    assert (events[-1]["event"], events[-1]["result"]) == ("run-end", "failed")
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))



class WritesInTheProbe(ScriptedAdapter):
    """A scripted adapter whose guard probe session also appends a line to the journal of the run."""

    def _run_probe(self, request):
        path = request.sealed.directory.parent.parent / "journal.jsonl"
        self.offsets = getattr(self, "offsets", []) + [path.stat().st_size]
        with path.open("ab") as handle:
            handle.write(b"written by the probe session\n")
        return super()._run_probe(request)


def test_a_change_before_the_first_step_ends_the_run_failed_and_names_the_journal_and_the_offset(tmp_path, capsys):
    repo = make_sealed_repo(tmp_path)
    adapter = WritesInTheProbe(probes_guard=True)
    adapters.register("scripted", adapter)
    try:
        code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    finally:
        adapters.unregister("scripted")

    events = read_journal(out)
    (offset,) = adapter.offsets
    assert code == 1
    assert adapter.calls == [] and adapter.verifier_calls == []
    assert event_names(events) == ["run-start", "invariant-violation", "run-end"]
    violation = events[1]
    assert (violation["kind"], violation["offset"], violation["size_found"]) == ("journal-changed", offset, offset + 29)
    assert Path(violation["changed_bytes"]).read_bytes() == b"written by the probe session\n"
    assert (events[-1]["result"], events[-1]["built"], events[-1]["failed"]) == ("failed", [], [])
    assert f"delegate run: invariant violation: another writer changed the journal {violation['journal']} at byte {offset}" in err
    assert "Traceback" not in err


# The events of the step of one ticket of a run with no other writer, with the fields of each, as the engine wrote them
# before #291, and the field `scratch` of `verify-start` (#294). The first gate result is green on the tip, so the gate after the rebase onto the same tip is skipped.
STEP: list[tuple[str, list[str]]] = [
    ("step-start", ["agent", "base_commit", "branch", "model", "stack", "ticket", "tier", "worktree"]),
    ("agent-start", ["event_stream", "role", "round", "ticket"]),
    ("adapter-result", ["end_state", "event_stream", "exit_status", "last_event", "session_id", "ticket", "tokens_cache", "tokens_new"]),
    ("report-validation", ["blocked_reason", "path", "reason", "status", "ticket", "valid"]),
    ("gate-start", ["command", "gate_log", "phase", "round", "ticket"]),
    ("gate-result", ["clean", "command", "commit", "exit_status", "green", "output_tail", "phase", "round", "ticket", "timed_out"]),
    ("rebase", ["files", "from_commit", "onto_commit", "result", "ticket", "to_commit"]),
    ("gate-skip", ["command", "commit", "green_seq", "phase", "reason", "round", "ticket"]),
    ("verify-start", ["agent", "commit", "copy", "model", "round", "scratch", "snapshot", "ticket", "tier"]),
    ("agent-start", ["event_stream", "role", "round", "ticket"]),
    ("verify-result", ["end_state", "event_stream", "exit_status", "last_event", "round", "session_id", "ticket", "tokens_cache", "tokens_new"]),
    ("verify-report", ["path", "reason", "round", "ticket", "valid"]),
    ("verdict", ["findings", "mode", "report", "round", "ticket", "unverified", "verdict"]),
    ("run-branch-advance", ["from_commit", "run_branch", "ticket", "to_commit"]),
    ("step-end", ["reason", "state", "ticket"]),
]
PLAIN_RUN: list[tuple[str, list[str]]] = [
    ("run-start", ["adapter", "agents", "base_branch", "base_commit", "configuration", "mode", "plan", "run_branch", "run_id", "schema", "tickets", "workflow"]),
    *STEP, *STEP, *STEP,
    ("run-end", ["built", "failed", "result", "skipped"]),
]


def test_a_run_with_no_other_writer_writes_the_journal_as_before(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    events = read_journal(out)
    assert (code, err) == (0, "")
    assert [(e["event"], sorted(set(e) - {"seq", "time", "event"})) for e in events] == PLAIN_RUN
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    # Each skipped gate names the green result of its ticket, which the engine found in the events that it holds.
    for skip in [e for e in events if e["event"] == "gate-skip"]:
        green = events[skip["green_seq"] - 1]
        assert (green["event"], green["ticket"], green["commit"]) == ("gate-result", skip["ticket"], skip["commit"])
    assert events[-1]["built"] == ["a", "b", "c"]


class JournalReads:
    """A spy on the reads of journal files: it counts each open of a journal file for reading while its run holds the lock."""

    def __init__(self, monkeypatch, repo: Path) -> None:
        self.reads = 0
        real = io.open

        def spy(file, mode="r", *args, **kwargs):
            path = Path(file) if isinstance(file, (str, Path)) else None
            if path is not None and path.name == "journal.jsonl" and not set(mode) & set("wax+"):
                if live_holder_of_run(state_dir(repo), path.parent.name) is not None:
                    self.reads += 1
            return real(file, mode, *args, **kwargs)

        monkeypatch.setattr(io, "open", spy)
        monkeypatch.setattr(builtins, "open", spy)


def halt_at_the_first_specialist(request):
    raise AdapterError("claude: not logged in")


def test_a_resume_of_three_tickets_reads_the_journal_one_time_while_it_holds_the_lock(tmp_path, capsys, scripted, monkeypatch):
    repo = make_repo(tmp_path, THREE)
    scripted.on_specialist = halt_at_the_first_specialist
    run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    scripted.on_specialist = None
    spy = JournalReads(monkeypatch, repo)

    code, out, err = resume_main(repo, capsys, run_dir.name)
    reads = spy.reads

    assert code == 0, err
    events = read_journal_file(repo, run_dir.name)
    assert (events[-1]["event"], events[-1]["built"]) == ("run-end", ["a", "b", "c"])
    assert reads == 1


def test_a_new_run_of_three_tickets_reads_its_journal_no_time(tmp_path, capsys, scripted, monkeypatch):
    repo = make_repo(tmp_path, THREE)
    spy = JournalReads(monkeypatch, repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    reads = spy.reads

    assert code == 0, err
    assert read_journal(out)[-1]["built"] == ["a", "b", "c"]
    assert reads == 0


def test_delegate_watch_shows_a_journal_change_as_a_change_of_the_journal_and_not_of_the_worktree(tmp_path, monkeypatch):
    from .support import bare_repo, frames
    from .test_watch_live import Run, ticket_a_is_built, watch

    repo = bare_repo(tmp_path)
    run = Run(repo, "a", "b")
    ticket_a_is_built(run)
    run.step_start(3600, "b")
    run.add(
        3700, "invariant-violation", kind="journal-changed", journal=str(run.path), offset=4096, size_written=4096,
        size_found=4200, changed_bytes=str(run.dir / "journal-changed-20.txt"),
    )
    run.add(3700, "step-end", ticket="b", state="failed", reason="invariant violation: another writer changed the journal")
    run.add(3700, "run-end", result="failed", built=["a"], failed=["b"], skipped=[])

    code, out = watch(repo, monkeypatch)

    (last,) = frames(out)
    lines = [line for line in last if "invariant violation" in line]
    assert any(line.endswith("invariant violation: another writer changed the journal") for line in lines), last
    assert not [line for line in lines if "the verifier changed the worktree" in line]
