"""CONTRIBUTING: a contributor sets up, tests and sends a change without help.

The page gives the setup with `pip` and `pytest`, the test-first rule, the
regeneration command, the rule for a harness fact, and the checklist for a new
adapter. CONTRIBUTING sits outside the package source, so a Nix build that
copies only the package skips these cases with a reason.
"""

import contextlib
import io
import json
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


@outside_the_package
def test_the_harness_fact_rule_names_the_version_and_the_date_and_the_manifest_keys_that_every_recording_holds():
    text = section("Date every harness fact")
    manifests = sorted((ROOT / "skills" / "agent-definitions" / "validator" / "tests" / "fixtures").glob("*/manifest.json"))

    assert re.search(r"version.*date|date.*version", text, flags=re.DOTALL)
    assert "`harness_version`" in text and "`recorded`" in text
    assert manifests, "the repository must hold a recorded manifest for the rule to bind"
    for manifest in manifests:
        keys = json.loads(manifest.read_text())
        assert keys["harness_version"] and re.fullmatch(r"\d{4}-\d{2}-\d{2}", keys["recorded"]), manifest


@outside_the_package
def test_the_adapter_checklist_is_a_list_of_boxes_that_names_the_contract_test_and_the_live_smoke_run():
    text = section("Add a harness adapter")
    boxes = re.findall(r"^- \[ \] (.+)$", text, flags=re.MULTILINE)

    assert len(boxes) >= 6
    assert any("contract test" in box for box in boxes)
    assert any("live smoke run" in box for box in boxes)
    assert any("version" in box and "date" in box for box in boxes), "the smoke run box must ask for the version and the date"


@outside_the_package
def test_every_file_that_the_adapter_checklist_names_exists_in_the_repository():
    text = section("Add a harness adapter")
    paths = [token for token in re.findall(r"`([^`\s]+)`", text) if "/" in token]

    assert paths, "the checklist must name the files that a contributor changes"
    assert [path for path in paths if not (ROOT / path).exists()] == []
    assert "docs/how-to/add-a-harness-adapter.md" in re.findall(r"\]\(([^)\s]+)\)", text)


@outside_the_package
def test_every_relative_link_of_contributing_reaches_a_file_and_a_heading():
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", CONTRIBUTING.read_text()):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = target.partition("#")
        file = ROOT / path
        if not file.is_file():
            broken.append(target)
        elif anchor and anchor not in headings_anchors(file.read_text()):
            broken.append(target)
    assert broken == []


@outside_the_package
def test_the_prose_keeps_each_sentence_to_25_words_or_fewer():
    prose = re.sub(r"^(`{3,}).*?^\1$", "", CONTRIBUTING.read_text(), flags=re.DOTALL | re.MULTILINE)
    lines = [line for line in prose.splitlines() if line.strip() and not line.startswith(("#", "|", "<!--"))]
    text = re.sub(r"`[^`]*`", "CODE", re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", " ".join(re.sub(r"^\s*(?:[-*]|\d+\.)\s+(?:\[ \]\s+)?", "", line) for line in lines)))  # an inline code span counts as one word
    sentences = re.split(r"(?<=[.:?!])\s+", text)
    assert [s for s in sentences if len(s.split()) > 25] == []


@outside_the_package
def test_the_readme_links_contributing():
    links = re.findall(r"\]\(([^)\s]+)\)", (ROOT / "README.md").read_text())
    assert "CONTRIBUTING.md" in links


def headings_anchors(markdown: str) -> set[str]:
    """The anchors of the headings of a page, as GitHub builds them."""
    without_code = re.sub(r"^(`{3,}).*?^\1$", "", markdown, flags=re.DOTALL | re.MULTILINE)
    return {re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-") for heading in re.findall(r"^#{1,6} (.+)$", without_code, flags=re.MULTILINE)}
