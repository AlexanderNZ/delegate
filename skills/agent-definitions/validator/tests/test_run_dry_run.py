"""`delegate run <workflow> --dry-run` checks a workflow file and prints the plan.

Each case writes a workflow file into a real temporary git repository and calls
`delegate.main`. The expected plan order, exit codes and messages come from the
ticket's acceptance criteria, not from the code under test.
"""

import subprocess
from pathlib import Path

import pytest

from agent_definitions import delegate

VALID = """\
base-branch = "main"
run-branch = "run/demo"
mode = "assure"
adapter = "claude-code"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["python3 -m pytest -rs"]
hotspots = ["LICENSE", ".github/workflows/*"]

[stacks.docs]
specialist = "docs-specialist"
verifier = "docs-verifier"
gates = ["make docs"]
hotspots = []

[[tickets]]
id = "c"
text = "Third ticket, inline."
stack = "python"
blocked-by = ["a", "b"]

[[tickets]]
id = "b"
text-file = "tickets/b.md"
stack = "docs"
blocked-by = ["a"]

[[tickets]]
id = "a"
text = "First ticket."
stack = "python"
blocked-by = []
"""


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Scratch", "-c", "user.email=scratch@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def make_repo(tmp_path: Path, workflow: str) -> Path:
    """A real git repository on `main` that holds `workflow.toml` and one ticket file."""
    repo = tmp_path / "repo"
    (repo / "tickets").mkdir(parents=True)
    (repo / "tickets" / "b.md").write_text("Second ticket, from a file.\n")
    (repo / "workflow.toml").write_text(workflow)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "seed")
    return repo


def state(repo: Path) -> tuple[str, str, str, list[str]]:
    """Branches, worktrees, status and every file on disk, as one comparable value."""
    files = sorted(str(p.relative_to(repo)) for p in repo.rglob("*") if ".git" not in p.parts)
    return (git(repo, "branch", "--all"), git(repo, "worktree", "list"), git(repo, "status", "--short"), files)


def run(repo: Path, capsys, *extra: str) -> tuple[int, str, str]:
    try:
        code = delegate.main(["run", str(repo / "workflow.toml"), *extra])
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_valid_workflow_prints_the_plan_in_dependency_order_and_creates_nothing(tmp_path, capsys):
    repo = make_repo(tmp_path, VALID)
    before = state(repo)

    code, out, err = run(repo, capsys, "--dry-run")

    assert (code, err) == (0, "")
    plan_ids = [line.split()[1] for line in out.splitlines() if line.startswith("ticket ")]
    assert plan_ids == ["a", "b", "c"]
    assert state(repo) == before
    assert not (repo.parent / "repo-worktrees").exists()


def invalid(tmp_path: Path, capsys, workflow: str) -> tuple[int, str, str]:
    """Run --dry-run on a workflow and check that it created nothing."""
    repo = make_repo(tmp_path, workflow)
    before = state(repo)
    result = run(repo, capsys, "--dry-run")
    assert state(repo) == before
    return result


def test_an_unknown_stack_exits_1_and_names_the_ticket_and_the_stack(tmp_path, capsys):
    code, out, err = invalid(tmp_path, capsys, VALID.replace('id = "a"\ntext = "First ticket."\nstack = "python"', 'id = "a"\ntext = "First ticket."\nstack = "rust"'))

    assert code == 1
    assert out == ""
    assert "ticket 'a'" in err
    assert "unknown stack 'rust'" in err
    assert "docs, python" in err  # the known stacks, sorted


def test_an_unknown_blocker_exits_1_and_names_the_ticket_and_the_blocker(tmp_path, capsys):
    code, out, err = invalid(tmp_path, capsys, VALID.replace('blocked-by = ["a"]', 'blocked-by = ["z9"]'))

    assert code == 1
    assert out == ""
    assert "ticket 'b'" in err
    assert "unknown blocker 'z9'" in err


def test_a_cycle_exits_1_and_names_every_ticket_in_it(tmp_path, capsys):
    cyclic = VALID.replace('id = "a"\ntext = "First ticket."\nstack = "python"\nblocked-by = []', 'id = "a"\ntext = "First ticket."\nstack = "python"\nblocked-by = ["c"]')

    code, out, err = invalid(tmp_path, capsys, cyclic)

    assert code == 1
    assert out == ""
    assert "cycle" in err
    for ticket_id in ("a", "b", "c"):
        assert f"'{ticket_id}'" in err


def test_a_ticket_that_blocks_itself_is_a_cycle(tmp_path, capsys):
    code, _out, err = invalid(tmp_path, capsys, VALID.replace('id = "a"\ntext = "First ticket."\nstack = "python"\nblocked-by = []', 'id = "a"\ntext = "First ticket."\nstack = "python"\nblocked-by = ["a"]'))

    assert code == 1
    assert "cycle" in err
    assert "'a'" in err


def test_every_problem_in_one_file_is_reported_in_one_run(tmp_path, capsys):
    broken = VALID.replace('stack = "docs"\nblocked-by = ["a"]', 'stack = "rust"\nblocked-by = ["z9"]')

    code, _out, err = invalid(tmp_path, capsys, broken)

    assert code == 1
    assert "unknown stack 'rust'" in err
    assert "unknown blocker 'z9'" in err


def with_overrides(**roles: str) -> str:
    lines = "".join(f'{role} = "{tier}"\n' for role, tier in roles.items())
    return VALID.replace("[stacks.python]", f"[tier-overrides]\n{lines}\n[stacks.python]", 1)


def test_a_model_identifier_in_place_of_a_tier_name_exits_1_and_names_the_field(tmp_path, capsys):
    code, out, err = invalid(tmp_path, capsys, with_overrides(specialist="opus"))

    assert code == 1
    assert out == ""
    assert "tier-overrides.specialist" in err
    assert "'opus'" in err
    assert "model identifier" in err
    assert "cheap, standard, strong, verifier" in err  # the tier names that are valid


def test_a_provider_model_identifier_in_the_verifier_role_is_refused_by_field(tmp_path, capsys):
    code, _out, err = invalid(tmp_path, capsys, with_overrides(verifier="anthropic/claude-opus-5"))

    assert code == 1
    assert "tier-overrides.verifier" in err
    assert "'anthropic/claude-opus-5'" in err


def test_a_tier_name_that_does_not_exist_is_refused_by_field(tmp_path, capsys):
    code, _out, err = invalid(tmp_path, capsys, with_overrides(specialist="turbo"))

    assert code == 1
    assert "tier-overrides.specialist" in err
    assert "'turbo'" in err


def test_an_override_for_an_unknown_role_is_refused_by_field(tmp_path, capsys):
    code, _out, err = invalid(tmp_path, capsys, with_overrides(reviewer="cheap"))

    assert code == 1
    assert "tier-overrides.reviewer" in err
    assert "specialist, verifier" in err


def test_a_tier_name_override_is_accepted_and_shown_in_the_plan(tmp_path, capsys):
    repo = make_repo(tmp_path, with_overrides(specialist="cheap"))

    code, out, err = run(repo, capsys, "--dry-run")

    assert (code, err) == (0, "")
    assert "tier override specialist: cheap" in out


def test_a_tier_file_of_the_user_defines_the_valid_tier_names(tmp_path, capsys):
    repo = make_repo(tmp_path, with_overrides(specialist="gateway-fast"))
    tiers = tmp_path / "my-tiers.toml"
    tiers.write_text(
        """\
[effort]
levels = ["low"]
[effort.opencode]
variants = ["high"]
[tiers.gateway-fast]
claude-code = "inherit"
opencode = "gateway/fast-1"
[allowed-models]
claude-code = ["inherit"]
opencode = ["gateway/fast-1"]
[budget]
claude-code-description-chars = 1000
"""
    )

    assert run(repo, capsys, "--dry-run", "--tiers", str(tiers))[0] == 0
    code, _out, err = run(repo, capsys, "--dry-run")  # the bundled table has no such tier
    assert code == 1
    assert "'gateway-fast'" in err


def test_an_unknown_mode_exits_1_and_names_the_field_and_the_modes(tmp_path, capsys):
    code, out, err = invalid(tmp_path, capsys, VALID.replace('mode = "assure"', 'mode = "sprint"'))

    assert code == 1
    assert out == ""
    assert "mode" in err
    assert "'sprint'" in err
    assert "assure, economy" in err


def test_an_unknown_adapter_exits_1_and_names_the_field_and_the_adapters(tmp_path, capsys):
    code, out, err = invalid(tmp_path, capsys, VALID.replace('adapter = "claude-code"', 'adapter = "codex"'))

    assert code == 1
    assert out == ""
    assert "adapter" in err
    assert "'codex'" in err
    assert "claude-code, cursor, opencode" in err


@pytest.mark.parametrize("mode", ["assure", "economy"])
@pytest.mark.parametrize("adapter", ["claude-code", "opencode", "cursor"])
def test_each_mode_and_each_built_in_adapter_is_accepted(tmp_path, capsys, mode, adapter):
    repo = make_repo(tmp_path, VALID.replace('mode = "assure"', f'mode = "{mode}"').replace('adapter = "claude-code"', f'adapter = "{adapter}"'))

    code, out, err = run(repo, capsys, "--dry-run")

    assert (code, err) == (0, "")
    assert f"mode {mode}, adapter {adapter}" in out
