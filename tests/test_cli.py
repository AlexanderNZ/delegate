import subprocess
import sys
import tomllib

import pytest

from delegate.cli import brief, delegate
from delegate.cli.agent_definitions import main
from tests.conftest import EXAMPLE
from tests.pages import ROOT, outside_the_package


def test_render_then_validate_round_trip(tmp_path, capsys):
    assert main(["render", str(EXAMPLE), "-o", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "claude-code/java-spring-specialist.md" in out
    assert main(["validate", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip().endswith("ok")


def test_validate_exits_one_on_findings(tmp_path, capsys):
    main(["render", str(EXAMPLE), "-o", str(tmp_path)])
    (tmp_path / "claude-code" / "java-spring-verifier.md").unlink()
    assert main(["validate", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "MISSING_TWIN" in captured.out


def test_validate_with_nothing_exits_two(capsys):
    assert main(["validate"]) == 2


def test_module_entry_point_runs():
    r = subprocess.run([sys.executable, "-m", "delegate.cli.agent_definitions", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "render" in r.stdout


# The reference page that the help of each command names: (the entry point, the arguments before --help, the page).
# A consumer coordinator reads the help and no page, so the help names the docs site and the page to read (#248).
HELP_PAGES = [
    ("delegate", [], "reference/commands.md"),
    ("agent-definitions", [], "reference/commands.md"),
    ("verifier-brief", [], "reference/commands.md"),
    ("delegate", ["run"], "reference/run.md"),
    ("delegate", ["pause"], "reference/run.md"),
    ("delegate", ["stop"], "reference/commands.md"),
    ("delegate", ["status"], "reference/status-and-watch.md"),
    ("delegate", ["watch"], "reference/status-and-watch.md"),
    ("delegate", ["clean"], "reference/commands.md"),
    ("delegate", ["docs"], "reference/docs.md"),
]


def _help_of(command: str, arguments: list[str], capsys) -> str:
    entry = {"delegate": delegate.main, "agent-definitions": main, "verifier-brief": brief.main}[command]
    try:
        code = entry([*arguments, "--help"])
    except SystemExit as stop:
        code = stop.code
    out = capsys.readouterr().out
    assert code == 0
    return out


def _site() -> tuple[str, dict[str, str]]:
    """The `site_url` of zensical.toml, and the title of each page of its navigation."""
    project = tomllib.loads((ROOT / "zensical.toml").read_text())["project"]
    titles: dict[str, str] = {}

    def walk(items: list) -> None:
        for item in items:
            for title, value in item.items():
                walk(value) if isinstance(value, list) else titles.setdefault(value, title)

    walk(project["nav"])
    return project["site_url"], titles


@outside_the_package
@pytest.mark.parametrize(("command", "arguments", "page"), HELP_PAGES, ids=[" ".join([c, *a]) for c, a, _ in HELP_PAGES])
def test_the_help_of_each_command_names_the_docs_site_and_its_reference_page_on_a_narrow_terminal(command, arguments, page, capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")  # argparse wraps the help to the terminal; the URL must stay on one line
    site_url, titles = _site()

    out = _help_of(command, arguments, capsys)

    assert site_url in out
    assert f"{site_url}{page.removesuffix('.md')}/" in out
    assert f'"{titles[page]}"' in out


@outside_the_package
def test_the_help_of_run_says_to_read_the_quality_check_of_the_dry_run_before_each_launch_and_each_resume(capsys, monkeypatch):
    monkeypatch.setenv("COLUMNS", "60")

    out = " ".join(_help_of("delegate", ["run"], capsys).split())

    assert "Before each launch and each resume, run delegate run <workflow> --dry-run and read its quality check" in out
