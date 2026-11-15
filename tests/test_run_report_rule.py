"""Each brief tells the agent to write its report only at an absolute path, and to change nothing else in the git directory.

The report path is outside the worktree. A brief that says "change no file
outside the worktree" contradicts itself. A specialist then made a relative
path to the runs directory from its worktree, made a stray directory inside the
git directory, and removed it with `rm -rf`.

Each case starts `delegate.main` from the directory above the repository, with
a relative workflow path and a relative `--repo`. The scripted adapter plays
each agent and records the brief that it gets.
"""

import re
from pathlib import Path

import pytest


from .support_run import SCENARIOS, run_from_above

RULE = re.compile(r"Write the report only at the absolute path `([^`]+)`\.")
GIT_DIRECTORY_RULE = re.compile(r"Except for the (worktree|copy) and the report, create, change, or remove nothing inside the git directory")
CONTRADICTION = "Change no file outside it."


pytestmark = pytest.mark.scripted_adapter()


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
