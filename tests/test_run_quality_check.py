"""The quality check of `delegate run` (#247).

A coordinator that copies an old workflow file reads no reference, but it reads the output of `delegate run --dry-run`
before each launch. The dry run therefore prints a section `quality check` after the plan: one line for each check, for
each stack or for the workflow, with `ok` and the setting that the workflow uses, or `warn`, the field to set and the
effect of the missing setting. A `warn` does not change the exit status. `delegate run` without `--dry-run` prints only
the `warn` lines, on stderr, at its start, and a detached run writes them to its log.

Each case writes a workflow into a real temporary git repository and calls `delegate.main`. The expected lines come
from the table of checks in the ticket, not from the code under test.
"""

from __future__ import annotations

import pytest

from delegate import adapters
from delegate.cli import delegate

from . import claude_fake
from .support import NO_TIMEOUTS_WORKFLOW, ScriptedAdapter, make_repo, run_main, write_project_agents
from .test_run_detach import printed, wait_for_ends

pytestmark = pytest.mark.usefixtures("home", "claude_stand_in")  # the dry run checks the agent files and probes the guard

# The support workflow with no time limits, on an adapter with a setup check: one stack `python` with hotspots, one ticket, `assure` mode.
BASE = NO_TIMEOUTS_WORKFLOW.replace('adapter = "scripted"', 'adapter = "claude-code"')
TIMEOUTS = 'hotspots = ["LICENSE", ".github/workflows/*"]\ngate-timeout = 30\nagent-timeout = 90\n'
HOTSPOTS = 'hotspots = ["LICENSE", ".github/workflows/*"]\n'


def with_timeouts(workflow: str) -> str:
    """`workflow` with a gate-timeout of 30 minutes and an agent-timeout of 90 minutes in the stack `python`."""
    return workflow.replace(HOTSPOTS, TIMEOUTS)


def unblocked_ticket(ticket_id: str) -> str:
    return f'\n[[tickets]]\nid = "{ticket_id}"\ntext = "Ticket {ticket_id}."\nstack = "python"\nblocked-by = []\n'


def with_ticket_start(workflow: str, value: str) -> str:
    return workflow.replace('mode = "assure"\n', f'mode = "assure"\nticket-start = "{value}"\n')


def dry_run(tmp_path, capsys, workflow: str) -> tuple[int, list[str], str]:
    """The exit code, the lines of the section `quality check`, and the stderr of a dry run of `workflow`."""
    repo = make_repo(tmp_path, workflow)
    write_project_agents(repo)
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert lines.count("quality check") == 1, captured.out
    after = lines[lines.index("quality check") + 1:]
    section = []
    for line in after:
        if not line.startswith(("ok ", "warn ")):
            break
        section.append(line)
    return code, section, captured.err


def check(section: list[str], check_id: str) -> list[str]:
    """The lines of the check `check_id` in the section."""
    return [line for line in section if line.split()[1].rstrip(":") == check_id]


def test_the_section_follows_the_plan_and_comes_before_the_setup_check(tmp_path, capsys):
    repo = make_repo(tmp_path, with_timeouts(BASE))
    write_project_agents(repo)

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    ticket = next(n for n, line in enumerate(lines) if line.startswith("ticket a "))
    setup = lines.index("stack python: checked")
    assert ticket < lines.index("quality check") < setup


def test_a_stack_with_no_gate_timeout_warns_and_names_the_field_and_the_effect(tmp_path, capsys):
    code, section, err = dry_run(tmp_path, capsys, BASE)

    assert code == 0
    assert check(section, "gate-timeout") == [
        "warn gate-timeout: stack python: set gate-timeout; without it, a gate that hangs stops the run with no limit"
    ]
    assert "warn" not in err


def test_a_stack_with_a_gate_timeout_is_ok_and_names_the_setting(tmp_path, capsys):
    code, section, _ = dry_run(tmp_path, capsys, with_timeouts(BASE))

    assert code == 0
    assert check(section, "gate-timeout") == ["ok gate-timeout: stack python: gate-timeout = 30 minutes"]


def test_a_stack_with_no_agent_timeout_warns_and_names_the_field_and_the_effect(tmp_path, capsys):
    code, section, _ = dry_run(tmp_path, capsys, BASE)

    assert code == 0
    assert check(section, "agent-timeout") == [
        "warn agent-timeout: stack python: set agent-timeout; without it, an agent that hangs stops the run with no limit"
    ]


def test_a_stack_with_an_agent_timeout_is_ok_and_names_the_setting(tmp_path, capsys):
    code, section, _ = dry_run(tmp_path, capsys, with_timeouts(BASE))

    assert code == 0
    assert check(section, "agent-timeout") == ["ok agent-timeout: stack python: agent-timeout = 90 minutes"]


def test_a_fraction_of_a_minute_shows_as_a_fraction(tmp_path, capsys):
    code, section, _ = dry_run(tmp_path, capsys, with_timeouts(BASE).replace("gate-timeout = 30", "gate-timeout = 0.5"))

    assert code == 0
    assert check(section, "gate-timeout") == ["ok gate-timeout: stack python: gate-timeout = 0.5 minutes"]


def test_each_stack_gets_its_own_line_of_a_stack_check(tmp_path, capsys):
    docs = '\n[stacks.docs]\nspecialist = "docs-specialist"\nverifier = "docs-verifier"\ngates = ["make docs"]\nhotspots = ["LICENSE"]\ngate-timeout = 5\n'
    workflow = BASE.replace("\n[[tickets]]", docs + "\n[[tickets]]", 1) + unblocked_ticket("b").replace('"python"', '"docs"')

    code, section, _ = dry_run(tmp_path, capsys, workflow)

    assert code == 0
    assert check(section, "gate-timeout") == [
        "warn gate-timeout: stack python: set gate-timeout; without it, a gate that hangs stops the run with no limit",
        "ok gate-timeout: stack docs: gate-timeout = 5 minutes",
    ]


def test_base_branch_in_assure_mode_with_two_unblocked_tickets_warns_once_and_names_the_field_and_the_effect(tmp_path, capsys):
    workflow = with_ticket_start(BASE, "base-branch") + unblocked_ticket("b") + unblocked_ticket("c")

    code, section, _ = dry_run(tmp_path, capsys, workflow)

    assert code == 0
    assert check(section, "ticket-start") == [
        'warn ticket-start: ticket-start = "base-branch", and 3 tickets have no blocked-by; set ticket-start = "run-branch"; '
        "without it, each such ticket after the first runs its gates again after the rebase, which doubles the gate time "
        "and the chance of a flaky failure"
    ]


def test_base_branch_with_one_unblocked_ticket_is_ok(tmp_path, capsys):
    workflow = with_ticket_start(BASE, "base-branch") + unblocked_ticket("b").replace("blocked-by = []", 'blocked-by = ["a"]')

    code, section, _ = dry_run(tmp_path, capsys, workflow)

    assert code == 0
    assert check(section, "ticket-start") == [
        'ok ticket-start: ticket-start = "base-branch", and fewer than two tickets have no blocked-by'
    ]


def test_the_default_ticket_start_of_assure_mode_with_two_unblocked_tickets_is_ok_and_names_the_setting(tmp_path, capsys):
    code, section, _ = dry_run(tmp_path, capsys, BASE + unblocked_ticket("b"))

    assert code == 0
    assert check(section, "ticket-start") == ['ok ticket-start: ticket-start = "run-branch": each ticket starts from the run branch']


def test_economy_mode_with_two_unblocked_tickets_is_ok(tmp_path, capsys):
    workflow = BASE.replace('mode = "assure"', 'mode = "economy"') + unblocked_ticket("b")

    code, section, _ = dry_run(tmp_path, capsys, workflow)

    assert code == 0
    assert check(section, "ticket-start") == ["ok ticket-start: economy mode: each ticket starts from the previous ticket"]


def test_a_stack_with_no_hotspots_warns_and_names_the_field_and_the_effect(tmp_path, capsys):
    code, section, _ = dry_run(tmp_path, capsys, BASE.replace(HOTSPOTS, "hotspots = []\n"))

    assert code == 0
    assert check(section, "hotspots") == [
        "warn hotspots: stack python: hotspots = []; set hotspots; without them, only the built-in agent directories are "
        "guarded, and a specialist can change the CI and the configuration"
    ]


def test_a_stack_with_hotspots_is_ok_and_names_them(tmp_path, capsys):
    code, section, _ = dry_run(tmp_path, capsys, BASE)

    assert code == 0
    assert check(section, "hotspots") == ["ok hotspots: stack python: hotspots LICENSE, .github/workflows/*"]


def test_the_verifier_tier_is_ok_and_names_the_tier(tmp_path, capsys):
    code, section, _ = dry_run(tmp_path, capsys, BASE)

    assert code == 0
    assert check(section, "verifier-tier") == ["ok verifier-tier: the verifier runs on the tier verifier"]


def test_a_verifier_override_is_ok_and_names_the_tier_of_the_override(tmp_path, capsys):
    workflow = BASE.replace("\n[stacks.python]", '\n[tier-overrides]\nverifier = "strong"\n\n[stacks.python]', 1)

    code, section, _ = dry_run(tmp_path, capsys, workflow)

    assert code == 0
    assert check(section, "verifier-tier") == ["ok verifier-tier: the verifier runs on the tier strong"]


def test_a_verifier_on_cheap_is_an_error_of_the_workflow_and_not_only_a_warning(tmp_path, capsys):
    # The workflow check refuses a verifier below the verifier tier (#14), so the warning of the ticket is an error.
    repo = make_repo(tmp_path, BASE.replace("\n[stacks.python]", '\n[tier-overrides]\nverifier = "cheap"\n\n[stacks.python]', 1))

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])

    captured = capsys.readouterr()
    assert (code, captured.out) == (1, "")
    assert "tier-overrides.verifier: 'cheap' is below the verifier tier" in captured.err


def test_each_gate_with_its_own_short_name_is_ok_and_names_the_names(tmp_path, capsys):
    workflow = BASE.replace('gates = ["test -f feature.txt"]', 'gates = ["python3 -m pytest", { name = "docs", command = "make docs" }]')

    code, section, _ = dry_run(tmp_path, capsys, workflow)

    assert code == 0
    assert check(section, "gate-names") == ["ok gate-names: stack python: each gate has its own short name: pytest, docs"]


def test_a_workflow_with_only_warnings_exits_0_and_prints_no_warning_on_stderr(tmp_path, capsys):
    workflow = with_ticket_start(BASE.replace(HOTSPOTS, "hotspots = []\n"), "base-branch") + unblocked_ticket("b")
    workflow = workflow.replace('gates = ["test -f feature.txt"]', 'gates = ["cd a && npx vitest run", "cd b && npx vitest run"]')

    code, section, err = dry_run(tmp_path, capsys, workflow)

    assert code == 0
    assert [line.split()[:2] for line in section if line.startswith("warn ")] == [
        ["warn", "gate-timeout:"], ["warn", "agent-timeout:"], ["warn", "ticket-start:"], ["warn", "hotspots:"], ["warn", "gate-names:"],
    ]
    assert err == ""


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def test_a_run_without_dry_run_prints_the_warn_lines_on_stderr_and_no_ok_line(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, NO_TIMEOUTS_WORKFLOW.replace(HOTSPOTS, "hotspots = []\n"))

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert code == 0
    assert err.splitlines() == [
        "delegate run: warn gate-timeout: stack python: set gate-timeout; without it, a gate that hangs stops the run with no limit",
        "delegate run: warn agent-timeout: stack python: set agent-timeout; without it, an agent that hangs stops the run with no limit",
        "delegate run: warn hotspots: stack python: hotspots = []; set hotspots; without them, only the built-in agent "
        "directories are guarded, and a specialist can change the CI and the configuration",
    ]
    assert "quality check" not in out
    assert not [line for line in out.splitlines() if line.startswith("ok ")]


def test_a_run_whose_checks_are_all_ok_prints_nothing_on_stderr(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, with_timeouts(NO_TIMEOUTS_WORKFLOW))

    code, _, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")


def test_a_detached_run_writes_the_warn_lines_to_its_log_and_not_to_the_stderr_of_the_caller(tmp_path, monkeypatch, capsys):
    claude_fake.install(tmp_path, monkeypatch, "finished.jsonl")
    repo = make_repo(tmp_path, BASE)
    write_project_agents(repo)

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--detach")

    assert (code, err) == (0, "")
    _, log = printed(out)
    text = wait_for_ends(log, 1)
    warnings = [line for line in text.splitlines() if line.startswith("delegate run: warn ")]
    assert [line.split()[3] for line in warnings] == ["gate-timeout:", "agent-timeout:"]
