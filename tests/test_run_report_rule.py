"""Each brief tells the agent to write its report only at an absolute path, and to change nothing else in the git directory.

The report path is outside the worktree. A brief that says "change no file
outside the worktree" contradicts itself. A specialist then made a relative
path to the runs directory from its worktree, made a stray directory inside the
git directory, and removed it with `rm -rf`.

Each case starts `delegate.main` from the directory above the repository, with
a relative workflow path and a relative `--repo`. The scripted adapter plays
each agent and records the brief that it gets.
"""

import json
import re
from pathlib import Path

import pytest

from delegate import adapters
from delegate.cli import delegate

from .support import ScriptedAdapter, git, make_repo, valid_report

RULE = re.compile(r"Write the report only at the absolute path `([^`]+)`\.")
GIT_DIRECTORY_RULE = re.compile(r"Except for the (worktree|copy) and the report, create, change, or remove nothing inside the git directory")
CONTRADICTION = "Change no file outside it."


@pytest.fixture
def scripted():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run_from_above(tmp_path, monkeypatch, capsys):
    """Run the workflow with relative paths, from the directory that holds the repository."""
    repo = make_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    code = delegate.main(["run", f"{repo.name}/workflow.toml", "--repo", repo.name])
    captured = capsys.readouterr()
    assert (code, captured.err) == (0, "")


def specialist(scripted):
    (call,) = scripted.calls
    return call


def resumed_continuation(scripted):
    scripted.supports_resume = True
    scripted.outcomes = [("capped", 0), ("finished", 0)]
    scripted.continuation_files = [{"second.txt": "second\n"}]
    return lambda: scripted.continuation_calls[0]


def new_agent_continuation(scripted):
    scripted.outcomes = [("capped", 0), ("finished", 0)]
    scripted.continuation_files = [{"second.txt": "second\n"}]
    return lambda: scripted.continuation_calls[0]


def fixup_specialist(scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    return lambda: scripted.fixup_calls[0]


def report_correction(scripted):
    """The first report has a field of the wrong type, so the engine asks only for a corrected report."""

    def invalid_until_corrected(request, head):
        branch = git(request.cwd, "rev-parse", "--abbrev-ref", "HEAD").strip()
        if "## Continuation" in request.prompt:
            return json.dumps(valid_report("a", branch, head))
        return json.dumps(valid_report("a", branch, head, gates_green="yes"))

    scripted.report_text = invalid_until_corrected
    return lambda: scripted.continuation_calls[0]


def full_verifier(scripted):
    return lambda: scripted.verifier_calls[0]


def fixup_verifier(scripted):
    scripted.verdict_sequence = ["REJECT", "ACCEPT"]
    return lambda: scripted.verifier_calls[1]


SCENARIOS = {
    "specialist": lambda scripted: lambda: specialist(scripted),
    "resumed continuation": resumed_continuation,
    "new-agent continuation": new_agent_continuation,
    "report correction": report_correction,
    "fix-up specialist": fixup_specialist,
    "full verifier": full_verifier,
    "fix-up verifier": fixup_verifier,
}


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_the_brief_gives_an_absolute_report_path_and_forbids_any_other_change_in_the_git_directory(
    tmp_path, monkeypatch, capsys, scripted, scenario
):
    request_of = SCENARIOS[scenario](scripted)

    run_from_above(tmp_path, monkeypatch, capsys)

    request = request_of()
    paths = RULE.findall(request.prompt)
    assert paths, f"the {scenario} brief does not tell the agent to write the report only at an absolute path"
    assert all(Path(path).is_absolute() for path in paths)
    assert set(paths) == {str(request.report_path)}
    assert GIT_DIRECTORY_RULE.search(request.prompt), f"the {scenario} brief does not forbid other changes in the git directory"
    # The report is outside the worktree, so a ban on every file outside the worktree contradicts the report path.
    assert CONTRADICTION not in request.prompt
