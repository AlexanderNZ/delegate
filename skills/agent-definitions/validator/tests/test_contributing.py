"""CONTRIBUTING: a contributor sets up, tests and sends a change without help.

The page gives the setup with `pip` and `pytest`, the test-first rule, the
regeneration command, the rule for a harness fact, and the checklist for a new
adapter. CONTRIBUTING sits outside the package source, so a Nix build that
copies only the package skips these cases with a reason.
"""

import contextlib
import io
import re

import pytest
import yaml

from agent_definitions import delegate

from .pages import ROOT, Block, blocks, commands, outside_the_package

CONTRIBUTING = ROOT / "CONTRIBUTING.md"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def section(heading: str) -> str:
    """The text of the `## heading` section of CONTRIBUTING, up to the next `## ` heading."""
    text = CONTRIBUTING.read_text()
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, flags=re.MULTILINE | re.DOTALL)
    assert match, f"CONTRIBUTING has no section {heading!r}"
    return match.group(1)


def bash_commands(heading: str) -> list[str]:
    """Every command of every bash block in the section."""
    body = section(heading)
    found = [Block(lang, path or None, text) for _fence, lang, path, text in re.findall(r"^(`{3,})(\w+)(?: (\S+))?\n(.*?)^\1$", body, flags=re.MULTILINE | re.DOTALL)]
    return [command for block in found if block.lang == "bash" for command in commands(block)]


def ci_steps() -> list[dict]:
    """The steps of the CI test job."""
    return yaml.safe_load(CI.read_text())["jobs"]["test"]["steps"]


def ci_commands() -> list[str]:
    """Every command that a CI step runs, one line each."""
    return [line.strip() for step in ci_steps() if "run" in step for line in step["run"].splitlines() if line.strip()]


@outside_the_package
def test_every_setup_command_of_contributing_is_a_command_that_ci_runs_and_the_test_directory_is_the_one_that_ci_uses():
    setup = bash_commands("Set up")
    moves = [command for command in setup if command.startswith("cd ")]
    runs = [command for command in setup if not command.startswith("cd ")]

    assert runs, "the setup section must give commands"
    assert [command for command in runs if command not in ci_commands()] == []
    test_step = next(step for step in ci_steps() if step.get("name") == "Run the test suite")
    assert moves == [f"cd {test_step['working-directory']}"]


@outside_the_package
def test_the_setup_installs_with_pip_and_runs_pytest_and_names_no_nix_command():
    setup = bash_commands("Set up")

    assert any(command.startswith("pip install ") and command.endswith(" pytest") for command in setup)
    assert any(command.startswith("python -m pytest") for command in setup)
    assert [command for command in setup if re.search(r"\bnix\b", command)] == []
    assert "Nix" in section("Set up"), "the page must say that Nix is optional"


@outside_the_package
def test_the_setup_names_every_tool_that_ci_installs_besides_python():
    assert "jq" in section("Set up")


@outside_the_package
def test_the_test_first_rule_is_stated_with_the_red_before_green_order():
    text = section("Write the test first").lower()

    assert "fail" in text
    assert "before" in text
    assert "public" in text
    assert "private function" in text


@outside_the_package
def test_the_regeneration_command_runs_and_finds_every_generated_section_current(monkeypatch):
    regenerate = bash_commands("Regenerate the docs")
    assert regenerate == ["delegate docs"]
    monkeypatch.chdir(ROOT)
    out, err = io.StringIO(), io.StringIO()

    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = delegate.main([*regenerate[0].split()[1:], "--check"])

    assert (code, err.getvalue()) == (0, "")
