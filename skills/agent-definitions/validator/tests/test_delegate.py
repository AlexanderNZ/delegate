"""`delegate` is the umbrella command over the standalone commands.

Each case runs the standalone entry point and the `delegate` entry point with
the same arguments, and compares the exit code, stdout, stderr and the files
written. The expected values are the standalone command's own behaviour, and
each case also pins one literal from the specification of the command, so a
pair of commands that fail alike cannot pass.
"""

import importlib
import shutil
import subprocess
import sys
import tomllib
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


@pytest.fixture
def scratch(tmp_path):
    """A repository with a base commit, a rejected commit and a fix-up commit."""
    r = tmp_path / "scratch"
    (r / "docs" / "agents").mkdir(parents=True)
    git(r, "init", "-q", "-b", "main")
    (r / "docs" / "agents" / "delegation.md").write_text(
        "# Rules\n\n## Verification gates\n\n```\nmake check\n```\n\n## Hotspots\n"
    )
    (r / "README.md").write_text("base\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "base")
    git(r, "checkout", "-q", "-b", "feat")
    (r / "feature.py").write_text("def answer():\n    return 41\n")
    git(r, "add", "feature.py")
    git(r, "commit", "-q", "-m", "add the answer")
    rejected = git(r, "rev-parse", "HEAD")
    (r / "feature.py").write_text("def answer():\n    return 42\n")
    git(r, "add", "feature.py")
    git(r, "commit", "-q", "-m", "correct the answer")
    return r, rejected


def test_brief_full_prints_what_verifier_brief_full_prints(scratch, capsys):
    repo, _rejected = scratch
    argv = ["full", "--repo", str(repo), "--branch", "feat", "--task", "Return the answer."]
    standalone = invoke(brief.main, argv, capsys)
    umbrella = invoke(delegate.main, ["brief", *argv], capsys)
    assert umbrella == standalone
    code, out, _err = umbrella
    assert code == 0
    assert "Return the answer." in out
    assert "make check" in out
    assert "+    return 42" in out


def test_brief_fixup_prints_what_verifier_brief_fixup_prints(scratch, tmp_path, capsys):
    repo, rejected = scratch
    findings = tmp_path / "findings.md"
    findings.write_text("# Findings\n\n1. The answer is wrong.\n")
    argv = ["fixup", "--repo", str(repo), "--branch", "feat", "--rejected", rejected, "--findings", str(findings)]
    standalone = invoke(brief.main, argv, capsys)
    umbrella = invoke(delegate.main, ["brief", *argv], capsys)
    assert umbrella == standalone
    code, out, _err = umbrella
    assert code == 0
    assert "1. The answer is wrong." in out
    assert "-    return 41" in out


def test_brief_failure_exits_one_and_prints_the_error_to_stderr_like_the_standalone_command(scratch, capsys):
    repo, _rejected = scratch
    argv = ["full", "--repo", str(repo), "--branch", "no-such-branch", "--task", "Return the answer."]
    standalone = invoke(brief.main, argv, capsys)
    umbrella = invoke(delegate.main, ["brief", *argv], capsys)
    assert umbrella == standalone
    code, out, err = umbrella
    assert code == 1
    assert out == ""
    assert "no-such-branch" in err


def test_brief_without_a_mode_is_a_usage_error_like_the_standalone_command(capsys):
    umbrella = invoke(delegate.main, ["brief"], capsys)
    assert umbrella == invoke(brief.main, [], capsys)
    assert umbrella[0] == 2


def test_help_lists_every_subcommand(capsys):
    code, out, _err = invoke(delegate.main, ["--help"], capsys)
    assert code == 0
    for name in ("render", "validate", "bootstrap", "brief"):
        assert name in out.split(), f"--help must list {name}"


def test_no_subcommand_is_a_usage_error_that_lists_the_subcommands(capsys):
    code, out, err = invoke(delegate.main, [], capsys)
    assert code == 2
    assert out == ""
    for name in ("render", "validate", "bootstrap", "brief"):
        assert name in err.split()


def test_an_unknown_subcommand_is_a_usage_error_that_names_it(capsys):
    code, out, err = invoke(delegate.main, ["frobnicate"], capsys)
    assert code == 2
    assert out == ""
    assert "frobnicate" in err


def test_a_tiers_option_before_brief_is_a_usage_error(capsys):
    # verifier-brief has no --tiers option, so the umbrella must not drop it
    # and run the brief as if it were absent.
    code, out, err = invoke(delegate.main, ["--tiers", "x.toml", "brief", "full"], capsys)
    assert code == 2
    assert out == ""
    assert "--tiers" in err


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
README = Path(__file__).resolve().parents[4] / "README.md"
COMMANDS = ("delegate", "agent-definitions", "verifier-brief")


def declared_scripts() -> dict[str, str]:
    return tomllib.loads((PACKAGE_ROOT / "pyproject.toml").read_text())["project"]["scripts"]


@pytest.mark.parametrize(
    ("command", "target"),
    [
        ("delegate", delegate.main),
        ("agent-definitions", cli.main),
        ("verifier-brief", brief.main),
    ],
)
def test_the_package_installs_each_command_to_its_entry_point(command, target):
    # `uv tool install` puts on PATH the commands that [project.scripts] declares.
    module_name, _, attribute = declared_scripts()[command].partition(":")
    assert getattr(importlib.import_module(module_name), attribute) is target


def test_the_package_installs_exactly_the_three_commands():
    assert sorted(declared_scripts()) == sorted(COMMANDS)


def test_the_module_entry_point_lists_the_subcommands():
    r = subprocess.run(
        [sys.executable, "-m", "agent_definitions.delegate", "--help"], capture_output=True, text=True
    )
    assert r.returncode == 0
    assert "bootstrap" in r.stdout and "brief" in r.stdout


@pytest.mark.skipif(not README.is_file(), reason="the README is outside the package source, as in a Nix build")
def test_the_readme_install_section_names_all_three_commands():
    text = README.read_text()
    install = text.split("## Install", 1)[1].split("\n## ", 1)[0]
    for command in COMMANDS:
        assert f"`{command}`" in install, f"the README install section must name {command}"
