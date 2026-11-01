"""The workflow check refuses a ticket id or a branch name that git or the file system cannot take.

Each case writes a workflow file into a real temporary git repository and calls
`delegate.main`. Before #181 the check accepted any string as an id, and the
engine failed after `run-start`, or wrote outside its state directory.
"""

from pathlib import Path

import pytest

from delegate.cli import delegate

from .support import state_dir
from .support import TEMPLATE_GIT, copy_of, git, write_project_agents

pytestmark = pytest.mark.usefixtures("home", "claude_stand_in")  # the dry run looks in the user directory for agent files (#182), and probes the guard (#219)

WORKFLOW = """\
base-branch = "main"
run-branch = "run/demo"
mode = "assure"
adapter = "claude-code"

[stacks.python]
specialist = "python-specialist"
verifier = "python-verifier"
gates = ["python3 -m pytest -rs"]
hotspots = []

[[tickets]]
id = "{ticket_id}"
text = "A ticket."
stack = "python"
blocked-by = []
"""


def make_repo(tmp_path: Path, workflow: str) -> Path:
    def build(repo: Path) -> None:
        (repo / "workflow.toml").write_text(workflow)
        write_project_agents(repo)
        git(repo, *TEMPLATE_GIT, "init", "-q", "-b", "main")
        git(repo, *TEMPLATE_GIT, "add", "-A")
        git(repo, *TEMPLATE_GIT, "commit", "-q", "-m", "seed")

    return copy_of(tmp_path / "repo", ("names", workflow), build)


def run(repo: Path, capsys, *extra: str) -> tuple[int, str, str]:
    try:
        code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), *extra])
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def workflow_with_id(ticket_id: str) -> str:
    return WORKFLOW.format(ticket_id=ticket_id)


BAD_IDS = ["/tmp/dlg-abs-ticket1", "a..b", "..", "a b", "-a", "a.lock", "a.", ".a", "a/b", "a~b", "a:b", "a@{b"]


@pytest.mark.parametrize("ticket_id", BAD_IDS)
def test_a_bad_ticket_id_is_refused_by_name_and_the_run_makes_no_branch(tmp_path, capsys, ticket_id):
    repo = make_repo(tmp_path, workflow_with_id(ticket_id))
    branches, trees = git(repo, "branch", "--all"), git(repo, "worktree", "list")

    code, out, err = run(repo, capsys)

    assert (code, out) == (1, "")
    assert repr(ticket_id) in err
    assert git(repo, "branch", "--all") == branches
    assert git(repo, "worktree", "list") == trees
    assert not (state_dir(repo) / "runs").exists()


@pytest.mark.parametrize("ticket_id", BAD_IDS)
def test_dry_run_names_a_bad_ticket_id(tmp_path, capsys, ticket_id):
    repo = make_repo(tmp_path, workflow_with_id(ticket_id))

    code, _out, err = run(repo, capsys, "--dry-run")

    assert code == 1
    assert repr(ticket_id) in err


@pytest.mark.parametrize("ticket_id", ["1", "a", "181", "fix-up.2", "A_b-3"])
def test_a_plain_ticket_id_passes_the_check(tmp_path, capsys, ticket_id):
    repo = make_repo(tmp_path, workflow_with_id(ticket_id))

    code, out, err = run(repo, capsys, "--dry-run")

    assert (code, err) == (0, "")
    assert f"ticket {ticket_id}" in out


@pytest.mark.parametrize(
    "branch",
    ["bad branch", "a..b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b", "a@{b", "a//b", "a/.b", "a/", "a.", "a.lock", "a.lock/b", "/a", "-a", "@", "a\tb"],
)
@pytest.mark.parametrize("field", ["base-branch", "run-branch"])
def test_a_branch_name_that_git_refuses_is_named_in_the_dry_run(tmp_path, capsys, field, branch):
    current = 'base-branch = "main"' if field == "base-branch" else 'run-branch = "run/demo"'
    workflow = workflow_with_id("1").replace(current, f"{field} = '{branch}'")  # a TOML literal string keeps the backslash and the tab
    repo = make_repo(tmp_path, workflow)

    code, out, err = run(repo, capsys, "--dry-run")

    assert (code, out) == (1, "")
    assert field in err
    assert repr(branch) in err


def test_a_ticket_branch_that_git_refuses_is_named_by_the_ticket(tmp_path, capsys):
    repo = make_repo(tmp_path, workflow_with_id("a.lock"))

    code, _out, err = run(repo, capsys, "--dry-run")

    assert code == 1
    assert "ticket 'a.lock'" in err
    assert "run/demo-a.lock" in err


def test_every_bad_id_and_branch_is_listed_in_one_dry_run(tmp_path, capsys):
    workflow = workflow_with_id("a b").replace('"run/demo"', '"run//demo"')
    workflow += '\n[[tickets]]\nid = "-x"\ntext = "Two."\nstack = "python"\nblocked-by = []\n'
    repo = make_repo(tmp_path, workflow)

    code, _out, err = run(repo, capsys, "--dry-run")

    assert code == 1
    assert "'a b'" in err and "'-x'" in err and "'run//demo'" in err
