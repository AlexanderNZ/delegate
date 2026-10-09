"""`delegate docs` writes the generated sections of the reference pages, and checks that the committed pages are current.

The pages are outside the package source, as the README is, so a Nix build
that copies only the package skips the cases that read them, with a reason.
"""

import re
import shutil
from pathlib import Path

import pytest

from delegate import delegate, reference, validate

from .pages import repository_root

ROOT = repository_root(Path(__file__))

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


@pytest.fixture
def pages(tmp_path):
    """A copy of the reference pages and of the skill, so a test can change a page and the repository stays as it is."""
    shutil.copytree(ROOT / "docs", tmp_path / "docs")
    skill = tmp_path / "skills" / "agent-definitions" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    shutil.copy(ROOT / "skills" / "agent-definitions" / "SKILL.md", skill)
    return tmp_path


def docs(capsys, *argv):
    code = delegate.main(["docs", *argv])
    out, err = capsys.readouterr()
    return code, out, err


@outside_the_package
def test_the_command_writes_the_options_of_run_into_the_run_page_and_keeps_the_prose(pages, capsys):
    page = pages / "docs" / "reference" / "run.md"
    page.write_text(page.read_text().replace("| `--break-lock` |", "| `--old-flag` |"))

    code, out, err = docs(capsys, "--root", str(pages))

    text = page.read_text()
    assert (code, err) == (0, "")
    assert "| `--break-lock` | Remove the lock of the run branch when its process no longer exists; a lock whose process is alive stays. |" in text
    assert "--old-flag" not in text
    assert "## What a run does" in text  # the prose around the section stays
    assert "docs/reference/run.md" in out


@outside_the_package
def test_the_check_fails_and_names_the_section_when_a_page_does_not_match_the_parser(pages, capsys):
    page = pages / "docs" / "reference" / "run.md"
    before = page.read_text()
    stale = before.replace("| `--break-lock` |", "| `--old-flag` |")
    assert stale != before
    page.write_text(stale)

    code, out, err = docs(capsys, "--root", str(pages), "--check")

    assert code == 1
    assert "docs/reference/run.md" in err and "run-options" in err
    assert page.read_text() == stale  # the check writes nothing


@outside_the_package
def test_the_check_passes_after_the_command_has_written_the_pages(pages, capsys):
    assert docs(capsys, "--root", str(pages))[0] == 0

    code, out, err = docs(capsys, "--root", str(pages), "--check")

    assert (code, err) == (0, "")


@outside_the_package
def test_a_page_with_no_marker_for_its_section_is_an_error_that_names_the_page_and_the_section(pages, capsys):
    page = pages / "docs" / "reference" / "run.md"
    page.write_text("# Reference: `delegate run`\n\nNo marker here.\n")

    code, out, err = docs(capsys, "--root", str(pages))

    assert code == 2
    assert "docs/reference/run.md" in err and "run-options" in err


def test_a_root_with_no_page_is_an_error_that_names_the_page(tmp_path, capsys):
    code, out, err = docs(capsys, "--root", str(tmp_path))

    assert code == 2
    assert "docs/reference/run.md" in err


@outside_the_package
def test_the_command_writes_the_options_of_status_and_of_watch_into_their_page(pages, capsys):
    page = pages / "docs" / "reference" / "status-and-watch.md"
    page.write_text(page.read_text().replace("| `--poll-seconds N` |", "| `--old-flag` |"))

    code, out, err = docs(capsys, "--root", str(pages))

    text = page.read_text()
    assert (code, err) == (0, "")
    assert "| `--poll-seconds <poll-seconds>` | How often to read the journal; default 1. |" in text
    assert "| `--until verdict` |" in text
    assert "| `[<run-id>]` | The run to show; default is the newest run of the repository. |" in text
    assert "--old-flag" not in text
    assert "### The problem events" in text  # the prose after the section stays


@outside_the_package
def test_the_command_writes_the_exit_code_table_of_watch_with_the_reason_and_the_condition_of_each_code(pages, capsys):
    page = pages / "docs" / "reference" / "status-and-watch.md"
    page.write_text(page.read_text().replace("| 5 | stall |", "| 5 | wrong reason |"))

    code, out, err = docs(capsys, "--root", str(pages))

    text = page.read_text()
    assert (code, err) == (0, "")
    assert "| 5 | stall | `--stall-minutes` is set and nothing changed for that time. |" in text
    assert "| 0 | run succeeded | The journal holds `run-end` with the result `built`. |" in text
    assert "wrong reason" not in text
    assert "Code 2 is also the code that `argparse` gives for a usage error." in text  # the prose after the table stays


@outside_the_package
def test_the_command_writes_the_options_of_render_validate_bootstrap_and_brief_into_the_commands_page(pages, capsys):
    page = pages / "docs" / "reference" / "commands.md"
    page.write_text(page.read_text().replace("| `--gate-command <cmd>` |", "| `--old-flag` |"))

    code, out, err = docs(capsys, "--root", str(pages))

    text = page.read_text()
    assert (code, err) == (0, "")
    assert "| `--gate-command <cmd>` | A gate the verifier may run, alone or with arguments; repeat it. |" in text
    assert "| `-o, --out <out>` | " in text  # render
    assert "| `--skills-dir <skills-dir>` | Check that every preloaded skill exists here. |" in text  # validate
    assert "| `--rejected <rejected>` | The commit the first verifier rejected. Required. |" in text  # brief fixup
    assert "| `--tiers <tiers>` | Path to a tiers.toml; default is the bundled table. |" in text  # before the subcommand
    assert "--old-flag" not in text
    assert "## `delegate bootstrap`" in text  # the prose of the page stays


GUARD_ROWS = [
    "| git reads | `git diff`, `git log`, `git show`, `git status`, `git ls-files`, `git ls-tree`, `git rev-parse`, `git merge-base`, `git worktree list`, `git branch --show-current` |",
    "| shell reads | `ls`, `cat`, `head`, `tail`, `sed -n`, `grep`, `rg`, `find`, `wc`, `diff`, `jq`, `shasum`, `stat`, `readlink`, `file`, `which`, `env`, `pwd`, `echo`, `printf`, `date`, `mktemp`, `python3`, `bash -c` |",
    "| temp writes | `mkdir`, `mv`, `rm`, `tee`, `touch` — each one only when every path argument starts with `/tmp/`, `/private/tmp/`, `$TMPDIR`, or `/var/folders/`. A redirection target obeys the same rule, in every segment. |",
    "| temp destination | `cp`, `cd` — each one only when the last path argument is a temp path. The earlier path arguments are sources, and a read of any path is a read. |",
]


@outside_the_package
@pytest.mark.parametrize("page", ["skills/agent-definitions/SKILL.md", "docs/reference/commands.md"])
def test_the_command_writes_the_guard_table_of_the_verifier_from_the_renderer_constants_into_the_skill_and_the_reference(pages, capsys, page):
    path = pages / page
    path.write_text(path.read_text().replace("| shell reads |", "| shell reads (old) |").replace("`git ls-tree`, ", ""))

    code, out, err = docs(capsys, "--root", str(pages))

    text = path.read_text()
    assert (code, err) == (0, "")
    for row in GUARD_ROWS:
        assert row in text
    assert "(old)" not in text


def emitted_finding_codes() -> set[str]:
    """The codes that the source of the validator writes, found in the text of the source: an oracle that does not run the validator."""
    source = (Path(validate.__file__)).read_text()
    return set(re.findall(r'Finding\(\s*"([A-Z_]+)"', source))


def codes_in_table(text: str) -> set[str]:
    return set(re.findall(r"^\| `([A-Z_]+)` \|", text, flags=re.MULTILINE))


@outside_the_package
@pytest.mark.parametrize("page", ["skills/agent-definitions/SKILL.md", "docs/reference/commands.md"])
def test_the_command_writes_a_row_for_each_finding_code_of_the_validator_into_the_skill_and_the_reference(pages, capsys, page):
    path = pages / page
    path.write_text(path.read_text().replace("| `BAD_TYPE` |", "| `OLD_CODE` |"))

    code, out, err = docs(capsys, "--root", str(pages))

    text = path.read_text()
    assert (code, err) == (0, "")
    assert "| `BAD_TYPE` | OpenCode `tools` as a string; that aborts config load for the whole session |" in text
    assert "OLD_CODE" not in text
    assert emitted_finding_codes() <= codes_in_table(text)


def test_the_validator_emits_exactly_the_finding_codes_that_it_documents():
    assert "BAD_MODEL" in emitted_finding_codes()  # the scan finds codes
    assert emitted_finding_codes() == set(validate.FINDING_CODES)


@outside_the_package
@pytest.mark.parametrize("section", reference.SECTIONS, ids=lambda section: section.name)
def test_each_committed_generated_section_matches_the_code(section):
    """The one drift test of a section. A changed flag, exit code, guard command or finding code with no `delegate docs` fails it."""
    assert section.name not in [stale.name for stale in reference.stale_sections(ROOT)], "run `delegate docs` and commit the result"


@outside_the_package
def test_the_reference_lists_each_generated_section_with_its_page_and_its_source(pages, capsys):
    page = pages / "docs" / "reference" / "docs.md"

    code, out, err = docs(capsys, "--root", str(pages))

    text = page.read_text()
    assert (code, err) == (0, "")
    assert "| `docs/reference/run.md` | `run-options` | The argument parser of `delegate run`. |" in text
    assert "| `skills/agent-definitions/SKILL.md` | `verifier-guard-commands` | The command constants of the renderer. |" in text
    assert "| `docs/reference/status-and-watch.md` | `watch-exit-codes` | The exit codes of `delegate watch`. |" in text
    assert "| `--check` | " in text


@outside_the_package
def test_the_readme_links_the_generated_pages_and_names_the_command_that_writes_them():
    readme = (ROOT / "README.md").read_text()
    assert "docs/reference/commands.md" in readme
    assert "docs/reference/docs.md" in readme
    assert "delegate docs" in readme
