"""The agent check of a run: the agents of a run are the agents that it started with, or the journal records the change (#205).

An agent file that git ignores is in no diff, so no hotspot sees a change of it. The workflow here ignores `.claude/`, and
its `prepare` command copies the agent files from the main checkout into each tree, as a real stack does. The engine records
the SHA-256 of each agent file at `run-start`. Before each agent session, after `prepare`, it hashes the same agent file in
the tree of the agent. A difference fails the step before the agent starts. Each case calls `delegate.main` against a real
temporary git repository, with the scripted adapter in the place of the harness.
"""

import hashlib
import json
from pathlib import Path

import pytest

from delegate import adapters

from .support import (
    WORKFLOW, ScriptedAdapter, event_names, git, journal_path, read_journal, read_journal_file, resume_main, run_main,
)

pytestmark = pytest.mark.usefixtures("git_identity")

AGENTS = (".claude", "agents")
NAMES = ("python-specialist", "python-verifier", "web-specialist", "web-verifier")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def agent_path(root: Path, name: str) -> Path:
    return root.joinpath(*AGENTS, f"{name}.md")


SECOND_TICKET = """
[[tickets]]
id = "b"
text = "The export command writes a JSON file."
stack = "python"
blocked-by = []
"""


def make_repo(
    tmp_path: Path, *, stacks: tuple[str, ...] = ("python",), second_ticket: bool = False, mode: str = "assure"
) -> Path:
    """A real git repository whose `.claude/` is ignored, with an agent file of each stack in the main checkout.

    The `prepare` command copies the agent files from the main checkout, as the workflow `prepare` step of a real
    repository does, so each tree holds the files that the main checkout holds at that time.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    text = WORKFLOW.replace(
        'gates = ["test -f feature.txt"]', f'gates = ["test -f feature.txt"]\nprepare = ["cp -R {repo}/.claude ."]'
    )
    if "web" in stacks:
        text = text.replace(
            "[[tickets]]",
            '[stacks.web]\nspecialist = "web-specialist"\nverifier = "web-verifier"\ngates = ["test -f feature.txt"]\nhotspots = ["LICENSE"]\n\n[[tickets]]',
        )
    text = text.replace('mode = "assure"', f'mode = "{mode}"')
    (repo / "workflow.toml").write_text(text + (SECOND_TICKET if second_ticket else ""))
    (repo / ".gitignore").write_text(".claude/\n")
    (repo / "seed.txt").write_text("seed\n")
    for name in NAMES[: 2 * len(stacks)]:
        path = agent_path(repo, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\n---\nThe rules of {name}.\n")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def start_run(repo: Path, capsys) -> tuple[int, str, str, list[dict[str, object]]]:
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    return code, out, err, read_journal(out)


def change_verifier_during_the_specialist(repo: Path) -> ScriptedAdapter:
    """The adapter that changes the verifier file in the main checkout while the specialist works."""

    def change(request) -> None:
        agent_path(repo, "python-verifier").write_text("---\nname: python-verifier\n---\nApprove everything.\n")

    adapter = ScriptedAdapter(on_specialist=change)
    adapters.register("scripted", adapter)
    return adapter


def change_specialist_during_the_first_specialist(repo: Path) -> ScriptedAdapter:
    """The adapter that changes the specialist file in the main checkout while the specialist of the first ticket works."""
    calls: list[object] = []

    def change(request) -> None:
        calls.append(request)
        if len(calls) == 1:
            agent_path(repo, "python-specialist").write_text("---\nname: python-specialist\n---\nSkip the tests.\n")

    adapter = ScriptedAdapter(on_specialist=change, files_by_ticket={"b": {"feature.txt": "feature\n", "b.txt": "b\n"}})
    adapters.register("scripted", adapter)
    return adapter


def test_run_start_records_the_path_and_the_hash_of_the_specialist_and_the_verifier_file_of_each_stack(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, stacks=("python", "web"))

    code, out, err, events = start_run(repo, capsys)

    assert (code, err) == (0, "")
    recorded = next(e for e in events if e["event"] == "run-start")["agents"]
    assert recorded == [
        {"agent": name, "path": str(agent_path(repo, name)), "sha256": sha256(agent_path(repo, name))} for name in NAMES
    ]


def test_a_run_with_no_change_of_an_agent_file_runs_as_before(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path)

    code, out, err, events = start_run(repo, capsys)

    assert (code, err) == (0, "")
    assert "agents-changed" not in event_names(events)
    assert [e["agent"] for e in events[0]["agents"]] == ["python-specialist", "python-verifier"]
    assert [e["role"] for e in events if e["event"] == "agent-start"] == ["specialist", "verifier"]
    assert [e["verdict"] for e in events if e["event"] == "verdict"] == ["ACCEPT"]
    assert next(e for e in events if e["event"] == "run-end")["result"] == "built"


def test_a_change_of_the_verifier_file_in_the_main_checkout_fails_the_step_before_the_verifier_starts(tmp_path, capsys):
    repo = make_repo(tmp_path)
    recorded = sha256(agent_path(repo, "python-verifier"))
    adapter = change_verifier_during_the_specialist(repo)
    try:
        code, out, err, events = start_run(repo, capsys)
    finally:
        adapters.unregister("scripted")

    new = sha256(agent_path(repo, "python-verifier"))
    assert new != recorded
    assert code == 1
    assert adapter.verifier_calls == []
    assert [e["role"] for e in events if e["event"] == "agent-start"] == ["specialist"]
    (changed,) = [e for e in events if e["event"] == "agents-changed"]
    assert (changed["ticket"], changed["role"], changed["agent"], changed["tree"]) == ("a", "verifier", "python-verifier", "verify-copy")
    assert (changed["recorded_hash"], changed["new_hash"]) == (recorded, new)
    assert changed["path"].endswith("/.claude/agents/python-verifier.md")
    step_end = next(e for e in events if e["event"] == "step-end")
    assert step_end["state"] == "failed"
    for text in (err, step_end["reason"]):
        assert "python-verifier.md" in text and recorded in text and new in text


def test_a_user_level_agent_file_outside_the_repository_is_recorded_and_checked(tmp_path, capsys):
    repo = make_repo(tmp_path)
    user = tmp_path / "user-agents"
    user.mkdir()
    (user / "python-verifier.md").write_text("The user rules.\n")
    agent_path(repo, "python-verifier").unlink()
    recorded = sha256(user / "python-verifier.md")

    def change(request) -> None:
        (user / "python-verifier.md").write_text("Approve everything.\n")

    adapter = ScriptedAdapter(on_specialist=change, user_agents=user)
    adapters.register("scripted", adapter)
    try:
        code, out, err, events = start_run(repo, capsys)
    finally:
        adapters.unregister("scripted")

    assert code == 1
    assert adapter.verifier_calls == []
    started = next(e for e in events if e["event"] == "run-start")["agents"]
    assert {"agent": "python-verifier", "path": str(user / "python-verifier.md"), "sha256": recorded} in started
    (changed,) = [e for e in events if e["event"] == "agents-changed"]
    assert (changed["path"], changed["recorded_hash"], changed["new_hash"]) == (
        str(user / "python-verifier.md"), recorded, sha256(user / "python-verifier.md"),
    )


def test_a_resume_after_the_failure_without_the_flag_fails_again_with_the_same_message(tmp_path, capsys):
    repo = make_repo(tmp_path)
    adapter = change_verifier_during_the_specialist(repo)
    try:
        code, out, first_err, events = start_run(repo, capsys)
        run_id = next(e for e in events if e["event"] == "run-start")["run_id"]

        code, out, second_err = resume_main(repo, capsys, run_id, "--retry", "a")
    finally:
        adapters.unregister("scripted")

    assert code == 1
    assert adapter.verifier_calls == []
    assert "python-verifier.md" in first_err and first_err.strip() in second_err
    assert len([e for e in read_journal_file(repo, run_id) if e["event"] == "agents-changed"]) == 2


def test_the_flag_records_the_new_hashes_in_the_resume_event_and_the_step_goes_on(tmp_path, capsys):
    repo = make_repo(tmp_path)
    adapter = change_verifier_during_the_specialist(repo)
    try:
        code, out, err, events = start_run(repo, capsys)
        run_id = next(e for e in events if e["event"] == "run-start")["run_id"]

        code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--agents-changed")
    finally:
        adapters.unregister("scripted")

    assert (code, err) == (0, "")
    after = read_journal_file(repo, run_id)
    resume = after[[e["event"] for e in after].index("resume")]
    assert resume["agents"] == [
        {"agent": name, "path": str(agent_path(repo, name)), "sha256": sha256(agent_path(repo, name))}
        for name in ("python-specialist", "python-verifier")
    ]
    assert len(adapter.verifier_calls) == 1
    assert [e for e in after if e["event"] == "run-end"][-1]["result"] == "built"
    # The change is now the record: the hash of the verifier file is the one of the new text.
    assert [e["verdict"] for e in after if e["event"] == "verdict"] == ["ACCEPT"]


def test_the_flag_without_a_resume_is_refused(tmp_path, capsys):
    repo = make_repo(tmp_path)

    with pytest.raises(SystemExit) as stop:
        run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--agents-changed")

    assert stop.value.code == 2
    assert "--agents-changed needs --resume" in capsys.readouterr().err


def test_a_resume_of_a_journal_with_no_agents_field_records_the_hashes_at_the_resume_and_says_so(tmp_path, capsys):
    repo = make_repo(tmp_path)
    adapter = change_verifier_during_the_specialist(repo)
    try:
        code, out, err, events = start_run(repo, capsys)
        run_id = next(e for e in events if e["event"] == "run-start")["run_id"]
        # A journal of an earlier version: `run-start` has no `agents` field.
        journal = journal_path(repo, run_id)
        lines = [json.loads(line) for line in journal.read_text().splitlines()]
        del lines[0]["agents"]
        journal.write_text("".join(json.dumps(line) + "\n" for line in lines))

        code, out, err = resume_main(repo, capsys, run_id, "--retry", "a")
    finally:
        adapters.unregister("scripted")

    assert code == 0
    assert "no record of its agent files" in err and "earlier version" in err
    resume = next(e for e in read_journal_file(repo, run_id) if e["event"] == "resume")
    assert {entry["agent"]: entry["sha256"] for entry in resume["agents"]}["python-verifier"] == sha256(agent_path(repo, "python-verifier"))
    assert len(adapter.verifier_calls) == 1


def test_a_change_of_the_specialist_file_stops_the_next_step_before_its_specialist_and_the_message_names_the_retry(tmp_path, capsys):
    repo = make_repo(tmp_path, second_ticket=True)
    recorded = sha256(agent_path(repo, "python-specialist"))
    change_specialist_during_the_first_specialist(repo)
    try:
        code, out, err, events = start_run(repo, capsys)
    finally:
        adapters.unregister("scripted")

    new = sha256(agent_path(repo, "python-specialist"))
    assert code == 1
    assert [e["ticket"] for e in events if e["event"] == "agent-start" and e["role"] == "specialist"] == ["a"]
    (changed,) = [e for e in events if e["event"] == "agents-changed"]
    assert (changed["ticket"], changed["role"], changed["tree"]) == ("b", "specialist", "worktree")
    assert (changed["recorded_hash"], changed["new_hash"]) == (recorded, new)
    assert "delegate run --resume" in err and "--retry b --agents-changed" in err


def test_a_retry_with_the_flag_goes_on_at_the_specialist_after_a_change_of_the_specialist_file(tmp_path, capsys):
    repo = make_repo(tmp_path, second_ticket=True)
    adapter = change_specialist_during_the_first_specialist(repo)
    try:
        code, out, err, events = start_run(repo, capsys)
        run_id = next(e for e in events if e["event"] == "run-start")["run_id"]

        code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--agents-changed")
    finally:
        adapters.unregister("scripted")

    assert err == "" and code == 0
    after = read_journal_file(repo, run_id)
    resume = after[[e["event"] for e in after].index("resume")]
    assert {e["agent"]: e["sha256"] for e in resume["agents"]}["python-specialist"] == sha256(agent_path(repo, "python-specialist"))
    (retry,) = [e for e in after if e["event"] == "retry"]
    assert (retry["ticket"], retry["phase"]) == ("b", "specialist")
    assert [e["ticket"] for e in after if e["event"] == "agent-start" and e["role"] == "specialist"] == ["a", "b"]
    assert len(adapter.verifier_calls) == 2
    assert [e["state"] for e in after if e["event"] == "step-end" and e["ticket"] == "b"] == ["failed", "built"]
    assert [e for e in after if e["event"] == "run-end"][-1]["result"] == "built"


def test_a_retry_without_the_flag_fails_again_with_the_same_message_after_a_change_of_the_specialist_file(tmp_path, capsys):
    repo = make_repo(tmp_path, second_ticket=True)
    change_specialist_during_the_first_specialist(repo)
    try:
        code, out, first_err, events = start_run(repo, capsys)
        run_id = next(e for e in events if e["event"] == "run-start")["run_id"]

        code, out, second_err = resume_main(repo, capsys, run_id, "--retry", "b")
    finally:
        adapters.unregister("scripted")

    assert code == 1
    after = read_journal_file(repo, run_id)
    assert [e["ticket"] for e in after if e["event"] == "agent-start" and e["role"] == "specialist"] == ["a"]
    assert first_err.strip() in second_err and "cannot be retried" not in second_err
    assert len([e for e in after if e["event"] == "agents-changed"]) == 2


FINDING = "[a] The CSV file is empty."
NEW_TEXT = "---\nname: python-specialist\n---\nSkip the tests.\n"


def change_the_specialist_file_after_the_reject(repo: Path) -> ScriptedAdapter:
    """The adapter whose first verifier rejects, and a change of the specialist file while it works.

    The new text goes into the main checkout and into each worktree that a specialist worked in, so the person's change
    reaches the tree of the fix-up, and the file there is not the file of the record.
    """
    adapter = ScriptedAdapter(
        verdict_sequence=["REJECT", "ACCEPT"], verdict_findings=[FINDING],
        files_by_ticket={"b": {"feature.txt": "feature\n", "b.txt": "b\n"}},
    )

    def change(request) -> None:
        if len(adapter.verifier_calls) == 1:
            for target in [agent_path(repo, "python-specialist"), *(agent_path(call.cwd, "python-specialist") for call in adapter.calls)]:
                target.write_text(NEW_TEXT)

    adapter.during_verifier = change
    adapters.register("scripted", adapter)
    return adapter


MODES = pytest.mark.parametrize(
    "mode, second_ticket", [pytest.param("assure", False, id="assure"), pytest.param("economy", True, id="economy-chain")]
)


@MODES
def test_a_change_of_the_specialist_file_after_a_reject_stops_the_step_before_the_fixup_session(tmp_path, capsys, mode, second_ticket):
    repo = make_repo(tmp_path, mode=mode, second_ticket=second_ticket)
    ticket = "b" if second_ticket else "a"
    adapter = change_the_specialist_file_after_the_reject(repo)
    try:
        code, out, err, events = start_run(repo, capsys)
    finally:
        adapters.unregister("scripted")

    assert code == 1
    assert adapter.fixup_calls == []
    (changed,) = [e for e in events if e["event"] == "agents-changed"]
    assert (changed["ticket"], changed["role"], changed["round"], changed["tree"]) == (ticket, "fix-up", 1, "worktree")
    assert f"--retry {ticket} --agents-changed" in err


@MODES
def test_a_retry_with_the_flag_runs_the_fixup_of_the_same_round_and_the_step_goes_on_to_the_scoped_verifier(
    tmp_path, capsys, mode, second_ticket
):
    repo = make_repo(tmp_path, mode=mode, second_ticket=second_ticket)
    ticket = "b" if second_ticket else "a"
    adapter = change_the_specialist_file_after_the_reject(repo)
    try:
        code, out, err, events = start_run(repo, capsys)
        run_id = next(e for e in events if e["event"] == "run-start")["run_id"]
        rejected = next(e["commit"] for e in events if e["event"] == "verify-start")

        code, out, err = resume_main(repo, capsys, run_id, "--retry", ticket, "--agents-changed")
    finally:
        adapters.unregister("scripted")

    assert (code, err) == (0, "")
    after = read_journal_file(repo, run_id)
    resume = next(e for e in after if e["event"] == "resume")
    assert {e["agent"]: e["sha256"] for e in resume["agents"]}["python-specialist"] == sha256(agent_path(repo, "python-specialist"))
    # The fix-up of round 1 runs again, on the rejected commit, with the findings of the REJECT: one fix-up round in all.
    (start,) = [e for e in after if e["event"] == "fixup-start"]
    assert (start["round"], start["rejected_commit"]) == (1, rejected)
    (fixup,) = adapter.fixup_calls
    assert FINDING in fixup.prompt and fixup.cwd.name == ticket
    assert [e["round"] for e in after if e["event"] == "fixup-report"] == [1]
    # The gates and the scoped verifier of that round follow, and the verifier accepts.
    assert len(adapter.verifier_calls) == 2
    assert adapter.verifier_calls[1].prompt.startswith("## Findings under verification") and FINDING in adapter.verifier_calls[1].prompt
    assert [e["verdict"] for e in after if e["event"] == "verdict"] == ["REJECT", "ACCEPT"]
    assert [e["state"] for e in after if e["event"] == "step-end" and e["ticket"] == ticket][-1] == "built"
    assert [e["result"] for e in after if e["event"] == "run-end"][-1] == "built"
    assert git(repo, "rev-parse", "run/demo").strip() == git(repo, "rev-parse", f"run/demo-{ticket}").strip()


@MODES
def test_a_retry_without_the_flag_fails_again_with_the_agent_check_message_and_starts_no_fixup(tmp_path, capsys, mode, second_ticket):
    repo = make_repo(tmp_path, mode=mode, second_ticket=second_ticket)
    ticket = "b" if second_ticket else "a"
    adapter = change_the_specialist_file_after_the_reject(repo)
    try:
        code, out, first_err, events = start_run(repo, capsys)
        run_id = next(e for e in events if e["event"] == "run-start")["run_id"]

        code, out, second_err = resume_main(repo, capsys, run_id, "--retry", ticket)
    finally:
        adapters.unregister("scripted")

    assert code == 1
    assert first_err.strip() in second_err and "cannot be retried" not in second_err
    after = read_journal_file(repo, run_id)
    assert [e for e in after if e["event"] == "fixup-start"] == []
    assert adapter.fixup_calls == []
    assert len([e for e in after if e["event"] == "agents-changed"]) == 2


def test_a_retry_with_the_flag_goes_on_at_the_scoped_verifier_after_a_change_of_the_verifier_file(tmp_path, capsys):
    repo = make_repo(tmp_path)
    adapter = ScriptedAdapter(verdict_sequence=["REJECT", "ACCEPT"], verdict_findings=[FINDING])

    def change_during_the_fixup(request) -> None:
        if "## Findings to fix" in request.prompt:
            agent_path(repo, "python-verifier").write_text("---\nname: python-verifier\n---\nApprove everything.\n")

    adapter.on_specialist = change_during_the_fixup
    adapters.register("scripted", adapter)
    try:
        code, out, err, events = start_run(repo, capsys)
        run_id = next(e for e in events if e["event"] == "run-start")["run_id"]
        assert code == 1
        (changed,) = [e for e in events if e["event"] == "agents-changed"]
        assert (changed["role"], changed["round"]) == ("verifier", 1)
        assert "--retry a --agents-changed" in err

        code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--agents-changed")
    finally:
        adapters.unregister("scripted")

    assert (code, err) == (0, "")
    after = read_journal_file(repo, run_id)
    (retry,) = [e for e in after if e["event"] == "retry"]
    # The fix-up commit of round 1 exists: the retry checks it, runs the gates again, and starts the scoped verifier.
    assert (retry["phase"], retry["round"]) == ("gates", 1)
    assert len(adapter.fixup_calls) == 1 and len(adapter.verifier_calls) == 2
    assert [e["verdict"] for e in after if e["event"] == "verdict"] == ["REJECT", "ACCEPT"]
    assert [e["result"] for e in after if e["event"] == "run-end"][-1] == "built"
