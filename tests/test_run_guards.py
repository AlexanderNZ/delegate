"""The engine guards each worktree that it makes: no push, and no change to a hotspot path.

Each case uses a real git repository and a local bare remote, registers a
scripted adapter by name, and calls `delegate.main`. The scripted adapter tries
the push from inside the worktree, as a specialist in any harness could.
"""

import subprocess

import pytest

from delegate import adapters
from delegate.cli import delegate

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


def worktree_extension(repo):
    """The result of `git config --get extensions.worktreeConfig` in `repo`: the exit code and the output."""
    r = subprocess.run(["git", "-C", str(repo), "config", "--get", "extensions.worktreeConfig"], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


def test_a_run_sets_no_worktree_extension_in_the_repository_while_the_push_guard_holds(tmp_path, capsys, registered):
    repo, remote = make_repo_with_remote(tmp_path)
    seen = []

    def push_and_read_the_extension(request):
        seen.append(worktree_extension(repo))
        r = subprocess.run(
            ["git", "-C", str(request.cwd), "push", "origin", "HEAD:refs/heads/stolen"], capture_output=True, text=True
        )
        seen.append(r.returncode)

    registered(ScriptedAdapter(on_specialist=push_and_read_the_extension))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    extension_during_the_run, push_returncode = seen[0], seen[1]
    assert extension_during_the_run == (1, "")
    assert worktree_extension(repo) == (1, "")
    assert push_returncode != 0
    assert remote_branches(remote) == []


def test_the_pre_push_hook_of_the_repository_still_runs_for_a_push_from_the_main_checkout(tmp_path, capsys, registered):
    repo, remote = make_repo_with_remote(tmp_path)
    marker = tmp_path / "pre-push-ran"
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\necho \"$1\" >> '{marker}'\nexit 0\n")
    hook.chmod(0o755)
    attempts = []

    def try_to_push(request):
        r = subprocess.run(
            ["git", "-C", str(request.cwd), "push", "origin", "HEAD:refs/heads/stolen"], capture_output=True, text=True
        )
        attempts.append(r.returncode)

    registered(ScriptedAdapter(on_specialist=try_to_push))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert attempts[0] != 0
    assert not marker.exists()
    main_push = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True)
    assert main_push.returncode == 0, main_push.stderr
    assert marker.read_text().split() == ["origin"]
    assert remote_branches(remote) == ["main"]


def leave_an_earlier_run(repo):
    """Leave the state that an earlier version of the engine left: a worktree with its settings in `config.worktree`.

    That version set `extensions.worktreeConfig` in the repository, and gave its
    worktree `core.fsmonitor=false` and a `core.hooksPath` whose `pre-push` hook
    refuses each push. Return the old worktree.
    """
    state = (repo / ".git" / "delegate").resolve()
    worktree = state / "worktrees" / "old-run" / "a"
    hooks = state / "hooks" / "old-run" / "a"
    hooks.mkdir(parents=True)
    (hooks / "pre-push").write_text("#!/bin/sh\necho 'delegate: push refused' >&2\nexit 1\n")
    (hooks / "pre-push").chmod(0o755)
    git(repo, "worktree", "add", "-q", "-b", "run/old-a", str(worktree), "main")
    git(repo, "config", "extensions.worktreeConfig", "true")
    git(worktree, "config", "--worktree", "core.fsmonitor", "false")
    git(worktree, "config", "--worktree", "core.hooksPath", str(hooks))
    return worktree


def test_a_run_removes_the_worktree_extension_that_an_earlier_run_set_and_the_old_worktree_still_refuses_a_push(
    tmp_path, capsys, registered
):
    repo, remote = make_repo_with_remote(tmp_path)
    old = leave_an_earlier_run(repo)
    registered(ScriptedAdapter())

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert worktree_extension(repo) == (1, "")
    refused = subprocess.run(["git", "-C", str(old), "push", "origin", "HEAD:refs/heads/stolen"], capture_output=True, text=True)
    assert refused.returncode != 0
    assert "push refused" in refused.stderr
    assert remote_branches(remote) == []
    assert git(old, "config", "--get", "core.fsmonitor").strip() == "false"
    main_push = subprocess.run(["git", "-C", str(repo), "push", "origin", "main"], capture_output=True, text=True)
    assert main_push.returncode == 0, main_push.stderr


def test_a_run_keeps_the_worktree_extension_when_a_setting_that_a_person_made_needs_it(tmp_path, capsys, registered):
    repo, _ = make_repo_with_remote(tmp_path)
    leave_an_earlier_run(repo)
    mine = tmp_path / "my-worktree"
    git(repo, "worktree", "add", "-q", "-b", "mine", str(mine), "main")
    git(mine, "config", "--worktree", "person.setting", "kept")
    registered(ScriptedAdapter())

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert worktree_extension(repo) == (0, "true")
    assert git(mine, "config", "--get", "person.setting").strip() == "kept"


def test_a_run_keeps_a_worktree_extension_that_a_person_set_in_a_repository_with_no_earlier_run(tmp_path, capsys, registered):
    repo, _ = make_repo_with_remote(tmp_path)
    git(repo, "config", "extensions.worktreeConfig", "true")
    registered(ScriptedAdapter())

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert worktree_extension(repo) == (0, "true")


def hotspot_workflow(*, mode="assure", hotspots='["LICENSE", ".github/workflows/*"]'):
    from .support import WORKFLOW

    return WORKFLOW.replace('mode = "assure"', f'mode = "{mode}"').replace(
        'hotspots = ["LICENSE", ".github/workflows/*"]', f"hotspots = {hotspots}"
    )


def make_parents(request):
    for name in ("LICENSE", ".github/workflows/ci.yml", "docs/agents/delegation.md"):
        (request.cwd / name).parent.mkdir(parents=True, exist_ok=True)


@pytest.mark.parametrize(
    "path, hotspots",
    [
        pytest.param("LICENSE", '["LICENSE"]', id="exact-file"),
        pytest.param(".github/workflows/ci.yml", '[".github/workflows/*"]', id="glob"),
        pytest.param("docs/agents/delegation.md", '["docs/agents/"]', id="directory-with-slash"),
        pytest.param("docs/agents/delegation.md", '["docs/agents"]', id="directory-without-slash"),
    ],
)
@pytest.mark.parametrize("mode", ["assure", "economy"])
def test_a_specialist_that_changes_a_hotspot_path_fails_the_step_naming_the_path_and_no_verifier_runs(
    tmp_path, capsys, registered, path, hotspots, mode
):
    repo = make_repo(tmp_path, hotspot_workflow(mode=mode, hotspots=hotspots))
    adapter = registered(ScriptedAdapter(files={"feature.txt": "feature\n", path: "changed\n"}, on_specialist=make_parents))
    base_tip = git(repo, "rev-parse", "main").strip()

    code, out, err = run(repo, capsys)

    events = read_journal(out)
    assert code == 1
    assert f"delegate run: ticket a: hotspot finding: {path} matches the hotspot" in err
    (finding,) = [e for e in events if e["event"] == "hotspot-finding"]
    assert (finding["ticket"], finding["phase"], finding["round"]) == ("a", "build", 0)
    assert [m["path"] for m in finding["matches"]] == [path]
    assert adapter.verifier_calls == []
    assert "verify-start" not in event_names(events)
    assert next(e for e in events if e["event"] == "step-end")["state"] == "failed"
    assert git(repo, "rev-parse", "run/demo").strip() == base_tip


def test_a_change_outside_the_hotspots_continues_to_the_verifier_even_beside_a_path_that_only_looks_like_a_hotspot(
    tmp_path, capsys, registered
):
    repo = make_repo(tmp_path, hotspot_workflow())
    adapter = registered(ScriptedAdapter(files={"feature.txt": "feature\n", "LICENSE.md": "notes\n", "NOTICE": "n\n"}))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert len(adapter.verifier_calls) == 1
    assert "hotspot-finding" not in event_names(read_journal(out))


def test_a_fixup_that_changes_a_hotspot_path_fails_the_step_and_no_second_verifier_runs(tmp_path, capsys, registered):
    repo = make_repo(tmp_path, hotspot_workflow())
    adapter = registered(
        ScriptedAdapter(verdict="REJECT", fixup_files={"LICENSE": "changed\n"}, on_specialist=make_parents)
    )

    code, out, err = run(repo, capsys)

    events = read_journal(out)
    assert code == 1
    assert "delegate run: ticket a: hotspot finding: LICENSE matches the hotspot" in err
    (finding,) = [e for e in events if e["event"] == "hotspot-finding"]
    assert (finding["phase"], finding["round"]) == ("fixup", 1)
    assert len(adapter.verifier_calls) == 1
