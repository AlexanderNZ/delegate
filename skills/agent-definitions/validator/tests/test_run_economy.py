"""`delegate run --mode economy` builds a chain and verifies once for each stack at the end of it.

Each case writes a workflow into a real temporary git repository, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter plays
the specialists and the verifiers, so the engine runs end to end with no model.
The expected values come from the ticket's acceptance criteria.
"""

import pytest

from agent_definitions import adapters, delegate

from .support import ScriptedAdapter, WORKFLOW, event_names, git, make_repo, read_journal

ECONOMY = WORKFLOW.replace('mode = "assure"', 'mode = "economy"')

THREE_TICKETS = ECONOMY + '''
[[tickets]]
id = "b"
text = "The export command writes a header row."
stack = "python"
blocked-by = ["a"]

[[tickets]]
id = "c"
text = "The export command quotes a comma."
stack = "python"
blocked-by = []
'''


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter(files_by_ticket={"a": {"feature.txt": "a\n"}, "b": {"b.txt": "b\n"}, "c": {"c.txt": "c\n"}})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo, capsys, *extra):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def tip(repo, ref):
    return git(repo, "rev-parse", ref).strip()


def test_each_branch_starts_from_the_tip_of_the_previous_ticket(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, THREE_TICKETS)
    main_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert tip(repo, "run/demo-a~1") == main_tip
    assert tip(repo, "run/demo-b~1") == tip(repo, "run/demo-a")
    assert tip(repo, "run/demo-c~1") == tip(repo, "run/demo-b")
    # The tickets b and c write no feature.txt. Their gate passes only because the branch holds the work of ticket a.
    assert git(repo, "ls-tree", "-r", "--name-only", "run/demo-c").split() == ["b.txt", "c.txt", "feature.txt", "seed.txt", "workflow.toml"]
    bases = {e["ticket"]: e["base_commit"] for e in read_journal(out) if e["event"] == "step-start"}
    assert bases == {"a": main_tip, "b": tip(repo, "run/demo-a"), "c": tip(repo, "run/demo-b")}


def test_a_ticket_that_failed_is_not_part_of_the_chain(tmp_path, capsys, scripted):
    scripted.failing_tickets = {"a"}
    scripted.files_by_ticket["b"] = {"feature.txt": "b\n"}  # the gate needs feature.txt, and ticket a does not make it
    scripted.files_by_ticket["c"] = {"c.txt": "c\n"}
    repo = make_repo(tmp_path, THREE_TICKETS.replace('blocked-by = ["a"]', 'blocked-by = []'))
    main_tip = tip(repo, "main")

    code, out, err = run(repo, capsys)

    assert code == 1
    bases = {e["ticket"]: e["base_commit"] for e in read_journal(out) if e["event"] == "step-start"}
    assert bases["a"] == main_tip
    assert bases["b"] == main_tip  # b follows the failed ticket a in the plan, and starts from the base
    assert bases["c"] == tip(repo, "run/demo-b")
