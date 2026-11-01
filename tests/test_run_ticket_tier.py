"""A ticket of a workflow can set the tier of its specialist (#231).

`tier = "<tier name>"` on a ticket sets the tier of the specialist of that ticket, of its continuations and of its
fix-ups. A ticket with no `tier` uses the tier of the role. The verifier tier does not change. In `economy` mode, the
fix-up of the chain verification uses the strongest specialist tier of the tickets that the verification covers.

Each case runs `delegate.main` against a real temporary git repository with the scripted adapter, and a tier table
whose model names tell the tiers apart.
"""

from pathlib import Path

import pytest

from delegate import adapters

from .support import ScriptedAdapter, make_repo, read_journal, read_journal_file, run_main, state_dir, ticket_of
from .test_run_multi import HEAD

ECONOMY_HEAD = HEAD.replace('mode = "assure"', 'mode = "economy"')

# The end of the message for a value that is not a tier name: the tier names of the table of `tier_table`.
KNOWN = "known tiers: cheap, standard, strong, verifier"


def ticket(ticket_id: str, blocked_by: tuple[str, ...] = (), tier: str | None = None) -> str:
    blockers = ", ".join(f'"{b}"' for b in blocked_by)
    line = "" if tier is None else f"tier = {tier}\n"
    return f'\n[[tickets]]\nid = "{ticket_id}"\ntext = "Ticket {ticket_id} adds its file."\nstack = "python"\nblocked-by = [{blockers}]\n{line}'


def tier_table(tmp_path: Path, extra: str = "") -> Path:
    """A tier table whose model names tell the tiers apart: the model of the tier `strong` is `strong-model`."""
    lines = ['[effort]\nlevels = ["low"]\n\n[effort.opencode]\nvariants = ["high"]\n']
    lines += [f'[tiers.{tier}]\nclaude-code = "{tier}-model"\n' for tier in ("strong", "standard", "cheap", "verifier")]
    lines.append(extra)
    lines.append('[allowed-models]\nclaude-code = []\n\n[budget]\nclaude-code-description-chars = 1\n')
    path = tmp_path / "tiers.toml"
    path.write_text("\n".join(lines))
    return path


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abc"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo: Path, capsys, tiers: Path, *extra: str) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--tiers", str(tiers), *extra)


def models_by_ticket(calls) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for call in calls:
        found.setdefault(ticket_of(call), []).append(call.model)
    return found


def test_a_ticket_tier_sets_the_model_of_its_specialist_continuation_and_fixup_and_the_other_tickets_keep_the_role_tier(
    tmp_path, capsys, scripted
):
    head = HEAD.replace("[stacks.python]", '[tier-overrides]\nspecialist = "cheap"\n\n[stacks.python]')
    repo = make_repo(tmp_path, head + ticket("a", tier='"strong"') + ticket("b", ("a",)))
    scripted.outcomes = [("failed", 1), ("finished", 0)]  # the first session of `a` fails, so `a` continues
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]  # the first verifier of `a` rejects, so `a` gets a fix-up

    code, out, err = run(repo, capsys, tier_table(tmp_path))

    assert (code, err) == (0, ""), err
    assert models_by_ticket(scripted.calls) == {"a": ["strong-model"], "b": ["cheap-model"]}
    assert [(ticket_of(c), c.model) for c in scripted.continuation_calls] == [("a", "strong-model")]
    assert [(ticket_of(c), c.model) for c in scripted.fixup_calls] == [("a", "strong-model")]
    assert {c.model for c in scripted.verifier_calls} == {"verifier-model"}
    events = read_journal(out)
    starts = {e["ticket"]: (e["tier"], e["model"]) for e in events if e["event"] == "step-start"}
    assert starts == {"a": ("strong", "strong-model"), "b": ("cheap", "cheap-model")}
    (fixup,) = [e for e in events if e["event"] == "fixup-start"]
    assert (fixup["ticket"], fixup["tier"], fixup["model"]) == ("a", "strong", "strong-model")
    assert {e["model"] for e in events if e["event"] == "verify-start"} == {"verifier-model"}


@pytest.mark.parametrize(
    ("tickets", "expected"),
    [
        pytest.param(ticket("a", tier='"strong"') + ticket("b", ("a",)), ("strong", "strong-model"), id="the-base-ticket-is-strong"),
        pytest.param(ticket("a") + ticket("b", ("a",), tier='"strong"'), ("strong", "strong-model"), id="the-last-ticket-is-strong"),
        pytest.param(ticket("a", tier='"cheap"') + ticket("b", ("a",)), ("standard", "standard-model"), id="the-role-tier-is-stronger"),
        pytest.param(ticket("a") + ticket("b", ("a",)), ("standard", "standard-model"), id="no-ticket-tier"),
    ],
)
def test_in_economy_mode_the_chain_fixup_uses_the_strongest_specialist_tier_of_the_tickets_it_covers(
    tmp_path, capsys, scripted, tickets, expected
):
    repo = make_repo(tmp_path, ECONOMY_HEAD + tickets)
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]

    code, out, err = run(repo, capsys, tier_table(tmp_path))

    assert (code, err) == (0, ""), err
    (fixup_call,) = scripted.fixup_calls
    assert fixup_call.model == expected[1]
    assert fixup_call.cwd.name == "b"  # the worktree of the last ticket of the stack
    events = read_journal(out)
    (fixup,) = [e for e in events if e["event"] == "fixup-start"]
    assert (fixup["tier"], fixup["model"], fixup["tickets"]) == (*expected, ["a", "b"])
    assert {e["model"] for e in events if e["event"] == "verify-start"} == {"verifier-model"}


def test_in_economy_mode_a_ticket_tier_sets_the_model_of_the_specialist_of_that_ticket_only(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY_HEAD + ticket("a", tier='"strong"') + ticket("b", ("a",)) + ticket("c", ("b",), tier='"cheap"'))

    code, out, err = run(repo, capsys, tier_table(tmp_path))

    assert (code, err) == (0, ""), err
    assert models_by_ticket(scripted.calls) == {"a": ["strong-model"], "b": ["standard-model"], "c": ["cheap-model"]}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param('"strong-model"', f"'strong-model' is a model identifier, not a tier name; {KNOWN}", id="model-of-the-table"),
        pytest.param('"vendor/some-model"', f"'vendor/some-model' is a model identifier, not a tier name; {KNOWN}", id="model-with-a-slash"),
        pytest.param('"huge"', f"'huge' is not a tier name; {KNOWN}", id="unknown-name"),
        pytest.param("3", "must be a string, not 3", id="not-a-string"),
    ],
)
@pytest.mark.parametrize("head", [pytest.param(HEAD, id="assure"), pytest.param(ECONOMY_HEAD, id="economy")])
def test_a_ticket_tier_that_is_not_a_tier_name_is_refused_and_the_message_names_the_ticket_and_the_field(
    tmp_path, capsys, scripted, value, expected, head
):
    repo = make_repo(tmp_path, head + ticket("a") + ticket("b", ("a",), tier=value) + ticket("c", ("b",)))

    code, out, err = run(repo, capsys, tier_table(tmp_path), "--dry-run")

    assert (code, out) == (1, "")
    # One message: the ticket `c` that `b` blocks gets none, and the tier order adds none.
    (line,) = err.splitlines()
    assert line.endswith(f"ticket 'b': tier: {expected}")
    assert scripted.calls == []


@pytest.mark.parametrize(("head", "refused"), [pytest.param(ECONOMY_HEAD, True, id="economy"), pytest.param(HEAD, False, id="assure")])
def test_only_in_economy_mode_a_ticket_tier_with_no_place_in_the_tier_order_is_refused(tmp_path, capsys, scripted, head, refused):
    """The fix-up of an economy chain picks the strongest tier, so each ticket tier needs a place in the tier order."""
    tiers = tier_table(tmp_path, '[tiers.premium]\nclaude-code = "premium-model"\n')
    repo = make_repo(tmp_path, head + ticket("a", tier='"premium"'))

    code, out, err = run(repo, capsys, tiers, "--dry-run")

    if refused:
        assert code == 1
        assert "ticket 'a': tier: 'premium' has no place in the tier order" in err
    else:
        assert (code, err) == (0, "")
        assert "ticket a (1/1): stack python, blocked by none, specialist tier premium" in out.splitlines()


def test_the_dry_run_prints_the_specialist_tier_of_each_ticket(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, ECONOMY_HEAD + ticket("a", tier='"strong"') + ticket("b", ("a",)))

    code, out, err = run(repo, capsys, tier_table(tmp_path), "--dry-run")

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert "ticket a (1/2): stack python, blocked by none, specialist tier strong" in lines
    assert "ticket b (2/2): stack python, blocked by a, specialist tier standard" in lines


def stopped_at_the_verifier_of_a(repo: Path, capsys, scripted, tiers: Path) -> str:
    def interrupt(request):
        if ticket_of(request) == "a":
            raise KeyboardInterrupt

    scripted.during_verifier = interrupt
    code, _, _ = run(repo, capsys, tiers)
    assert code == 130
    scripted.during_verifier = None
    scripted.calls.clear()
    scripted.verifier_calls.clear()
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    return run_dir.name


def test_a_plan_change_cannot_change_the_tier_of_a_started_ticket_and_can_set_the_tier_of_a_ticket_that_has_not_started(
    tmp_path, capsys, scripted
):
    tiers = tier_table(tmp_path)
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c", ("b",)))
    run_id = stopped_at_the_verifier_of_a(repo, capsys, scripted, tiers)
    (repo / "workflow.toml").write_text(HEAD + ticket("a", tier='"cheap"') + ticket("b", ("a",)) + ticket("c", ("b",)))

    code, out, err = run(repo, capsys, tiers, "--resume", run_id)

    assert code == 1
    assert "ticket 'a' has started; its tier changed" in err
    assert scripted.calls == [] and scripted.verifier_calls == []

    (repo / "workflow.toml").write_text(HEAD + ticket("a") + ticket("b", ("a",), tier='"cheap"') + ticket("c", ("b",)))

    code, out, err = run(repo, capsys, tiers, "--resume", run_id)

    assert (code, err) == (0, ""), err
    assert models_by_ticket(scripted.calls) == {"b": ["cheap-model"], "c": ["strong-model"]}
    events = read_journal_file(repo, run_id)
    (change,) = [e for e in events if e["event"] == "plan-change"]
    # A new tier changes no work, so the later ticket `c` needs no review.
    assert (change["changed"], change["fields"], change["reviewed"]) == (["b"], {"b": ["tier"]}, [])
    assert {t["id"]: t.get("tier") for t in change["plan"]} == {"a": None, "b": "cheap", "c": None}
    starts = {e["ticket"]: e["tier"] for e in events if e["event"] == "step-start"}
    assert starts == {"a": "strong", "b": "cheap", "c": "strong"}
