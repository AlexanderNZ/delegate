"""`delegate` is the umbrella command over the standalone commands.

Each case runs the standalone entry point and the `delegate` entry point with
the same arguments, and compares the exit code, stdout, stderr and the files
written. The expected values are the standalone command's own behaviour, and
each case also pins one literal from the specification of the command, so a
pair of commands that fail alike cannot pass.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from agent_definitions import brief, cli, delegate
from tests.conftest import EXAMPLE

DOMAIN = "acme-api is a Spring Boot service: controllers, JPA repositories, and Flyway migrations."


def invoke(entry, argv, capsys):
    """Run one entry point. Return its exit code, stdout and stderr.

    argparse ends a run with SystemExit for `--help` and for a usage error, so
    the helper reads that as the exit code.
    """
    try:
        code = entry(argv)
    except SystemExit as stop:
        code = stop.code
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def snapshot(root: Path) -> dict[str, str]:
    """Every file under root, by relative path, with its text."""
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=Scratch", "-c", "user.email=scratch@example.invalid",
         "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
        env={"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null", "PATH": shutil.os.environ["PATH"]},
    )
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def test_render_gives_the_standalone_exit_code_output_and_files(tmp_path, capsys):
    argv = ["render", str(EXAMPLE), "-o", str(tmp_path / "out")]
    standalone = invoke(cli.main, argv, capsys)
    standalone_files = snapshot(tmp_path / "out")
    shutil.rmtree(tmp_path / "out")

    umbrella = invoke(delegate.main, argv, capsys)

    assert umbrella == standalone
    assert snapshot(tmp_path / "out") == standalone_files
    code, out, _err = umbrella
    assert code == 0
    assert "claude-code/java-spring-specialist.md" in out
    assert "opencode/java-spring-verifier.md" in out


def test_validate_gives_the_standalone_result_for_a_clean_and_a_broken_render(tmp_path, capsys):
    cli.main(["render", str(EXAMPLE), "-o", str(tmp_path)])
    capsys.readouterr()

    clean = invoke(delegate.main, ["validate", str(tmp_path)], capsys)
    assert clean == invoke(cli.main, ["validate", str(tmp_path)], capsys)
    assert clean[0] == 0
    assert clean[1].strip().endswith("ok")

    (tmp_path / "claude-code" / "java-spring-verifier.md").unlink()
    broken = invoke(delegate.main, ["validate", str(tmp_path)], capsys)
    assert broken == invoke(cli.main, ["validate", str(tmp_path)], capsys)
    assert broken[0] == 1
    assert "MISSING_TWIN" in broken[1]


def test_validate_with_nothing_to_check_exits_two_like_the_standalone_command(capsys):
    umbrella = invoke(delegate.main, ["validate"], capsys)
    assert umbrella == invoke(cli.main, ["validate"], capsys)
    assert umbrella[0] == 2


def test_the_tiers_option_before_the_subcommand_reaches_the_standalone_command(tmp_path, capsys):
    missing = str(tmp_path / "no-such-tiers.toml")
    argv = ["--tiers", missing, "render", str(EXAMPLE), "-o", str(tmp_path / "out")]
    standalone = invoke_expecting_error(cli.main, argv, capsys)
    umbrella = invoke_expecting_error(delegate.main, argv, capsys)
    assert umbrella == standalone
    assert "no-such-tiers.toml" in umbrella[1]


def invoke_expecting_error(entry, argv, capsys):
    """A bad tier file is a loud failure. Return the exception type and text."""
    with pytest.raises(Exception) as caught:
        entry(argv)
    capsys.readouterr()
    return type(caught.value).__name__, str(caught.value)


@pytest.fixture
def repo(tmp_path):
    """A git repository that bootstrap can write into."""
    r = tmp_path / "acme-api"
    (r / "docs").mkdir(parents=True)
    (r / "docs" / "gates.md").write_text("# Gates\nrun the suite\n")
    d = r / ".claude" / "skills" / "tdd"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: tdd\ndescription: The tdd skill.\n---\n")
    (r / "prompt.md").write_text("Prove every change with the suite. Never push.\n")
    git(r, "init", "-q")
    return r


def bootstrap_argv(repo: Path, *extra: str) -> list[str]:
    return [
        "bootstrap",
        "--repo", str(repo),
        "--name", "acme-api",
        "--domain", DOMAIN,
        "--tier", "standard",
        "--skill", "tdd",
        "--reference", "`docs/gates.md` — the gates",
        "--prompt-file", str(repo / "prompt.md"),
        *extra,
    ]


def test_bootstrap_dry_run_prints_what_the_standalone_command_prints(repo, capsys):
    argv = bootstrap_argv(repo, "--dry-run")
    standalone = invoke(cli.main, argv, capsys)
    umbrella = invoke(delegate.main, argv, capsys)
    assert umbrella == standalone
    assert umbrella[0] == 0
    assert "acme-api-context" in umbrella[1]
    assert not (repo / "docs" / "agents").exists(), "a dry run writes nothing"


def test_bootstrap_writes_the_files_the_standalone_command_writes(repo, tmp_path, capsys):
    # The written files hold the repository path, so both runs use the same
    # path: the second run starts from a copy of the untouched repository.
    pristine = tmp_path / "pristine"
    shutil.copytree(repo, pristine)

    standalone = invoke(cli.main, bootstrap_argv(repo), capsys)
    standalone_files = snapshot(repo)
    shutil.rmtree(repo)
    shutil.copytree(pristine, repo)
    umbrella = invoke(delegate.main, bootstrap_argv(repo), capsys)

    assert umbrella == standalone
    assert umbrella[0] == 0
    assert snapshot(repo) == standalone_files
    assert any(name.endswith("acme-api-context/SKILL.md") for name in standalone_files)
