"""The engine guards each worktree that it makes: no push, and no change to a hotspot path.

Each case uses a real git repository and a local bare remote, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter tries
the push from inside the worktree, as a specialist in any harness could.
"""

import subprocess

import pytest

from agent_definitions import adapters, delegate

from .support import ScriptedAdapter, event_names, git, make_repo, read_journal


@pytest.fixture(autouse=True)
def identity(git_identity):
    """The rebase and the scripted commits need a committer."""


def run(repo, capsys):
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def make_repo_with_remote(tmp_path):
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    return repo, remote


def remote_branches(remote):
    return git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()


@pytest.fixture
def registered():
    registered_names = []

    def register(adapter):
        adapters.register("scripted", adapter)
        registered_names.append("scripted")
        return adapter

    yield register
    for name in registered_names:
        adapters.unregister(name)


def test_a_push_from_an_engine_worktree_is_refused_and_a_push_from_the_main_checkout_is_not(tmp_path, capsys, registered):
    repo, remote = make_repo_with_remote(tmp_path)
    attempts = []

    def try_to_push(request):
        r = subprocess.run(
            ["git", "-C", str(request.cwd), "push", "origin", "HEAD:refs/heads/stolen"], capture_output=True, text=True
        )
        attempts.append((r.returncode, r.stderr))

    registered(ScriptedAdapter(on_specialist=try_to_push))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (returncode, stderr) = attempts[0]
    assert returncode != 0
    assert "push" in stderr and "refused" in stderr
    assert remote_branches(remote) == []
    # The main checkout keeps its own hooks: its push goes through.
    main_push = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True)
    assert main_push.returncode == 0, main_push.stderr
    assert remote_branches(remote) == ["main"]


def test_the_repository_hooks_still_run_for_a_commit_in_an_engine_worktree(tmp_path, capsys, registered):
    repo, _ = make_repo_with_remote(tmp_path)
    hook = repo / ".git" / "hooks" / "post-commit"
    marker = tmp_path / "post-commit-ran"
    hook.write_text(f"#!/bin/sh\necho \"$PWD\" >> '{marker}'\n")
    hook.chmod(0o755)
    registered(ScriptedAdapter())

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (worktree,) = marker.read_text().split()
    assert worktree.endswith("/worktrees/" + next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"] + "/a")

