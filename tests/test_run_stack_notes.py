"""The notes of a stack: the engine adds them to each brief of the stack (#156).

A stack can name `specialist-notes` and `verifier-notes`, inline or in a file
(`specialist-notes-file`, `verifier-notes-file`). The engine adds the text as
the section "Notes of this stack": the specialist notes to each specialist
brief, the verifier notes to each verifier brief. Each case calls
`delegate.main` against a real temporary git repository, with the scripted
adapter in the place of the harness.
"""

from pathlib import Path

import pytest

from delegate import adapters

from .support import TEMPLATE_GIT, WORKFLOW, ScriptedAdapter, copy_of, git, run_main

HEADING = "## Notes of this stack"
SPECIALIST_NOTES = "Run `make setup` once before the tests. This host has no visual gate."
VERIFIER_NOTES = "Check each screenshot claim against the test output. This host has no visual gate."
# A part of each text that the other text does not hold.
SPECIALIST_ONLY = "make setup"
VERIFIER_ONLY = "screenshot claim"
STACK_LINE = 'gates = ["test -f feature.txt"]'


def workflow(fields: str, mode: str = "assure") -> str:
    """The test workflow with `fields` added to the stack `python`."""
    return WORKFLOW.replace('mode = "assure"', f'mode = "{mode}"').replace(STACK_LINE, f"{STACK_LINE}\n{fields}")


BOTH_INLINE = f'specialist-notes = "{SPECIALIST_NOTES}"\nverifier-notes = "{VERIFIER_NOTES}"'
BOTH_FILES = 'specialist-notes-file = "notes/specialist.md"\nverifier-notes-file = "notes/verifier.md"'


def make_repo(tmp_path: Path, text: str, files: dict[str, str] | None = None) -> Path:
    """A real git repository on `main` that holds `workflow.toml` and `files`."""

    def build(repo: Path) -> None:
        (repo / "workflow.toml").write_text(text)
        for name, content in (files or {}).items():
            (repo / name).parent.mkdir(parents=True, exist_ok=True)
            (repo / name).write_text(content)
        git(repo, *TEMPLATE_GIT, "init", "-q", "-b", "main")
        git(repo, *TEMPLATE_GIT, "add", "-A")
        git(repo, *TEMPLATE_GIT, "commit", "-q", "-m", "seed")

    return copy_of(tmp_path / "repo", ("stack-notes", text, tuple(sorted((files or {}).items()))), build)


@pytest.fixture
def adapter(git_identity):
    scripted = ScriptedAdapter()
    adapters.register("scripted", scripted)
    yield scripted
    adapters.unregister("scripted")


def run(repo: Path, capsys) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))


def notes_of(prompt: str) -> str:
    """The text of the section "Notes of this stack" of `prompt`, up to the next section."""
    assert prompt.count(HEADING) == 1, prompt
    section = prompt.split(HEADING, 1)[1]
    return section.split("\n## ", 1)[0]


def assert_specialist_brief(prompt: str) -> None:
    section = notes_of(prompt)
    assert SPECIALIST_NOTES in section
    assert VERIFIER_ONLY not in prompt


def assert_verifier_brief(prompt: str) -> None:
    section = notes_of(prompt)
    assert VERIFIER_NOTES in section
    assert SPECIALIST_ONLY not in prompt


def test_each_specialist_brief_holds_the_specialist_notes_and_each_verifier_brief_the_verifier_notes(tmp_path, capsys, adapter):
    # A REJECT and then an ACCEPT: a specialist brief, a full verifier brief, a fix-up brief, and a scoped verifier brief.
    adapter.verdict_sequence = ["REJECT", "ACCEPT"]
    repo = make_repo(tmp_path, workflow(BOTH_INLINE))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    assert (len(adapter.calls), len(adapter.fixup_calls), len(adapter.verifier_calls)) == (1, 1, 2)
    for request in (*adapter.calls, *adapter.fixup_calls):
        assert_specialist_brief(request.prompt)
    full, scoped = adapter.verifier_calls
    assert not full.prompt.startswith("## Findings under verification")
    assert scoped.prompt.startswith("## Findings under verification")  # the notes do not change the mode of the brief
    for request in adapter.verifier_calls:
        assert_verifier_brief(request.prompt)


def test_the_section_says_that_the_ticket_and_the_rules_of_the_brief_win(tmp_path, capsys, adapter):
    repo = make_repo(tmp_path, workflow(BOTH_INLINE))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (specialist,) = adapter.calls
    section = notes_of(specialist.prompt)
    assert "do not replace the ticket" in section
    assert "the ticket and the rules of this brief win" in section
    # The ticket text stays in the task, before the notes.
    assert specialist.prompt.index("The export command writes a CSV file.") < specialist.prompt.index(HEADING)


def test_the_continuation_brief_holds_the_specialist_notes(tmp_path, capsys, adapter):
    # The first run commits no `feature.txt`, so the gate is red and a new agent continues the work.
    adapter.files = {"other.txt": "other\n"}
    adapter.continuation_files = [{"feature.txt": "feature\n"}]
    repo = make_repo(tmp_path, workflow(BOTH_INLINE))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (continuation,) = adapter.continuation_calls
    assert "## Continuation" in continuation.prompt
    assert_specialist_brief(continuation.prompt)


def test_the_chain_brief_of_economy_mode_holds_the_verifier_notes(tmp_path, capsys, adapter):
    repo = make_repo(tmp_path, workflow(BOTH_INLINE, mode="economy"))

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    (verifier,) = adapter.verifier_calls
    assert "## Finding labels" in verifier.prompt  # a chain brief
    assert_verifier_brief(verifier.prompt)
    (specialist,) = adapter.calls
    assert_specialist_brief(specialist.prompt)


def test_a_stack_with_no_notes_gives_briefs_with_no_notes_section(tmp_path, capsys, adapter):
    repo = make_repo(tmp_path, WORKFLOW)

    code, out, err = run(repo, capsys)

    assert (code, err) == (0, "")
    for request in (*adapter.calls, *adapter.verifier_calls):
        assert HEADING not in request.prompt


def test_the_file_form_reads_each_file_relative_to_the_workflow_file(tmp_path, capsys, adapter):
    # The workflow file is in a subdirectory, so a path relative to the repository root would not be found.
    text = workflow(BOTH_FILES)
    files = {
        "plan/workflow.toml": text,
        "plan/notes/specialist.md": f"{SPECIALIST_NOTES}\n",
        "plan/notes/verifier.md": f"{VERIFIER_NOTES}\n",
    }
    repo = make_repo(tmp_path, "", files)

    code, out, err = run_main(capsys, str(repo / "plan" / "workflow.toml"), "--repo", str(repo))

    assert (code, err) == (0, "")
    (specialist,) = adapter.calls
    (verifier,) = adapter.verifier_calls
    assert_specialist_brief(specialist.prompt)
    assert_verifier_brief(verifier.prompt)


def dry_run(tmp_path: Path, capsys, text: str, files: dict[str, str] | None = None) -> tuple[int, str, str]:
    """`delegate run --dry-run` on `text` with a built-in adapter, which the check knows without a registration."""
    repo = make_repo(tmp_path, text.replace('adapter = "scripted"', 'adapter = "claude-code"'), files)
    return run_main(capsys, str(repo / "workflow.toml"), "--dry-run")


@pytest.mark.parametrize("role", ["specialist", "verifier"])
def test_a_missing_notes_file_is_a_workflow_problem_that_names_the_field(tmp_path, capsys, role):
    code, out, err = dry_run(tmp_path, capsys, workflow(f'{role}-notes-file = "notes/missing.md"'))

    assert (code, out) == (1, "")
    assert f"stacks.python: {role}-notes-file 'notes/missing.md' not found" in err


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        pytest.param(
            'specialist-notes = "a"\nspecialist-notes-file = "notes/specialist.md"',
            "stacks.python: give at most one of specialist-notes and specialist-notes-file",
            id="specialist-both",
        ),
        pytest.param(
            'verifier-notes = "a"\nverifier-notes-file = "notes/verifier.md"',
            "stacks.python: give at most one of verifier-notes and verifier-notes-file",
            id="verifier-both",
        ),
        pytest.param("specialist-notes = 3", "stacks.python.specialist-notes: must be a string", id="not-a-string"),
        pytest.param('verifier-notes = "  "', "stacks.python.verifier-notes: must not be empty", id="blank"),
        pytest.param(
            "verifier-notes-file = ['notes/verifier.md']", "stacks.python.verifier-notes-file: must be a string", id="file-not-a-string",
        ),
        pytest.param(
            'specialist-notes-file = "notes/empty.md"', "stacks.python: specialist-notes-file 'notes/empty.md' is empty", id="empty-file",
        ),
    ],
)
def test_the_validator_checks_the_notes_fields(tmp_path, capsys, fields, message):
    files = {"notes/specialist.md": "a\n", "notes/verifier.md": "a\n", "notes/empty.md": "\n"}

    code, out, err = dry_run(tmp_path, capsys, workflow(fields), files)

    assert (code, out) == (1, "")
    assert message in err


def test_the_dry_run_prints_the_notes_of_each_stack(tmp_path, capsys):
    files = {"notes/verifier.md": "First line.\nSecond line.\n"}
    text = workflow(f'specialist-notes = "{SPECIALIST_NOTES}"\nverifier-notes-file = "notes/verifier.md"')

    code, out, err = dry_run(tmp_path, capsys, text, files)

    assert (code, err) == (0, "")
    assert f"stack python specialist-notes:\n  {SPECIALIST_NOTES}\n" in out
    assert "stack python verifier-notes:\n  First line.\n  Second line.\n" in out


def test_the_dry_run_prints_no_notes_for_a_stack_with_no_notes(tmp_path, capsys):
    code, out, err = dry_run(tmp_path, capsys, WORKFLOW)

    assert (code, err) == (0, "")
    assert "notes" not in out
