"""The `prepare` commands of a stack: the engine runs them in each tree before the agents start (#155).

A tree that git makes holds no ignored file, so a stack that needs installed
dependencies has none in the worktree of a ticket, in the verify copy, or in
the proof copy. The engine runs the `prepare` commands of the stack itself, in
the worktree after it makes it, and in the verify copy before it makes the
proof copy. Each case calls `delegate.main` against a real temporary git
repository, with the scripted adapter in the place of the harness.
"""

import os
import time
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate
from delegate.run.brief import red_proof_copy

from .support import (
    TEMPLATE_GIT, WORKFLOW, ScriptedAdapter, bare_repo, copy_of, event_names, git, read_journal, read_journal_file, resume_main,
    run_main, run_start, step_start, write_journal,
)

# The marker is an ignored file, as `node_modules` is: git does not put it in a tree, and the scripted specialist does not commit it.
MARKER = "prepared.marker"
PREPARE = f"printf prepared > {MARKER}"
# A command that passes in the worktree of a ticket (its `.git` entry is a file) and fails in the verify copy (a clone).
WORKTREE_ONLY = "test -f .git"


def workflow(prepare: str | None, mode: str = "assure", tickets: str = "a") -> str:
    text = WORKFLOW.replace('mode = "assure"', f'mode = "{mode}"')
    if prepare is not None:
        text = text.replace('gates = ["test -f feature.txt"]', f'gates = ["test -f feature.txt"]\nprepare = {prepare}')
    head, ticket = text.split("[[tickets]]")
    blocks = []
    for position, ticket_id in enumerate(tickets):
        blockers = f'["{tickets[position - 1]}"]' if position else "[]"
        blocks.append(
            "[[tickets]]" + ticket.replace('id = "a"', f'id = "{ticket_id}"').replace("blocked-by = []", f"blocked-by = {blockers}")
        )
    return head + "\n".join(blocks)


def make_repo(tmp_path: Path, text: str) -> Path:
    """A real git repository on `main` that holds `workflow.toml` and a `.gitignore` that names the marker."""

    def build(repo: Path) -> None:
        (repo / ".gitignore").write_text(f"{MARKER}\n")
        (repo / "workflow.toml").write_text(text)
        git(repo, *TEMPLATE_GIT, "init", "-q", "-b", "main")
        git(repo, *TEMPLATE_GIT, "add", "-A")
        git(repo, *TEMPLATE_GIT, "commit", "-q", "-m", "seed")

    return copy_of(tmp_path / "repo", ("prepare", text), build)


@pytest.fixture
def seen(git_identity):
    """A scripted adapter that records, for each agent, whether its trees hold the marker."""
    found: dict[str, list[object]] = {"specialist": [], "copy": [], "proof": []}

    def at_specialist(request):
        found["specialist"].append((request.cwd, (request.cwd / MARKER).is_file()))

    def at_verifier(request):
        found["copy"].append((request.cwd / MARKER).is_file())
        found["proof"].append((red_proof_copy(request.cwd) / MARKER).is_file())

    adapter = ScriptedAdapter(on_specialist=at_specialist, during_verifier=at_verifier)
    adapters.register("scripted", adapter)
    yield adapter, found
    adapters.unregister("scripted")


def run(repo: Path, capsys) -> tuple[int, str, str, list[dict[str, object]]]:
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return code, out, err, read_journal(out)


def prepare_events(events: list[dict[str, object]]) -> list[tuple[object, ...]]:
    return [
        (e["event"], e["ticket"], e["tree"], e["command"], e.get("exit_status"))
        for e in events if e["event"] in ("prepare-start", "prepare-result")
    ]


def test_the_marker_of_prepare_is_in_the_worktree_the_verify_copy_and_the_proof_copy_before_the_agents_start(tmp_path, capsys, seen):
    adapter, found = seen
    repo = make_repo(tmp_path, workflow(f'["{PREPARE}"]'))

    code, out, err, events = run(repo, capsys)

    assert (code, err) == (0, "")
    assert [held for _, held in found["specialist"]] == [True]
    assert (found["copy"], found["proof"]) == ([True], [True])
    assert prepare_events(events) == [
        ("prepare-start", "a", "worktree", PREPARE, None),
        ("prepare-result", "a", "worktree", PREPARE, 0),
        ("prepare-start", "a", "verify-copy", PREPARE, None),
        ("prepare-result", "a", "verify-copy", PREPARE, 0),
    ]
    names = event_names(events)
    assert names.index("step-start") < names.index("prepare-start") < names.index("prepare-result") < names.index("agent-start")
    assert max(i for i, name in enumerate(names) if name == "prepare-result") < names.index("verify-start")
    for event in (e for e in events if e["event"] == "prepare-result"):
        assert Path(str(event["prepare_log"])).read_text() == ""  # the command wrote to the marker, not to its output
    paths = [Path(str(e["path"])) for e in events if e["event"] == "prepare-result"]
    assert paths == [found["specialist"][0][0], paths[1]] and paths[1].name == "copy" and not paths[1].exists()
    # The marker is ignored: no commit of the run holds it.
    assert MARKER not in git(repo, "ls-tree", "-r", "--name-only", "run/demo")


def test_the_prepare_commands_run_in_order_and_each_gets_its_own_log(tmp_path, capsys, seen):
    adapter, found = seen
    first, second = "printf one > first.log.txt", f"cat first.log.txt > {MARKER} && echo second ran"
    repo = make_repo(tmp_path, workflow(f'["{first}", "{second}"]'))
    (repo / ".gitignore").write_text(f"{MARKER}\nfirst.log.txt\n")
    git(repo, "commit", "-q", "-am", "ignore the first output")

    code, out, err, events = run(repo, capsys)

    assert (code, err) == (0, "")
    results = [e for e in events if e["event"] == "prepare-result"]
    assert [(e["tree"], e["command"]) for e in results] == [
        ("worktree", first), ("worktree", second), ("verify-copy", first), ("verify-copy", second),
    ]
    logs = [Path(str(e["prepare_log"])) for e in results]
    assert len(set(logs)) == 4
    assert [log.read_text() for log in logs] == ["", "second ran\n", "", "second ran\n"]
    assert found["specialist"][0][1] and found["copy"] == [True] and found["proof"] == [True]


def test_a_failing_prepare_command_ends_the_step_failed_and_no_agent_starts(tmp_path, capsys, seen):
    adapter, found = seen
    repo = make_repo(tmp_path, workflow('["echo installing; exit 3"]'))

    code, out, err, events = run(repo, capsys)

    assert code == 1
    assert "ticket a: prepare failed: echo installing; exit 3" in err
    assert adapter.calls == [] and adapter.verifier_calls == []
    (result,) = [e for e in events if e["event"] == "prepare-result"]
    assert (result["tree"], result["exit_status"]) == ("worktree", 3)
    assert Path(str(result["prepare_log"])).read_text() == "installing\n"
    (end,) = [e for e in events if e["event"] == "step-end"]
    assert (end["state"], end["reason"]) == ("failed", "prepare failed: echo installing; exit 3")
    assert "gate-start" not in event_names(events)


def test_a_prepare_command_that_fails_in_the_verify_copy_ends_the_step_with_no_fix_up_round(tmp_path, capsys, seen):
    adapter, found = seen
    repo = make_repo(tmp_path, workflow(f'["{WORKTREE_ONLY}"]'))

    code, out, err, events = run(repo, capsys)

    assert code == 1
    assert len(adapter.calls) == 1 and adapter.verifier_calls == [] and adapter.fixup_calls == []
    assert [(e["tree"], e["exit_status"]) for e in events if e["event"] == "prepare-result"] == [("worktree", 0), ("verify-copy", 1)]
    names = event_names(events)
    assert "verify-start" not in names and "fixup-start" not in names
    (end,) = [e for e in events if e["event"] == "step-end"]
    assert (end["state"], end["reason"]) == ("failed", f"prepare failed: {WORKTREE_ONLY}")
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", "main").strip()


def test_a_stack_with_no_prepare_field_runs_no_prepare_command(tmp_path, capsys, seen):
    adapter, found = seen
    repo = make_repo(tmp_path, workflow(None))

    code, out, err, events = run(repo, capsys)

    assert (code, err) == (0, "")
    assert prepare_events(events) == []
    assert [held for _, held in found["specialist"]] == [False]
    assert (found["copy"], found["proof"]) == ([False], [False])


def test_in_economy_mode_each_new_worktree_and_the_verify_copy_of_the_chain_are_prepared(tmp_path, capsys, seen):
    adapter, found = seen
    adapter.files_by_ticket = {t: {f"{t}.txt": f"{t}\n", "feature.txt": "feature\n"} for t in "ab"}
    repo = make_repo(tmp_path, workflow(f'["{PREPARE}"]', mode="economy", tickets="ab"))

    code, out, err, events = run(repo, capsys)

    assert (code, err) == (0, "")
    assert [held for _, held in found["specialist"]] == [True, True]
    assert (found["copy"], found["proof"]) == ([True], [True])
    assert [(e["ticket"], e["tree"]) for e in events if e["event"] == "prepare-result"] == [
        ("a", "worktree"), ("b", "worktree"), ("b", "verify-copy"),
    ]


def first_run_with_no_verdict(repo: Path, capsys, adapter: ScriptedAdapter) -> str:
    """Run the workflow; the verifier ends with no verdict, so the step fails after the build. Return the run id."""
    adapter.write_verifier_report = False
    adapter.verifier_end_state, adapter.verifier_exit_status = "failed", -9
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    adapter.write_verifier_report = True
    adapter.verifier_end_state, adapter.verifier_exit_status = "finished", 0
    return next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]


def test_a_retry_that_makes_the_worktree_again_runs_prepare_in_the_new_worktree(tmp_path, capsys, seen):
    adapter, found = seen
    repo = make_repo(tmp_path, workflow(f'["{PREPARE}"]'))
    run_id = first_run_with_no_verdict(repo, capsys, adapter)
    worktree = found["specialist"][0][0]
    git(repo, "worktree", "remove", "--force", str(worktree))
    found["copy"].clear()
    found["proof"].clear()

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    retried = events[event_names(events).index("retry"):]
    assert [(e["tree"], e["exit_status"]) for e in retried if e["event"] == "prepare-result"] == [("worktree", 0), ("verify-copy", 0)]
    assert (worktree / MARKER).is_file()
    assert (found["copy"], found["proof"]) == ([True], [True])


def test_a_retry_in_the_worktree_that_prepare_made_runs_prepare_only_in_the_verify_copy(tmp_path, capsys, seen):
    adapter, found = seen
    repo = make_repo(tmp_path, workflow(f'["{PREPARE}"]'))
    run_id = first_run_with_no_verdict(repo, capsys, adapter)

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    events = read_journal_file(repo, run_id)
    retried = events[event_names(events).index("retry"):]
    assert [e["tree"] for e in retried if e["event"] == "prepare-result"] == ["verify-copy"]


def dry_run(tmp_path: Path, capsys, text: str) -> tuple[int, str, str]:
    """`delegate run --dry-run` on `text` with a built-in adapter, which the check knows without a registration."""
    repo = make_repo(tmp_path, text.replace('adapter = "scripted"', 'adapter = "claude-code"'))
    return run_main(capsys, str(repo / "workflow.toml"), "--dry-run")


def test_the_dry_run_prints_the_prepare_commands_of_each_stack(tmp_path, capsys):
    code, out, err = dry_run(tmp_path, capsys, workflow('["npm ci", "make deps"]'))

    assert (code, err) == (0, "")
    assert "stack python prepare: npm ci\nstack python prepare: make deps\n" in out


def test_the_dry_run_prints_no_prepare_line_for_a_stack_with_no_prepare_field(tmp_path, capsys):
    code, out, err = dry_run(tmp_path, capsys, workflow(None))

    assert (code, err) == (0, "")
    assert "prepare" not in out


@pytest.mark.parametrize(
    ("value", "message"),
    [
        pytest.param('"npm ci"', "stacks.python.prepare: must be a list of strings", id="not-a-list"),
        pytest.param('["npm ci", 3]', "stacks.python.prepare: must be a list of strings", id="not-a-string"),
        pytest.param('["  "]', "stacks.python.prepare: must be a list of strings", id="blank-command"),
        pytest.param("[]", "stacks.python.prepare: needs at least one entry", id="empty"),
    ],
)
def test_the_validator_checks_prepare_as_it_checks_gates(tmp_path, capsys, value, message):
    code, out, err = dry_run(tmp_path, capsys, workflow(value))

    assert (code, out) == (1, "")
    assert message in err


def test_an_economy_retry_after_a_failed_prepare_runs_prepare_again_in_the_same_worktree(tmp_path, capsys, seen):
    adapter, found = seen
    flag = tmp_path / "registry-up"
    repo = make_repo(tmp_path, workflow(f'["test -f {flag}", "{PREPARE}"]', mode="economy"))
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1 and adapter.calls == []
    run_id = next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]
    flag.touch()

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")

    assert (code, err) == (0, "")
    assert [held for _, held in found["specialist"]] == [True]
    events = read_journal_file(repo, run_id)
    retried = events[event_names(events).index("retry"):]
    assert [(e["tree"], e["exit_status"]) for e in retried if e["event"] == "prepare-result"] == [
        ("worktree", 0), ("worktree", 0), ("verify-copy", 0), ("verify-copy", 0),
    ]


@pytest.mark.parametrize(("log_minutes", "code"), [pytest.param(1, 6, id="the-log-changes"), pytest.param(10, 5, id="the-log-is-old")])
def test_a_prepare_command_that_writes_to_its_log_is_not_a_stall_for_watch(tmp_path, capsys, log_minutes, code):
    # A long install writes to its log, and the journal gets no new event until the command ends.
    repo = bare_repo(tmp_path)
    log = tmp_path / "prepare-worktree-0.log"
    log.write_text("added 812 packages\n")
    start = (
        "prepare-start",
        {"ticket": "a", "round": 0, "tree": "worktree", "path": "/w/a", "command": "npm ci", "prepare_log": str(log)},
    )
    journal = write_journal(repo, "r1", run_start("r1", "a"), step_start("a"), start)
    for path, minutes in ((journal, 10), (log, log_minutes)):
        then = time.time() - minutes * 60
        os.utime(path, (then, then))

    exit_code = delegate.main(["watch", "r1", "--repo", str(repo), "--poll-seconds", "0.02", "--stall-minutes", "5", "--max-minutes", "0.01"])

    out = capsys.readouterr().out
    assert exit_code == code
    assert ("watch: stall" in out) == (code == 5)
