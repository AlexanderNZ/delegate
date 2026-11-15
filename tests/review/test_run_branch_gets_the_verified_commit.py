"""The run branch moves only to the commit that the verifier copy held (#348, review of the speed stack).

#348 gave the HEAD of the worktree from the gates to the verify phase. The verifier copy is made from the ticket branch,
so a specialist that left its worktree on another branch, with more commits, made the run branch move to work that no
verifier saw. The case calls `delegate run` against a real temporary git repository with the scripted adapter.
"""

from __future__ import annotations

import json

from ..support import git, make_repo, read_journal, run_main, valid_report


def test_the_run_branch_moves_only_to_the_tip_of_the_ticket_branch_that_the_verifier_copy_held(tmp_path, scripted, capsys):
    repo = make_repo(tmp_path)

    def side_branch(request) -> None:
        # The specialist commits on its branch and then moves its worktree to another branch. The adapter then commits
        # its files on that other branch, so the HEAD of the worktree holds a commit that the ticket branch does not hold.
        (request.cwd / "feature.txt").write_text("feature\n")
        git(request.cwd, "add", "-A", environment=request.environment)
        git(request.cwd, "commit", "-q", "-m", "work on the ticket branch", environment=request.environment)
        git(request.cwd, "checkout", "-q", "-b", "side", environment=request.environment)

    scripted.on_specialist = side_branch
    scripted.files = {"side.txt": "side\n"}
    scripted.report_text = lambda request, head: json.dumps(valid_report("a", "run/demo-a", head))

    code, out, _ = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    events = read_journal(out)

    branch_tip = git(repo, "rev-parse", "run/demo-a").strip()
    side_tip = git(repo, "rev-parse", "side").strip()
    assert side_tip != branch_tip, "the case needs a HEAD of the worktree that is not the tip of the ticket branch"
    assert git(repo, "rev-parse", "run/demo").strip() != side_tip
    starts = [e["commit"] for e in events if e["event"] == "verify-start"]
    advances = [e["to_commit"] for e in events if e["event"] == "run-branch-advance"]
    assert starts and set(starts) == {branch_tip}, (code, starts)
    assert set(advances) <= {branch_tip}, advances
