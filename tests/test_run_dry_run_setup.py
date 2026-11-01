"""`delegate run --dry-run` refuses a workflow that a run of it would fail for a reason of setup (#182).

A missing agent file fails the first agent of its stack. A workflow gate that the verifier guard does not permit shows
only when the verifier tries to run it. The check looks at the agent files of the adapter of the workflow, in the
project directory and in the user directory, and it asks the rendered verifier whether it permits each gate.

Each case writes real agent files that the renderer made into a real temporary git repository, and calls `delegate.main`.
"""

from pathlib import Path

import pytest

from delegate.cli import delegate
from delegate.definitions.declaration import Agent
from delegate.definitions.render import render

from .support import TEMPLATE_GIT, git

# The dry run starts the guard probe of each verifier of a `claude-code` workflow (#219), so a stand-in answers it.
pytestmark = pytest.mark.usefixtures("claude_stand_in")

WORKFLOW = """\
base-branch = "main"
run-branch = "run/demo"
mode = "assure"
adapter = "{adapter}"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["python3 -m pytest -rs"]
hotspots = []

[stacks.docs]
specialist = "docs-specialist"
verifier = "docs-verifier"
gates = ["make docs", "make links"]
hotspots = []

[[tickets]]
id = "a"
text = "First ticket."
stack = "python"
blocked-by = []

[[tickets]]
id = "b"
text = "Second ticket."
stack = "docs"
blocked-by = ["a"]
"""

# The agents of the two stacks, as a declaration holds them. Each lists the gates of its stack in `gateCommands`.
DECLARED = {
    "python": ["python3 -m pytest -rs"],
    "docs": ["make docs", "make links"],
}

CLAUDE_CODE_DIR = Path(".claude") / "agents"
OPENCODE_DIR = Path(".opencode") / "agents"


def make_repo(tmp_path: Path, adapter: str = "claude-code") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "workflow.toml").write_text(WORKFLOW.format(adapter=adapter))
    git(repo, *TEMPLATE_GIT, "init", "-q", "-b", "main")
    git(repo, *TEMPLATE_GIT, "add", "-A")
    git(repo, *TEMPLATE_GIT, "commit", "-q", "-m", "seed")
    return repo


def write_agents(root: Path, harness: str, tiers, declared: dict[str, list[str]] = DECLARED, skip: tuple[str, ...] = ()) -> None:
    """Render the pair of each stack of `declared` and write its files under `root`, except the files in `skip`."""
    agents = [Agent(key=key, description=f"The {key} stack.", gate_commands=gates) for key, gates in declared.items()]
    directory = root / (CLAUDE_CODE_DIR if harness == "claude-code" else OPENCODE_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    for name, content in render(agents, tiers)[harness].items():
        if name not in skip:
            (directory / name).write_text(content)


def dry_run(repo: Path, capsys, *extra: str) -> tuple[int, str, str]:
    try:
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run", *extra])
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_a_missing_verifier_file_exits_1_and_names_the_stack_the_agent_and_the_paths_it_looked_in(tmp_path, home, tiers, capsys):
    repo = make_repo(tmp_path)
    write_agents(repo, "claude-code", tiers, skip=("docs-verifier.md",))

    code, out, err = dry_run(repo, capsys)

    assert code == 1
    assert "stack docs" in err
    assert "docs-verifier" in err
    assert str(repo / CLAUDE_CODE_DIR / "docs-verifier.md") in err
    assert str(home / CLAUDE_CODE_DIR / "docs-verifier.md") in err
    assert "python-verifier" not in err  # the files of the other stack are there
    assert "stack python: checked" in out
    assert "stack docs: checked" not in out


def test_a_missing_specialist_file_is_named_too(tmp_path, home, tiers, capsys):
    repo = make_repo(tmp_path)
    write_agents(repo, "claude-code", tiers, skip=("python-specialist.md",))

    code, _, err = dry_run(repo, capsys)

    assert code == 1
    assert "stack python" in err
    assert str(repo / CLAUDE_CODE_DIR / "python-specialist.md") in err


def test_an_agent_file_in_the_user_directory_counts(tmp_path, home, tiers, capsys):
    repo = make_repo(tmp_path)
    write_agents(home, "claude-code", tiers)

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    assert "stack python: checked" in out
    assert "stack docs: checked" in out


def test_a_gate_that_the_rendered_verifier_denies_exits_1_and_names_the_gate_and_the_stack(tmp_path, home, tiers, capsys):
    repo = make_repo(tmp_path)
    # The declaration of the docs stack lacks `make links`, so its verifier guard does not permit that gate.
    write_agents(repo, "claude-code", tiers, {**DECLARED, "docs": ["make docs"]})

    code, out, err = dry_run(repo, capsys)

    assert code == 1
    assert "stack docs" in err
    assert "make links" in err
    assert str(repo / CLAUDE_CODE_DIR / "docs-verifier.md") in err
    assert "'make docs'" not in err  # the gate that the verifier permits is not named
    assert "stack python: checked" in out
    assert "stack docs: checked" not in out


def test_matching_agent_files_and_gates_exit_0_and_print_checked_for_each_stack(tmp_path, home, tiers, capsys):
    repo = make_repo(tmp_path)
    write_agents(repo, "claude-code", tiers)

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    assert [line for line in out.splitlines() if "checked" in line] == ["stack python: checked", "stack docs: checked"]
    assert "ticket a (1/2)" in out  # the plan is there too


def test_opencode_checks_the_files_and_the_permission_map(tmp_path, home, tiers, capsys):
    repo = make_repo(tmp_path, "opencode")
    write_agents(repo, "opencode", tiers, {**DECLARED, "docs": ["make docs"]}, skip=("python-specialist.md",))

    code, out, err = dry_run(repo, capsys)

    assert code == 1
    assert str(repo / OPENCODE_DIR / "python-specialist.md") in err
    assert str(home / ".config" / "opencode" / "agents" / "python-specialist.md") in err
    assert "make links" in err and "docs" in err
    assert str(repo / OPENCODE_DIR / "docs-verifier.md") in err


def test_opencode_with_matching_files_and_gates_prints_checked_for_each_stack(tmp_path, home, tiers, capsys):
    repo = make_repo(tmp_path, "opencode")
    write_agents(repo, "opencode", tiers)

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    assert [line for line in out.splitlines() if "checked" in line] == ["stack python: checked", "stack docs: checked"]


def test_a_harness_with_no_check_prints_one_line_that_says_it_skipped_the_check_and_why(tmp_path, home, scripted, capsys):
    repo = make_repo(tmp_path, "scripted")

    code, out, err = dry_run(repo, capsys)

    assert (code, err) == (0, "")
    [line] = [line for line in out.splitlines() if "skipped" in line]
    assert "scripted" in line
    assert "checked" not in out.replace(line, "")


def test_the_check_changes_nothing_on_disk(tmp_path, home, tiers, capsys):
    repo = make_repo(tmp_path)
    write_agents(repo, "claude-code", tiers, {**DECLARED, "docs": ["make docs"]})
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if ".git" not in p.parts)

    dry_run(repo, capsys)

    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if ".git" not in p.parts) == before


def test_a_scripted_adapter_that_seals_is_checked_through_the_port_with_no_harness_knowledge(tmp_path, home, scripted, capsys):
    repo = make_repo(tmp_path, "scripted")
    user = tmp_path / "user-agents"
    user.mkdir()
    for name in ("python-specialist", "python-verifier", "docs-specialist", "docs-verifier"):
        (user / f"{name}.md").write_text("rules\n")
    scripted.checks_setup = True
    scripted.user_agents = user
    scripted.denials = {"make links": "the scripted guard says no"}

    code, out, err = dry_run(repo, capsys)

    assert code == 1
    assert "stack python: checked" in out
    assert "stack docs: checked" not in out
    assert "the gate 'make links': the scripted guard says no" in err
    assert str(user / "docs-verifier.md") in err
    assert "skipped" not in out


def test_a_scripted_adapter_that_seals_names_the_paths_it_looked_in_for_a_missing_agent(tmp_path, home, scripted, capsys):
    repo = make_repo(tmp_path, "scripted")
    scripted.checks_setup = True

    code, _, err = dry_run(repo, capsys)

    assert code == 1
    assert str(repo / ".claude" / "agents" / "python-specialist.md") in err
