"""Every git command of the suite ignores the git configuration of the machine (#58).

The fixture that isolates git is autouse, so a test cannot set the
configuration of the machine before it runs. The case therefore runs pytest in
a child process, with the conftest of the suite as a plugin and a global and a
system configuration that turn on `core.fsmonitor` and `commit.gpgsign`. The
inner test makes a repository and commits in it with no `-c` option, as a test
of the suite does.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# The configuration of a machine that would change a result: a daemon for each repository, a signature that
# needs a key (the program `false` makes each signature fail), and an identity that is not the identity of the suite.
MACHINE_CONFIG = """\
[core]
\tfsmonitor = true
[commit]
\tgpgsign = true
[gpg]
\tprogram = false
[user]
\tname = Machine Person
\temail = machine@example.invalid
"""

INNER_TEST = '''\
import subprocess


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def test_a_test_repository_sees_no_setting_of_the_machine(tmp_path):
    repo = tmp_path / "repo"
    assert git(tmp_path, "init", "-q", "-b", "main", str(repo)).returncode == 0

    commit = git(repo, "commit", "-q", "--allow-empty", "-m", "seed")

    assert commit.returncode == 0, commit.stderr
    assert git(repo, "config", "--get", "core.fsmonitor").stdout.strip() != "true"
    assert git(repo, "config", "--get", "commit.gpgsign").stdout.strip() != "true"
    assert git(repo, "log", "-1", "--format=%an <%ae> %cn <%ce>").stdout.strip() == (
        "Scratch <scratch@example.invalid> Scratch <scratch@example.invalid>"
    )
'''


def test_a_test_repository_sees_neither_the_global_nor_the_system_git_configuration_of_the_machine(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    machine = home / ".gitconfig"
    machine.write_text(MACHINE_CONFIG)
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / "pytest.ini").write_text("[pytest]\n")
    (inner / "test_inner.py").write_text(INNER_TEST)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        HOME=str(home), XDG_CONFIG_HOME=str(home / ".config"), GIT_CONFIG_GLOBAL=str(machine), GIT_CONFIG_SYSTEM=str(machine),
        PYTHONPATH=os.pathsep.join(filter(None, [str(ROOT), os.environ.get("PYTHONPATH")])),
    )
    # The machine itself, with no fixture: the configuration reaches git.
    assert subprocess.run(["git", "config", "--get", "commit.gpgsign"], capture_output=True, text=True, env=env).stdout.strip() == "true"

    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.conftest", "--rootdir", str(inner),
         "--basetemp", str(tmp_path / "inner-tmp"), str(inner)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300,
    )

    assert done.returncode == 0, done.stdout + done.stderr
    assert "1 passed" in done.stdout
