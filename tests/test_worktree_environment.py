"""The engine gives the settings of a worktree through the environment, and writes no `includeIf` section (#221).

Claude Code refuses a worktree-isolated agent spawn in a repository whose configuration holds an `includeIf` section.
So the port keeps `delegate.config` in the git directory of the worktree, and it gives the environment entries that
include the file for that worktree only. The cases call the port on real temporary repositories, and they run `delegate
run` with the stand-in harness and a gate command that record the entries.
"""

import os
import subprocess
from pathlib import Path

import pytest

from delegate import adapters
from delegate.adapters.git import GitVersionControl
from delegate.cli import delegate
from delegate.ports.vcs import VcsError

from . import git_processes
from .support import ScriptedAdapter, WORKFLOW, git, make_repo, state_dir

PUSH_REFUSED = "delegate: push refused"


@pytest.fixture(autouse=True)
def identity(git_identity):
    """A commit and a rebase need a committer."""


@pytest.fixture
def vcs() -> GitVersionControl:
    return GitVersionControl()


def run_git(directory: Path, *args: str, environment: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run git in `directory`, with the entries of `environment` added to the environment of the test, whatever the exit code is."""
    return subprocess.run(
        ["git", "-C", str(directory), *args], capture_output=True, text=True, env={**os.environ, **(environment or {})}
    )


def make_guarded_worktree(tmp_path: Path, vcs: GitVersionControl) -> tuple[Path, Path, Path]:
    """A repository with a bare remote, and a worktree whose hooks directory refuses each push. Return the repository, the remote and the worktree."""
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    hooks = tmp_path / "guard-hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text(f"#!/bin/sh\necho '{PUSH_REFUSED}' >&2\nexit 1\n")
    (hooks / "pre-push").chmod(0o755)
    vcs.use_hooks_directory(repo, worktree, hooks)
    return repo, remote, worktree


def include_sections(repo: Path) -> list[str]:
    """The names of the `includeIf` entries in the configuration of the repository."""
    result = run_git(repo, "config", "--local", "--name-only", "--get-regexp", r"^includeif\.")
    return result.stdout.split()


# The configuration of the repository.


def test_the_engine_writes_no_includeif_section_to_the_repository_after_it_installs_the_guard(tmp_path, vcs):
    repo, _, worktree = make_guarded_worktree(tmp_path, vcs)

    assert include_sections(repo) == []
    assert (worktree / ".git").is_file()
    assert (Path(git(worktree, "rev-parse", "--absolute-git-dir").strip()) / "delegate.config").is_file()


# The push guard through the environment.


def test_a_push_in_the_worktree_with_the_environment_of_the_port_fails_with_the_message_of_the_push_guard(tmp_path, vcs):
    repo, remote, worktree = make_guarded_worktree(tmp_path, vcs)
    entries = vcs.worktree_environment(worktree)

    refused = run_git(worktree, "push", "origin", "HEAD:refs/heads/stolen", environment=entries)

    assert refused.returncode != 0
    assert PUSH_REFUSED in refused.stderr
    assert git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").split() == []


def test_a_push_in_a_subdirectory_of_the_worktree_is_refused_too(tmp_path, vcs):
    repo, remote, worktree = make_guarded_worktree(tmp_path, vcs)
    (worktree / "sub").mkdir()

    refused = run_git(worktree / "sub", "push", "origin", "HEAD:refs/heads/stolen", environment=vcs.worktree_environment(worktree))

    assert refused.returncode != 0
    assert PUSH_REFUSED in refused.stderr


def test_a_push_from_the_main_checkout_runs_the_hooks_of_the_repository_with_the_same_environment(tmp_path, vcs):
    repo, remote, worktree = make_guarded_worktree(tmp_path, vcs)
    marker = tmp_path / "pre-push-ran"
    hook = repo / ".git" / "hooks" / "pre-push"
    hook.write_text(f"#!/bin/sh\necho \"$1\" >> '{marker}'\nexit 0\n")
    hook.chmod(0o755)

    pushed = run_git(repo, "push", "origin", "main", environment=vcs.worktree_environment(worktree))

    assert pushed.returncode == 0, pushed.stderr
    assert marker.read_text().split() == ["origin"]
    assert git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").split() == ["main"]


def test_a_git_repository_outside_the_worktree_reads_no_setting_of_the_include_file(tmp_path, vcs):
    repo, _, worktree = make_guarded_worktree(tmp_path, vcs)
    entries = vcs.worktree_environment(worktree)
    git(repo, "worktree", "add", "-q", "-b", "other", str(tmp_path / "other"), "main")
    stranger = make_repo(tmp_path / "stranger")

    for directory in (repo, tmp_path / "other", stranger):
        assert run_git(directory, "config", "--get", "core.hooksPath", environment=entries).returncode == 1, directory
    assert run_git(worktree, "config", "--get", "core.hooksPath", environment=entries).returncode == 0
    assert run_git(worktree, "config", "--get", "core.hooksPath").returncode == 1


def test_the_environment_entries_hold_for_a_worktree_in_a_path_with_a_wildcard(tmp_path, vcs):
    repo = make_repo(tmp_path / "odd[1]*?")
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    git(repo, "worktree", "add", "-q", "-b", "other", str(tmp_path / "other"), "main")
    hooks = tmp_path / "my-hooks"
    hooks.mkdir()
    vcs.use_hooks_directory(repo, worktree, hooks)
    entries = vcs.worktree_environment(worktree)

    assert Path(run_git(worktree, "config", "--get", "core.hooksPath", environment=entries).stdout.strip()) == hooks
    assert run_git(tmp_path / "other", "config", "--get", "core.hooksPath", environment=entries).returncode == 1
    assert run_git(repo, "config", "--get", "core.hooksPath", environment=entries).returncode == 1


def test_the_environment_entries_hold_for_a_worktree_whose_git_file_names_its_git_directory_with_a_relative_path(tmp_path, vcs):
    # The port reads the `.git` file of a worktree for its git directory (#239). Git writes a relative path there with
    # `--relative-paths`, and the entries must name the git directory that git itself finds.
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    git(repo, "worktree", "add", "-q", "--relative-paths", "-b", "feature", str(worktree), "main")
    git(repo, "worktree", "add", "-q", "-b", "other", str(tmp_path / "other"), "main")
    assert (worktree / ".git").read_text().startswith("gitdir: ../")
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    hooks = tmp_path / "my-hooks"
    hooks.mkdir()
    vcs.use_hooks_directory(repo, worktree, hooks)
    before = git_processes.all_started()
    entries = vcs.worktree_environment(worktree)

    assert git_processes.all_started() - before == 0  # the `.git` file gave the git directory, with no git process
    git_dir = Path(git(worktree, "rev-parse", "--absolute-git-dir").strip()).resolve()
    assert entries[f"GIT_CONFIG_VALUE_{int(entries['GIT_CONFIG_COUNT']) - 1}"] == str(git_dir / "delegate.config")
    assert Path(run_git(worktree, "config", "--get", "core.hooksPath", environment=entries).stdout.strip()) == hooks
    assert run_git(worktree, "config", "--get", "core.fsmonitor", environment=entries).stdout.strip() == "false"
    assert run_git(tmp_path / "other", "config", "--get", "core.hooksPath", environment=entries).returncode == 1


def test_the_entries_of_the_port_go_after_the_entries_that_the_environment_holds_and_these_stay_in_effect(tmp_path, vcs, monkeypatch):
    repo, _, worktree = make_guarded_worktree(tmp_path, vcs)
    count = int(os.environ["GIT_CONFIG_COUNT"])
    monkeypatch.setenv(f"GIT_CONFIG_KEY_{count}", "person.setting")
    monkeypatch.setenv(f"GIT_CONFIG_VALUE_{count}", "kept")
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(count + 1))

    entries = vcs.worktree_environment(worktree)

    assert set(entries) == {"GIT_CONFIG_COUNT", f"GIT_CONFIG_KEY_{count + 1}", f"GIT_CONFIG_VALUE_{count + 1}"}
    assert entries["GIT_CONFIG_COUNT"] == str(count + 2)
    assert run_git(worktree, "config", "--get", "person.setting", environment=entries).stdout.strip() == "kept"
    assert run_git(worktree, "config", "--get", "core.fsmonitor", environment=entries).stdout.strip() == "false"
    assert run_git(worktree, "config", "--get", "core.hooksPath", environment=entries).returncode == 0


def test_the_entries_of_the_port_start_at_the_first_index_when_the_environment_holds_none(tmp_path, vcs, monkeypatch):
    repo, _, worktree = make_guarded_worktree(tmp_path, vcs)
    for name in [name for name in os.environ if name.startswith("GIT_CONFIG_KEY_") or name.startswith("GIT_CONFIG_VALUE_")]:
        monkeypatch.delenv(name)
    monkeypatch.delenv("GIT_CONFIG_COUNT")

    entries = vcs.worktree_environment(worktree)

    assert set(entries) == {"GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"}
    assert entries["GIT_CONFIG_COUNT"] == "1"
    assert run_git(worktree, "config", "--get", "core.hooksPath", environment=entries).returncode == 0


@pytest.mark.parametrize("count", ["many", "-1", ""])
def test_a_count_in_the_environment_that_is_no_number_is_an_error(tmp_path, vcs, monkeypatch, count):
    repo, _, worktree = make_guarded_worktree(tmp_path, vcs)
    monkeypatch.setenv("GIT_CONFIG_COUNT", count)

    with pytest.raises(VcsError, match="GIT_CONFIG_COUNT"):
        vcs.worktree_environment(worktree)


# The settings that an earlier version wrote.


def write_earlier_section(repo: Path, worktree: Path) -> None:
    """Write the section that an earlier version wrote for `worktree` to the configuration of the repository."""
    git_dir = Path(git(worktree, "rev-parse", "--absolute-git-dir").strip()).resolve()
    git(repo, "config", "--local", f"includeIf.gitdir:{git_dir}.path", str(git_dir / "delegate.config"))


def test_ensure_worktree_removes_the_section_of_an_earlier_version_also_when_its_file_exists_and_keeps_a_section_of_a_person(
    tmp_path, vcs
):
    repo, _, old = make_guarded_worktree(tmp_path, vcs)
    write_earlier_section(repo, old)
    person = tmp_path / "person.config"
    person.write_text("[person]\n\tsetting = kept\n")
    git(repo, "config", "includeIf.gitdir:/no/such/place/.path", str(person))
    git(repo, "config", "include.path", str(tmp_path / "missing.config"))
    # A person points an include at a file of the same name outside the worktrees of the repository.
    git(repo, "config", "includeIf.gitdir:/other/place/.path", str(tmp_path / "delegate.config"))
    assert len(include_sections(repo)) == 3

    vcs.ensure_worktree(repo, tmp_path / "next", "next", "main")

    assert sorted(include_sections(repo)) == ["includeif.gitdir:/no/such/place/.path", "includeif.gitdir:/other/place/.path"]
    assert run_git(repo, "config", "--local", "--get", "include.path").returncode == 0
    # The old worktree keeps its file, so a process that has the entries of the port still holds the guard.
    refused = run_git(old, "push", "origin", "HEAD:refs/heads/stolen", environment=vcs.worktree_environment(old))
    assert PUSH_REFUSED in refused.stderr


def test_remove_worktree_removes_the_section_of_an_earlier_version_of_another_worktree_that_is_still_there(tmp_path, vcs):
    repo, _, old = make_guarded_worktree(tmp_path, vcs)
    write_earlier_section(repo, old)
    other = tmp_path / "other"
    vcs.ensure_worktree(repo, other, "other", "main")

    vcs.remove_worktree(repo, other)

    assert include_sections(repo) == []


def test_a_section_of_an_earlier_version_for_a_worktree_that_a_person_removed_goes_with_the_prune(tmp_path, vcs):
    repo, _, old = make_guarded_worktree(tmp_path, vcs)
    write_earlier_section(repo, old)
    person = tmp_path / "person.config"
    person.write_text("[person]\n\tsetting = kept\n")
    git(repo, "config", "includeIf.gitdir:/no/such/place/.path", str(person))
    subprocess.run(["rm", "-rf", str(old)], check=True)

    vcs.prune_worktrees(repo)

    assert include_sections(repo) == ["includeif.gitdir:/no/such/place/.path"]


# The file watcher.


def test_the_git_commands_of_the_port_in_a_worktree_start_no_daemon_when_the_user_turns_fsmonitor_on(tmp_path, vcs, monkeypatch):
    from .test_fsmonitor import GLOBAL_CONFIG, daemon_pid, why_no_daemon_can_start

    reason = why_no_daemon_can_start()
    if reason is not None:
        pytest.skip(reason)
    repo = make_repo(tmp_path)
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    (tmp_path / "global-gitconfig").write_text(GLOBAL_CONFIG)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "global-gitconfig"))
    monkeypatch.delenv("GIT_CONFIG_COUNT")
    try:
        assert run_git(worktree, "config", "core.fsmonitor").stdout.strip() == "true"
        (worktree / "x.txt").write_text("x\n")

        assert [change.path for change in vcs.uncommitted_changes(worktree)] == ["x.txt"]
        vcs.head_commit(worktree)
        vcs.changed_paths(worktree, "main")

        assert daemon_pid(worktree) is None
    finally:
        vcs.stop_file_watcher(worktree)


# The engine passes the entries to each process of a ticket.


@pytest.fixture
def registered():
    adapter = ScriptedAdapter()
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def run(repo: Path, capsys) -> tuple[int, str, str]:
    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo)])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_the_stand_in_harness_and_a_gate_command_record_the_entries_for_the_worktree_of_their_ticket(tmp_path, capsys, registered):
    marker = tmp_path / "gate-environment"
    count = int(os.environ["GIT_CONFIG_COUNT"])
    gate = f"printenv GIT_CONFIG_COUNT GIT_CONFIG_KEY_{count} GIT_CONFIG_VALUE_{count} > '{marker}'; test -f feature.txt"
    repo = make_repo(tmp_path, WORKFLOW.replace('"test -f feature.txt"', f'"{gate}"'))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    specialist, verifier = registered.calls[0], registered.verifier_calls[0]
    worktree = specialist.cwd
    entries = GitVersionControl().worktree_environment(worktree)
    # The engine also sets `TMPDIR` to the scratch directory of the session (#227).
    assert {name: value for name, value in specialist.environment.items() if name != "TMPDIR"} == entries
    assert {name: value for name, value in verifier.environment.items() if name != "TMPDIR"} == entries
    assert entries["GIT_CONFIG_COUNT"] == str(count + 1)
    assert marker.read_text().split("\n")[:-1] == [entries[name] for name in ("GIT_CONFIG_COUNT", f"GIT_CONFIG_KEY_{count}", f"GIT_CONFIG_VALUE_{count}")]
    assert include_sections(repo) == []


def test_a_push_from_the_engine_worktree_with_the_entries_of_the_request_is_refused_and_the_repository_has_no_include_section(
    tmp_path, capsys, registered
):
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    seen: list[object] = []

    def try_to_push(request):
        seen.append(include_sections(repo))
        pushed = run_git(request.cwd, "push", "origin", "HEAD:refs/heads/stolen", environment=dict(request.environment))
        seen.append((pushed.returncode, pushed.stderr))

    registered.on_specialist = try_to_push

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert seen[0] == []
    assert seen[1][0] != 0 and "push refused" in seen[1][1]
    assert git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").split() == []


def test_a_run_in_a_repository_with_a_worktree_of_an_earlier_version_removes_its_section_and_the_worktree_keeps_the_guard_through_the_environment(
    tmp_path, capsys, registered
):
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    vcs = GitVersionControl()
    old = state_dir(repo).resolve() / "worktrees" / "old-run" / "a"
    vcs.ensure_worktree(repo, old, "run/old-a", "main")
    hooks = tmp_path / "old-hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text(f"#!/bin/sh\necho '{PUSH_REFUSED}' >&2\nexit 1\n")
    (hooks / "pre-push").chmod(0o755)
    vcs.use_hooks_directory(repo, old, hooks)
    write_earlier_section(repo, old)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert include_sections(repo) == []
    refused = run_git(old, "push", "origin", "HEAD:refs/heads/stolen", environment=vcs.worktree_environment(old))
    assert PUSH_REFUSED in refused.stderr
