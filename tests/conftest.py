import os
from pathlib import Path

import pytest

from delegate.definitions.declaration import load_declaration
from delegate.definitions.render import render
from delegate.shared.tiers import load_tiers

from . import git_processes

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "java-spring.toml"


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


# The name of the count of `git` processes that the test code of one test starts (see `tests/git_processes.py`).
GIT_PROCESSES = "git processes of the test code"

_first_count: int = 0


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """Note the count before the setup of the test, so the count of the test holds its fixtures too."""
    global _first_count
    _first_count = git_processes.started()
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Give the report of the teardown the count of `git` processes that the test code of the test started."""
    outcome = yield
    if call.when == "teardown":
        outcome.get_result().user_properties.append((GIT_PROCESSES, git_processes.started() - _first_count))


def pytest_terminal_summary(terminalreporter):
    """Write the sum of the counts, also from each worker of `pytest -n`."""
    counts = [
        value
        for reports in terminalreporter.stats.values()
        for report in reports
        if getattr(report, "when", None) == "teardown"
        for name, value in getattr(report, "user_properties", ())
        if name == GIT_PROCESSES
    ]
    if counts:
        terminalreporter.write_line(f"{GIT_PROCESSES}: {sum(counts)} in {len(counts)} tests")
