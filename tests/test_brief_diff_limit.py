"""The diff in the brief of a verifier has a size limit (#292).

A diff above the limit makes the brief longer than the context of the verifier, and the session fails. Up to the limit,
the brief holds the diff inline. Above it, the brief holds the output of `git diff --stat` for the same range, and the
exact `git diff` command that the verifier runs in its copy. The limit is 60,000 characters, and the stack field
`verifier-diff-limit` changes it. Each case uses a real temporary git repository: the brief CLI for the default limit,
`delegate run` with the scripted adapter for the stack field, and the sealed verifier of the Claude Code adapter for the guard.
"""

import re

import pytest

from delegate import adapters
from delegate.cli import brief, delegate

from .support import WORKFLOW, ScriptedAdapter, git, make_repo, run_main, write_project_agents
from .test_claude_code_seal import seal, write_agents

pytestmark = pytest.mark.usefixtures("home", "claude_stand_in")  # the dry run looks for agent files and probes the guard

LINES = 8000  # "line <n>" lines: the diff is far above 60,000 characters
SMALL_DIFF = "diff --git a/feature.txt b/feature.txt"


def stack_workflow(fields: str) -> str:
    """The test workflow with `fields` in the stack `python`."""
    return WORKFLOW.replace('gates = ["test -f feature.txt"]', f'gates = ["test -f feature.txt"]\n{fields}')


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def brief_repo(tmp_path, lines: int):
    """A repository with `main`, and the branch `feat` that adds `big.txt` with `lines` lines."""
    repo = tmp_path / "brief-repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("base\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "checkout", "-q", "-b", "feat")
    (repo / "big.txt").write_text("".join(f"line {n}\n" for n in range(lines)))
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "add big.txt")
    git(repo, "checkout", "-q", "main")
    return repo


def full_brief_text(capsys, repo) -> str:
    code = brief.main(["full", "--repo", str(repo), "--branch", "feat", "--task", "Ticket 1: add the file."])
    out = capsys.readouterr()
    assert (code, out.err) == (0, "")
    return out.out


def diff_section(text: str) -> str:
    """The text of the section `## Diff` of a brief, up to the next section."""
    return text.split("## Diff", 1)[1].split("\n## ", 1)[0]


def test_a_diff_above_the_default_limit_gives_the_stat_and_the_git_diff_command_and_not_the_diff(tmp_path, capsys):
    repo = brief_repo(tmp_path, LINES)

    text = full_brief_text(capsys, repo)

    section = diff_section(text)
    assert "```diff" not in text
    assert "+line 100" not in text
    assert "big.txt" in section and "8000 +" in section  # the line of `git diff --stat`: the path and 8000 added lines
    assert "git diff main...feat" in section
    assert "60,000 characters" in section


def test_a_diff_under_the_default_limit_is_inline(tmp_path, capsys):
    repo = brief_repo(tmp_path, 20)

    section = diff_section(full_brief_text(capsys, repo))

    assert "```diff" in section
    assert "+line 19" in section
    assert "| 20 +" not in section  # no stat


def test_the_limit_of_a_stack_decides_whether_the_verifier_brief_holds_the_diff(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, stack_workflow("verifier-diff-limit = 100000"))

    code, _out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    (verifier,) = scripted.verifier_calls
    assert SMALL_DIFF in diff_section(verifier.prompt)
    assert "```diff" in verifier.prompt


def test_a_diff_above_the_limit_of_the_stack_gives_the_stat_and_the_command_in_the_verifier_brief(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, stack_workflow("verifier-diff-limit = 100"))

    code, _out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    (verifier,) = scripted.verifier_calls
    section = diff_section(verifier.prompt)
    assert SMALL_DIFF not in verifier.prompt
    assert "feature.txt |" in section
    assert "100 characters" in section
    assert "`git diff run/demo...run/demo-a`" in section


def test_the_delta_of_a_fix_up_round_follows_the_same_rule(tmp_path, capsys, scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    repo = make_repo(tmp_path, stack_workflow("verifier-diff-limit = 100"))

    code, _out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    full, scoped = scripted.verifier_calls
    assert scoped.prompt.startswith("## Findings under verification")
    delta = scoped.prompt.split("## Delta", 1)[1].split("\n## ", 1)[0]
    assert "```diff" not in scoped.prompt
    assert re.search(r"\| +\d+ \+", delta)  # a line of the stat
    assert re.search(r"`git diff [0-9a-f]{40}\.\.\S+`", delta)
    assert "100 characters" in delta


def test_the_chain_brief_of_economy_mode_follows_the_same_rule(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, stack_workflow("verifier-diff-limit = 100").replace('mode = "assure"', 'mode = "economy"'))

    code, _out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    (verifier,) = scripted.verifier_calls
    assert "## Finding labels" in verifier.prompt
    assert SMALL_DIFF not in verifier.prompt
    assert "feature.txt |" in diff_section(verifier.prompt)


@pytest.mark.parametrize("value", ["0", "-5", "1.5", '"big"', "true"])
def test_a_limit_that_is_not_a_positive_whole_number_is_an_error_of_the_workflow(tmp_path, capsys, value):
    repo = make_repo(tmp_path, stack_workflow(f"verifier-diff-limit = {value}"))

    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run")

    assert (code, out) == (1, "")
    assert "stacks.python.verifier-diff-limit: must be a whole number of characters greater than 0" in err


def test_the_dry_run_prints_the_limit_of_each_stack(tmp_path, capsys):
    docs = '\n[stacks.docs]\nspecialist = "docs-specialist"\nverifier = "docs-verifier"\ngates = ["make docs"]\nhotspots = ["LICENSE"]\nverifier-diff-limit = 20000\n'
    text = stack_workflow("").replace('adapter = "scripted"', 'adapter = "claude-code"').replace("\n[[tickets]]", docs + "\n[[tickets]]", 1)
    text += '\n[[tickets]]\nid = "b"\ntext = "Ticket b."\nstack = "docs"\nblocked-by = []\n'
    repo = make_repo(tmp_path, text)
    write_project_agents(repo)

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])

    captured = capsys.readouterr()
    out = captured.out
    assert (code, captured.err) == (0, "")
    assert [line for line in out.splitlines() if "verifier-diff-limit" in line] == [
        "ok verifier-diff-limit: stack python: verifier-diff-limit = 60000 characters (the default)",
        "ok verifier-diff-limit: stack docs: verifier-diff-limit = 20000 characters",
    ]


@pytest.mark.usefixtures("claude_version")
def test_the_verifier_guard_of_the_claude_code_adapter_permits_the_git_diff_command_of_the_brief(tmp_path, capsys, tiers):
    repo = brief_repo(tmp_path, LINES)
    section = diff_section(full_brief_text(capsys, repo))
    (command,) = re.findall(r"`(git diff [^`]+)`", section)[:1]
    write_agents(repo, tiers)
    sealed = seal(repo, tmp_path / "seal")

    assert adapters.get("claude-code").permits(sealed, command) is None
