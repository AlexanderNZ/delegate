import os
import subprocess
from pathlib import Path

import pytest

from delegate.adapters.git import GitVersionControl
from delegate.definitions.declaration import load_declaration
from delegate.definitions.render import render
from delegate.shared.tiers import load_tiers

from . import git_processes

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "java-spring.toml"

# The `src` of the tree that the tests run in. The `pythonpath` setting of pyproject.toml puts it on the path of the
# pytest process only (#170).
SOURCE = Path(__file__).resolve().parents[1] / "src"


def pytest_configure(config):
    """Put the `src` of this tree first on PYTHONPATH, so each Python process that a test starts imports the same code.

    The dev shell sets PYTHONPATH only in a git checkout. A copy with no `.git` entry, as the proof copy of a verifier
    (#153), has no PYTHONPATH from it, so `python -m delegate...` in a test did not find the package (#170).
    """
    config.addinivalue_line(
        "markers",
        "kit_repository: the test looks up the repository of the kit itself through the version control port (#285); say why in the test",
    )
    entries = [entry for entry in os.environ.get("PYTHONPATH", "").split(os.pathsep) if entry]
    if entries[:1] != [str(SOURCE)]:
        os.environ["PYTHONPATH"] = os.pathsep.join([str(SOURCE), *entries])


# The identity of every commit that a test makes. It comes from the environment, so no test needs the git
# configuration of the machine for it.
IDENTITY: dict[str, str] = {"NAME": "Scratch", "EMAIL": "scratch@example.invalid"}


@pytest.fixture(scope="session")
def empty_git_config(tmp_path_factory) -> Path:
    """An empty file in a temporary directory, for the global and the system git configuration of the suite."""
    path = tmp_path_factory.mktemp("git-config") / "empty-gitconfig"
    path.write_text("")
    return path


@pytest.fixture(autouse=True)
def isolated_git_config(monkeypatch, empty_git_config):
    """Keep the git configuration of the machine out of every test.

    `GIT_CONFIG_GLOBAL` and `GIT_CONFIG_SYSTEM` name an empty file, and
    `GIT_CONFIG_NOSYSTEM` is set, so git reads no configuration file of the
    machine. The author and the committer come from the environment.

    A global `core.hooksPath` can name a `post-rewrite` hook that does not read
    its stdin. Git then dies of SIGPIPE (exit 141) after a finished rebase, and a
    test of the run fails for a cause outside the code. A global
    `commit.gpgsign` or `init.defaultBranch` can also change a result, and each
    hook or signature is one more process start. Setting the environment of the
    process is the boundary; the test repositories hold all they need.

    The same fixture turns `core.fsmonitor` off for every git command of the
    suite. With the setting on, git starts a `git fsmonitor--daemon` for each of
    the hundreds of temporary repositories, and each daemon outlives its
    directory. The fixture also turns `maintenance.auto` off: with it on, each
    commit, merge and rebase starts one more process, `git maintenance run
    --auto`. The entries go in the git configuration environment, after any
    entry that is already there, so a test can still set its own value after them.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty_git_config))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty_git_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for key, value in IDENTITY.items():
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
    count = int(os.environ.get("GIT_CONFIG_COUNT", "0"))
    for key in ("core.fsmonitor", "maintenance.auto"):
        monkeypatch.setenv(f"GIT_CONFIG_KEY_{count}", key)
        monkeypatch.setenv(f"GIT_CONFIG_VALUE_{count}", "false")
        count += 1
    monkeypatch.setenv("GIT_CONFIG_COUNT", str(count))


@pytest.fixture(autouse=True)
def isolated_state_home(tmp_path_factory, monkeypatch) -> Path:
    """Keep the state of the runs of a test out of the home of the machine (#200).

    The default state root is `$XDG_STATE_HOME/delegate`. Each test gets its own value, so no test writes under the
    real home, and `DELEGATE_STATE_ROOT` of the machine takes no part in a test. A test that needs another root sets it.
    """
    path = tmp_path_factory.mktemp("state-home")
    monkeypatch.setenv("XDG_STATE_HOME", str(path))
    monkeypatch.delenv("DELEGATE_STATE_ROOT", raising=False)
    return path


def _kit_paths() -> tuple[Path, Path] | None:
    """The top level and the git common directory of the repository that holds this tree, or None when the tree is no repository.

    A copy with no `.git` entry (the proof copy of a verifier, #153) has no repository of its own: the guard of
    `no_run_against_the_kit` then has nothing to guard.
    """
    result = subprocess.run(
        ["git", "-C", str(SOURCE.parent), "rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    top, common = result.stdout.splitlines()
    return Path(top).resolve(), Path(common).resolve()


KIT_PATHS = _kit_paths()


@pytest.fixture(autouse=True)
def no_run_against_the_kit(monkeypatch, request):
    """Fail a test whose code looks up the paths of the repository of the kit itself (#285).

    Each command of the engine finds its repository through `repository_paths` of the version control port: the state
    directory, the branches and the worktrees all follow from the paths that it returns. A test that calls `delegate run`
    with no `--repo` in the checkout of the kit looked up the kit, so it made branches and a worktree there and found the
    state root that the kit records. The check covers the top level and the git common directory, so each worktree of the
    kit counts. A test that reads a file of the kit by path does not look up the repository, so it is not affected. A test
    that needs the repository of the kit has `@pytest.mark.kit_repository` and says why.
    """
    if KIT_PATHS is None or request.node.get_closest_marker("kit_repository"):
        return
    real = GitVersionControl.repository_paths

    def guarded(self, path: Path) -> tuple[Path, Path]:
        top, common = real(self, path)
        if top.resolve() == KIT_PATHS[0] or common.resolve() == KIT_PATHS[1]:
            pytest.fail(
                f"{request.node.nodeid} looked up the repository of the kit itself ({KIT_PATHS[0]}) from {path}. "
                "A test of the engine must pass --repo with a test repository (a real temporary git repository), so no "
                "branch, worktree or state directory of the kit is made. If the test needs the kit, mark it "
                "@pytest.mark.kit_repository and say why.",
                pytrace=False,
            )
        return top, common

    monkeypatch.setattr(GitVersionControl, "repository_paths", guarded)


@pytest.fixture
def home(tmp_path_factory, monkeypatch) -> Path:
    """A user directory that holds nothing, so no agent file of the machine takes part in a case (#182).

    `delegate run --dry-run` looks for the agent files of each stack in the user directory too. A test that calls it
    uses this fixture, so the result does not depend on the machine that runs the test.
    """
    path = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(path))
    return path


@pytest.fixture
def claude_stand_in(tmp_path_factory, monkeypatch):
    """A `claude` command on PATH that replays a recorded stream, and answers a guard probe (#219) with a denial.

    The dry run of a workflow for the `claude-code` adapter starts the guard probe of each verifier, so a test of it
    needs the stand-in at the boundary of the harness. The directory is not the `tmp_path` of the test, so the files
    that the stand-in writes are not files that a test of the disk state finds.
    """
    from . import claude_fake

    return claude_fake.install(tmp_path_factory.mktemp("claude-stand-in"), monkeypatch, "finished.jsonl")


@pytest.fixture(scope="session")
def tiers():
    return load_tiers()


@pytest.fixture
def java_spring(tiers):
    return load_declaration(EXAMPLE)


@pytest.fixture
def rendered(tmp_path, java_spring, tiers):
    """Render the example to disk: tmp/claude-code/*.md and tmp/opencode/*.md."""
    out = render(java_spring, tiers)
    for harness, files in out.items():
        d = tmp_path / harness
        d.mkdir()
        for name, content in files.items():
            (d / name).write_text(content)
    return tmp_path


@pytest.fixture
def git_identity(monkeypatch):
    """The rebase writes commits, so git needs a committer. This sets the environment of the process, which is the real boundary."""
    for key, value in (("NAME", "Scratch"), ("EMAIL", "scratch@example.invalid")):
        monkeypatch.setenv(f"GIT_COMMITTER_{key}", value)
        monkeypatch.setenv(f"GIT_AUTHOR_{key}", value)


@pytest.fixture
def scripted(git_identity):
    """A scripted adapter registered as `scripted`. Each ticket a to d writes its own file."""
    from delegate import adapters

    from .support import ScriptedAdapter

    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abcd"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


# The environment variables of a host that change how the engine works: where it keeps its state (#97), and what
# each agent gets (#96). A host that sets them for its own runs must not change what a test sees, so each test
# starts without them.
HOST_VARIABLES: tuple[str, ...] = ("DELEGATE_STATE_ROOT", "DELEGATE_AGENT_SETTINGS", "DELEGATE_PERMISSION_MODE")


@pytest.fixture(autouse=True)
def no_host_settings(monkeypatch):
    """Remove the host variables of the engine from the environment of every test."""
    for name in HOST_VARIABLES:
        monkeypatch.delenv(name, raising=False)


# The names of the counts of one test: the processes that the test process starts, and the `git` processes that its
# test code starts (see `tests/git_processes.py`).
ALL_PROCESSES = "processes that the tests start"
GIT_PROCESSES = "git processes of the test code"

_first_counts: tuple[int, int] = (0, 0)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Note the counts before the setup of the test, so the counts of the test hold its fixtures too."""
    global _first_counts
    _first_counts = (git_processes.all_started(), git_processes.started())
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Give the report of the teardown the counts of the processes that the test started."""
    outcome = yield
    if call.when == "teardown":
        properties = outcome.get_result().user_properties
        properties.append((ALL_PROCESSES, git_processes.all_started() - _first_counts[0]))
        properties.append((GIT_PROCESSES, git_processes.started() - _first_counts[1]))


def pytest_terminal_summary(terminalreporter):
    """Write the sum of each count, also from each worker of `pytest -n`."""
    for count in (ALL_PROCESSES, GIT_PROCESSES):
        values = [
            value
            for reports in terminalreporter.stats.values()
            for report in reports
            if getattr(report, "when", None) == "teardown"
            for name, value in getattr(report, "user_properties", ())
            if name == count
        ]
        if values:
            terminalreporter.write_line(f"{count}: {sum(values)} in {len(values)} tests")


@pytest.fixture
def claude_version(tmp_path, monkeypatch):
    """A `claude` command on PATH that gives the version 2.1.287 to `--version`, as the seal of the Claude Code adapter asks (#217)."""
    from . import claude_fake

    claude_fake.install_version(tmp_path, monkeypatch)
