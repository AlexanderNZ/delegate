"""`delegate run --resume RUN_ID --retry TICKET` with a new text for the failed ticket (#202).

The built tickets and the ticket that runs stay fixed. The text of a failed ticket that the retry starts again can
change, and only its text: the stack and the `blocked-by` stay fixed. The journal records the old and the new
`text_sha256` in `plan-change`, and the briefs of the retried ticket hold the new text.

Each case calls `delegate.main` against a real temporary git repository, with the scripted adapter in the place of
the harness. A verifier that ends with no verdict fails the step of ticket `b`, and leaves its commits for the retry.
"""

import hashlib

import pytest

from delegate import adapters

from .support import ScriptedAdapter, git, make_repo, read_journal_file, resume_main, run_main, state_dir, ticket_of
from .test_run_multi import HEAD, ticket

RULING = "RULING-5202 add no new dependency."
OLD_B = "Ticket b adds its file."
NEW_B = f"Ticket b adds its file. {RULING}"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def workflow_of(b_text: str = OLD_B, b_blocked_by: tuple[str, ...] = (), a_text: str = "Ticket a adds its file.") -> str:
    a = ticket("a").replace("Ticket a adds its file.", a_text)
    b = ticket("b", b_blocked_by).replace(OLD_B, b_text)
    return HEAD + a + b + ticket("c", ("b",))


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abc"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def failed_run(tmp_path, capsys, scripted):
    """A run whose ticket a is built, whose ticket b failed at its verifier, and whose ticket c was skipped. Return the repo and the run id."""

    def time_limit(request):
        killed = ticket_of(request) == "b"
        scripted.write_verifier_report = not killed
        scripted.verifier_end_state, scripted.verifier_exit_status = ("failed", -9) if killed else ("finished", 0)

    scripted.during_verifier = time_limit
    repo = make_repo(tmp_path, workflow_of())
    code, _, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == 1
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    scripted.during_verifier = None
    scripted.write_verifier_report = True
    scripted.verifier_end_state, scripted.verifier_exit_status = "finished", 0
    for calls in (scripted.calls, scripted.continuation_calls, scripted.fixup_calls, scripted.verifier_calls):
        calls.clear()
    return repo, run_dir.name


def write(repo, text: str) -> None:
    (repo / "workflow.toml").write_text(text)


def test_a_retry_with_a_new_text_for_the_failed_ticket_starts_it_again_with_the_new_text_and_the_journal_holds_both_hashes(
    tmp_path, capsys, scripted
):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    write(repo, workflow_of(NEW_B))

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")

    assert (code, err) == (0, "")
    (verifier,) = [call for call in scripted.verifier_calls if ticket_of(call) == "b"]
    assert RULING in verifier.prompt
    events = read_journal_file(repo, run_id)
    (change,) = [e for e in events if e["event"] == "plan-change"]
    assert (change["changed"], change["fields"]) == (["b"], {"b": ["text"]})
    assert change["retry_text"] == {"b": {"old": sha(OLD_B), "new": sha(NEW_B)}}
    assert {t["id"]: t["text_sha256"] for t in change["plan"]}["b"] == sha(NEW_B)
    ends = [(e["ticket"], e["state"]) for e in events if e["event"] == "step-end"]
    assert ("b", "built") in ends and ("c", "built") in ends
    assert "b.txt" in git(repo, "ls-tree", "-r", "--name-only", "run/demo")


def test_the_same_new_text_without_retry_is_refused_because_the_ticket_has_started(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    write(repo, workflow_of(NEW_B))
    before = (state_dir(repo) / "runs" / run_id / "journal.jsonl").read_bytes()

    code, out, err = resume_main(repo, capsys, run_id, "--reviewed", "c")

    assert code == 1 and "'b'" in err and "has started" in err and "text" in err
    assert (state_dir(repo) / "runs" / run_id / "journal.jsonl").read_bytes() == before
    assert scripted.calls == [] and scripted.verifier_calls == []


def test_a_retry_of_one_ticket_does_not_let_another_ticket_change_its_text(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    write(repo, workflow_of(NEW_B, a_text="Ticket a adds its file. Another text."))

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")

    assert code == 1 and "'a'" in err and "text" in err and "'b'" not in err
    assert scripted.verifier_calls == []


def test_a_new_text_for_a_built_ticket_is_still_refused_with_a_retry_of_another_ticket(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    write(repo, workflow_of(a_text="Ticket a adds its file. NEW-RULING-5202."))

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "a", "--reviewed", "c")

    assert code == 1 and "'a'" in err and "text" in err
    assert scripted.calls == [] and scripted.verifier_calls == []


def test_a_retry_cannot_change_the_blocked_by_of_the_failed_ticket(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    write(repo, workflow_of(NEW_B, b_blocked_by=("a",)))

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c")

    assert code == 1 and "'b'" in err and "blocked-by" in err and "has started" in err
    assert scripted.verifier_calls == []


def test_the_dry_run_of_a_retry_with_a_new_text_prints_the_verdict_of_the_new_rule(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    write(repo, workflow_of(NEW_B))

    code, out, err = resume_main(repo, capsys, run_id, "--retry", "b", "--reviewed", "c", "--dry-run")

    assert (code, err) == (0, "")
    (rule,) = [line for line in out.splitlines() if line.startswith("rule 10: ")]
    assert rule.startswith("rule 10: ok: a failed ticket that a retry starts again can change its text: ") and "b" in rule
    assert not any(line.startswith("rule 1: refused") for line in out.splitlines())
    assert scripted.verifier_calls == []


def test_the_dry_run_without_retry_refuses_the_new_text_of_the_failed_ticket(tmp_path, capsys, scripted):
    repo, run_id = failed_run(tmp_path, capsys, scripted)
    write(repo, workflow_of(NEW_B))

    code, out, err = resume_main(repo, capsys, run_id, "--reviewed", "c", "--dry-run")

    assert code == 1
    (rule_1,) = [line for line in out.splitlines() if line.startswith("rule 1: ")]
    assert rule_1.startswith("rule 1: refused") and "'b'" in rule_1
    assert [line for line in out.splitlines() if line.startswith("rule 10: ")][0].startswith("rule 10: ok")
