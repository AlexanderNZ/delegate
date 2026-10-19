"""The neutrality check: no denylisted term in the kit outside the allowlist.

The tests use a temporary kit and a temporary denylist of made-up terms. The
real denylist is private to the consumer, so no real term appears here.
"""

import os
from pathlib import Path

import pytest

from delegate.docs.neutrality import DENYLIST_VAR, Finding, StaleEntry, check_kit

# tests/ -> the repository root -> skills/, the directory with both skill dirs.
KIT_ROOT = Path(__file__).resolve().parents[1] / "skills"


def test_the_kit_holds_no_denylisted_term_outside_the_allowlist():
    report = check_kit(os.environ, default_kit_root=KIT_ROOT)
    if report.skipped is not None:
        pytest.skip(report.skipped)
    print(report.summary())
    assert report.passed, report.format()


PLUGIN_DIRS = ("plugins", ".claude-plugin")


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _kit(repo: Path, files: dict[str, str], plugin_files: dict[str, str] | None = None) -> Path:
    """Make a repository tree and return its kit root, `<repo>/skills`.

    The kit root holds both skill directories and `files`. The repository root
    holds both plugin directories and `plugin_files`.
    """
    root = repo / "skills"
    (root / "agent-delegation").mkdir(parents=True)
    (root / "agent-definitions").mkdir(parents=True)
    for plugin_dir in PLUGIN_DIRS:
        (repo / plugin_dir).mkdir(parents=True)
    _write(root, files)
    _write(repo, plugin_files or {})
    return root


@pytest.fixture
def denylist(tmp_path):
    path = tmp_path / "private" / "denylist.txt"
    path.parent.mkdir()
    path.write_text("# made-up terms\nzorblax\n\nQuuxCorp\n")
    return path


def test_a_denylisted_term_fails_and_names_the_file_line_and_term(tmp_path, denylist):
    kit = _kit(tmp_path / "kit", {
        "agent-delegation/SKILL.md": "# Skill\n\nAsk Zorblax first.\n",
        "agent-definitions/SKILL.md": "# Clean\n",
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.skipped is None
    assert report.passed is False
    assert report.findings == (Finding("agent-delegation/SKILL.md", 3, "zorblax"),)


ALLOWLIST = "agent-definitions/neutrality-allowlist.txt"


def test_a_match_that_the_allowlist_names_by_file_and_term_passes(tmp_path, denylist):
    kit = _kit(tmp_path / "kit", {
        "agent-delegation/SKILL.md": "Ask Zorblax first.\nThen zorblax again.\n",
        ALLOWLIST: "# file and term\nagent-delegation/SKILL.md zorblax\n",
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.passed is True
    assert report.findings == ()


def test_an_allowlist_entry_covers_only_the_file_it_names(tmp_path, denylist):
    kit = _kit(tmp_path / "kit", {
        "agent-delegation/SKILL.md": "Ask Zorblax first.\n",
        "agent-definitions/SKILL.md": "Ask Zorblax too.\n",
        ALLOWLIST: "agent-delegation/SKILL.md zorblax\n",
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.passed is False
    assert report.findings == (Finding("agent-definitions/SKILL.md", 1, "zorblax"),)


@pytest.mark.parametrize("environ", [{}, {DENYLIST_VAR: ""}], ids=["unset", "empty"])
def test_without_the_denylist_variable_the_check_skips_with_a_notice(tmp_path, environ):
    kit = _kit(tmp_path / "kit", {"agent-delegation/SKILL.md": "Ask Zorblax first.\n"})

    report = check_kit(environ, default_kit_root=kit)

    assert report.passed is False
    assert report.findings == ()
    assert report.skipped is not None
    assert DENYLIST_VAR in report.skipped


def test_a_denylist_variable_that_names_no_file_fails_loud(tmp_path):
    kit = _kit(tmp_path / "kit", {})

    with pytest.raises(FileNotFoundError, match=DENYLIST_VAR):
        check_kit({DENYLIST_VAR: str(tmp_path / "absent.txt")}, default_kit_root=kit)


@pytest.mark.parametrize("text", ["# only a comment\n\n", "zorblax\nquux corp\n"],
                         ids=["no-term", "term-with-space"])
def test_a_denylist_with_no_usable_term_fails_loud(tmp_path, text):
    kit = _kit(tmp_path / "kit", {})
    path = tmp_path / "denylist.txt"
    path.write_text(text)

    with pytest.raises(ValueError, match="denylist"):
        check_kit({DENYLIST_VAR: str(path)}, default_kit_root=kit)


def test_a_term_matches_in_any_case_in_a_nested_file(tmp_path, denylist):
    kit = _kit(tmp_path / "kit", {
        "agent-definitions/docs/adr/x.md": "x = 1\nURL = 'QUUXCORP.example'\n",
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.findings == (
        Finding("agent-definitions/docs/adr/x.md", 2, "quuxcorp"),
    )


def test_the_kit_root_variable_replaces_the_default_kit_root(tmp_path, denylist):
    from delegate.docs.neutrality import KIT_ROOT_VAR

    clean = _kit(tmp_path / "clean", {"agent-delegation/SKILL.md": "Nothing here.\n"})
    other = _kit(tmp_path / "other", {"agent-delegation/SKILL.md": "Ask Zorblax.\n"})

    report = check_kit(
        {DENYLIST_VAR: str(denylist), KIT_ROOT_VAR: str(other)}, default_kit_root=clean
    )

    assert report.findings == (Finding("agent-delegation/SKILL.md", 1, "zorblax"),)


def test_a_kit_root_without_both_kit_directories_fails_loud(tmp_path, denylist):
    root = tmp_path / "kit" / "skills"
    (root / "agent-delegation").mkdir(parents=True)
    for plugin_dir in PLUGIN_DIRS:
        (tmp_path / "kit" / plugin_dir).mkdir()

    with pytest.raises(FileNotFoundError, match="agent-definitions"):
        check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=root)


@pytest.mark.parametrize("rel", [
    "agent-definitions/delegate/__pycache__/x.txt",
    "agent-definitions/.pytest_cache/README.md",
    "agent-definitions/build/lib/x.py",
    "agent-definitions/agent_definitions.egg-info/PKG-INFO",
    "agent-delegation/.DS_Store",
])
def test_files_that_a_build_or_the_os_generates_are_not_scanned(tmp_path, denylist, rel):
    kit = _kit(tmp_path / "kit", {rel: "Ask Zorblax.\n"})

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.passed is True


def test_an_allowlist_entry_whose_file_no_longer_holds_its_term_is_stale(tmp_path, denylist):
    # After a scrub removes a term from a file, a stale entry makes the
    # allowlist the only kit file that shows the term.
    kit = _kit(tmp_path / "kit", {
        "agent-delegation/SKILL.md": "Nothing here now.\n",
        ALLOWLIST: "# a reason\nagent-delegation/SKILL.md zorblax\n",
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.findings == ()
    assert report.stale == (StaleEntry(2, "agent-delegation/SKILL.md", "zorblax"),)
    assert report.passed is False
    assert (
        f"STALE_ALLOWLIST_ENTRY {ALLOWLIST}:2: [agent-delegation/SKILL.md zorblax]"
        in report.format()
    )


def test_an_allowlist_entry_that_names_a_missing_file_is_stale(tmp_path, denylist):
    kit = _kit(tmp_path / "kit", {
        "agent-delegation/SKILL.md": "Ask Zorblax first.\n",
        ALLOWLIST: (
            "agent-delegation/SKILL.md zorblax\n"
            "agent-delegation/gone.md quuxcorp\n"
        ),
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.findings == ()
    assert report.stale == (StaleEntry(2, "agent-delegation/gone.md", "quuxcorp"),)
    assert report.passed is False
    assert (
        f"STALE_ALLOWLIST_ENTRY {ALLOWLIST}:2: [agent-delegation/gone.md quuxcorp]"
        in report.format()
    )


def test_a_denylisted_term_in_a_plugin_file_fails_and_the_output_names_the_file(
    tmp_path, denylist
):
    kit = _kit(tmp_path / "repo", {"agent-delegation/SKILL.md": "Clean.\n"}, plugin_files={
        "plugins/demo/hooks/register.tsx": "// hook\n\nconst owner = 'Zorblax';\n",
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.passed is False
    assert report.findings == (Finding("plugins/demo/hooks/register.tsx", 3, "zorblax"),)
    assert (
        f"DENYLISTED_TERM plugins/demo/hooks/register.tsx:3: [zorblax] is not in {ALLOWLIST}"
        in report.format()
    )


def test_a_denylisted_term_in_the_marketplace_file_fails(tmp_path, denylist):
    kit = _kit(tmp_path / "repo", {}, plugin_files={
        ".claude-plugin/marketplace.json": '{\n  "owner": "QuuxCorp"\n}\n',
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.passed is False
    assert report.findings == (Finding(".claude-plugin/marketplace.json", 2, "quuxcorp"),)


def test_an_allowlist_entry_for_a_plugin_file_permits_its_term(tmp_path, denylist):
    kit = _kit(
        tmp_path / "repo",
        {ALLOWLIST: "# a reason\nplugins/demo/hooks/register.tsx zorblax\n"},
        plugin_files={"plugins/demo/hooks/register.tsx": "const owner = 'Zorblax';\n"},
    )

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.findings == ()
    assert report.stale == ()
    assert report.passed is True


def test_the_plugin_directories_come_from_the_parent_of_the_kit_root_variable(
    tmp_path, denylist
):
    from delegate.docs.neutrality import KIT_ROOT_VAR

    clean = _kit(tmp_path / "clean", {})
    other = _kit(tmp_path / "other", {}, plugin_files={"plugins/demo/README.md": "Zorblax.\n"})

    report = check_kit(
        {DENYLIST_VAR: str(denylist), KIT_ROOT_VAR: str(other)}, default_kit_root=clean
    )

    assert report.findings == (Finding("plugins/demo/README.md", 1, "zorblax"),)


@pytest.mark.parametrize("missing", PLUGIN_DIRS)
def test_a_repository_without_a_plugin_directory_fails_loud(tmp_path, denylist, missing):
    kit = _kit(tmp_path / "repo", {})
    (tmp_path / "repo" / missing).rmdir()

    with pytest.raises(FileNotFoundError, match=f"{missing}/"):
        check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)


def test_the_types_that_claude_code_writes_into_a_plugin_are_not_scanned(tmp_path, denylist):
    # Claude Code writes type declarations into a plugin that it loads from a
    # folder. The .gitignore of the repository holds them out of the tree.
    kit = _kit(tmp_path / "repo", {}, plugin_files={
        "plugins/demo/.claude-plugin/types/index.d.ts": "// Zorblax\n",
        "plugins/demo/.claude-plugin/plugin.json": '{"name": "demo"}\n',
    })

    report = check_kit({DENYLIST_VAR: str(denylist)}, default_kit_root=kit)

    assert report.passed is True
    assert report.files == 1
