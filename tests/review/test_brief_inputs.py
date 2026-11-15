"""The briefs take one value of their inputs, not ten to sixteen parameters (#362).

`delegate.run.brief` holds the functions that make each brief. Before the change they took 8 to 16 positional parameters,
and several had the same type (`worktree`, `report_path`, `scratch`), so a swap was not found by the type checker. These
cases check two things.

- The text of each brief did not change by one byte. Each scenario runs `delegate run` against a real temporary git
  repository with the scripted adapter in the place of the harness, and compares each prompt that the engine gave an
  agent with the text in `golden_briefs/`. The parts of a prompt that change from one run to the next (the temporary
  directory, the run id, the commit ids) are replaced by a fixed name first. To write the files again after a change of
  a brief that is meant, run the cases with `DELEGATE_WRITE_GOLDEN_BRIEFS=1` and read the diff.
- No function of the brief module takes more than six parameters.
"""

from __future__ import annotations

import ast
import json
import os
import re
import tempfile
from pathlib import Path

import pytest

import delegate
from delegate import adapters
from delegate.cli import brief

from ..support import (
    WORKFLOW, ScriptedAdapter, commit_head, committed_repo, git, make_repo, resume_main, run_main, state_dir, ticket_of, valid_report,
)
from ..support_run import ForgetfulAdapter

GOLDEN = Path(__file__).parent / "golden_briefs"
BRIEF_MODULE = Path(delegate.__file__).parent / "run" / "brief.py"
PARAMETER_LIMIT = 6

SPECIALIST_NOTES = "SPECIALIST-NOTE-5521 run the setup first."
VERIFIER_NOTES = "VERIFIER-NOTE-6632 check each claim against the output."
NOTES = f'specialist-notes = "{SPECIALIST_NOTES}"\nverifier-notes = "{VERIFIER_NOTES}"\n'
FINDINGS = ["[c] FINDING-7741 the comma is not quoted.", "FINDING-7742 the header row is missing."]


@pytest.fixture(autouse=True)
def fixed_places(git_identity, tmp_path, monkeypatch):
    """Each commit of a case has a committer, and the temporary directory of the engine is one that the case names.

    The scratch directory of a verifier is made in the temporary directory of the host, so a prompt names a path that
    depends on the machine unless the case sets it.
    """
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(temporary))


@pytest.fixture
def registered():
    """A function that registers a scripted adapter as `scripted` for the case, and gives it back."""
    names: list[str] = []

    def make(adapter: ScriptedAdapter) -> ScriptedAdapter:
        adapters.register("scripted", adapter)
        names.append("scripted")
        return adapter

    yield make
    for name in names:
        adapters.unregister(name)


def workflow(mode: str = "assure", gate: str | None = None, notes: bool = True, tickets: str = "a") -> str:
    """The test workflow with the stack notes, one ticket for each letter of `tickets`, each blocked by the one before."""
    text = WORKFLOW.split("[[tickets]]")[0].replace('mode = "assure"', f'mode = "{mode}"')
    if gate is not None:
        text = text.replace('gates = ["test -f feature.txt"]', f"gates = ['{gate}']")
    if notes:
        text = text.replace("gate-timeout", NOTES + "gate-timeout", 1)
    for position, ticket in enumerate(tickets):
        blockers = f'"{tickets[position - 1]}"' if position else ""
        text += f'\n[[tickets]]\nid = "{ticket}"\ntext = "Ticket {ticket} adds its file."\nstack = "python"\nblocked-by = [{blockers}]\n'
    return text


def normal(text: str, repo: Path, tmp_path: Path) -> str:
    """`text` with each part that changes from one run to the next replaced by a fixed name."""
    state = state_dir(repo)
    for path, name in ((state, "<STATE>"), (repo, "<REPO>"), (tmp_path, "<TMP>")):
        for form in (str(path), str(path.resolve())):
            text = text.replace(form, name)
    text = re.sub(r"\d{8}T\d{6}Z-[0-9a-f]{4}", "<RUN>", text)
    text = re.sub(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", "<SHA>", text)
    text = re.sub(r"(delegate-verifier-<RUN>-[\w]+?-)[A-Za-z0-9_]{8}(?=[/\s`'\"])", r"\1<RANDOM>", text)
    return re.sub(r"(delegate-verify-)[A-Za-z0-9_]{8}(?=[/\s`'\"])", r"\1<RANDOM>", text)


def prompts(adapter: ScriptedAdapter) -> dict[str, list[str]]:
    return {
        "specialist": [call.prompt for call in adapter.calls],
        "continuation": [call.prompt for call in adapter.continuation_calls],
        "fixup": [call.prompt for call in adapter.fixup_calls],
        "verifier": [call.prompt for call in adapter.verifier_calls],
    }


def check(scenario: str, found: dict[str, list[str]], repo: Path, tmp_path: Path) -> None:
    """Compare each prompt of `found` with its file in `golden_briefs/`, or write the file when the variable asks for it."""
    names = [f"{scenario}.{role}-{number}.txt" for role, texts in found.items() for number in range(len(texts))]
    assert names, scenario
    for role, texts in found.items():
        for number, text in enumerate(texts):
            path = GOLDEN / f"{scenario}.{role}-{number}.txt"
            shown = normal(text, repo, tmp_path)
            if os.environ.get("DELEGATE_WRITE_GOLDEN_BRIEFS"):
                GOLDEN.mkdir(exist_ok=True)
                path.write_text(shown)
            assert path.read_text() == shown, f"the brief {path.name} changed"
    if not os.environ.get("DELEGATE_WRITE_GOLDEN_BRIEFS"):
        # A scenario that no longer makes a brief leaves a file behind: the files of the scenario are exactly the briefs.
        assert sorted(p.name for p in GOLDEN.glob(f"{scenario}.*")) == sorted(names)


def start(repo: Path, capsys) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))


def test_the_specialist_and_a_continuation_for_red_gates_keep_their_text(tmp_path, capsys, registered):
    adapter = registered(ScriptedAdapter(files={"other.txt": "other\n"}, continuation_files=[{"feature.txt": "feature\n"}]))
    repo = make_repo(tmp_path, workflow())

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    check("red-gates", prompts(adapter), repo, tmp_path)


def test_a_resumed_continuation_for_red_gates_keeps_its_text(tmp_path, capsys, registered):
    adapter = registered(
        ScriptedAdapter(files={"other.txt": "other\n"}, continuation_files=[{"feature.txt": "feature\n"}], supports_resume=True)
    )
    repo = make_repo(tmp_path, workflow(notes=False))

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    check("resumed", prompts(adapter), repo, tmp_path)


@pytest.mark.parametrize("resume", [False, True])
def test_a_continuation_for_an_uncommitted_file_keeps_its_text(tmp_path, capsys, registered, resume):
    adapter = registered(ForgetfulAdapter(supports_resume=resume))
    repo = make_repo(tmp_path, workflow())

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    check(f"uncommitted-{'resumed' if resume else 'new'}", prompts(adapter), repo, tmp_path)


@pytest.mark.parametrize("report", ["missing", "invalid"])
def test_the_report_correction_brief_keeps_its_text(tmp_path, capsys, registered, report):
    # The first session commits, and its report is missing or not valid, so the engine asks for a corrected report.
    first = None if report == "missing" else "this is not json"
    adapter = registered(
        ScriptedAdapter(
            report_text=lambda request, head: (
                json.dumps(valid_report("a", "run/demo-a", head)) if "## Continuation" in request.prompt else first
            )
        )
    )
    repo = make_repo(tmp_path, workflow())

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    check(f"report-{report}", prompts(adapter), repo, tmp_path)


def test_the_briefs_of_a_reject_and_of_red_gates_after_a_fixup_keep_their_text(tmp_path, capsys, registered):
    adapter = registered(ScriptedAdapter(verdict_sequence=["REJECT", "ACCEPT"], verdict_findings=FINDINGS))
    # Green on the build, red after the first fix-up, green again after the second.
    gate = "test ! -f fixup-1.txt || test -f fixup-2.txt || { echo RED-GATE-5127 fixup-2.txt is missing; exit 1; }"
    repo = make_repo(tmp_path, workflow(gate=gate))

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    assert (len(adapter.fixup_calls), len(adapter.verifier_calls)) == (2, 2)
    check("reject", prompts(adapter), repo, tmp_path)


def test_the_briefs_of_an_economy_chain_with_a_fixup_of_the_gates_and_a_reject_keep_their_text(tmp_path, capsys, registered):
    adapter = registered(
        ScriptedAdapter(
            files_by_ticket={"a": {"a.txt": "a\n"}, "b": {"b.txt": "b\n"}, "c": {"c.txt": "c\n"}},
            verdict_sequence=["REJECT", "ACCEPT"],
            verdict_findings=FINDINGS,
        )
    )
    gate = "test -f fixup-1.txt || { echo RED-GATE-4410 fixup-1.txt is missing; exit 1; }"
    repo = make_repo(tmp_path, workflow(mode="economy", gate=gate, tickets="abc"))

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    assert (len(adapter.fixup_calls), len(adapter.verifier_calls)) == (2, 2)
    check("economy-chain", prompts(adapter), repo, tmp_path)


def test_the_brief_of_the_second_stack_of_an_economy_chain_holds_the_fixups_of_the_first_stack(tmp_path, capsys, registered):
    # The fix-up of the stack `one` changes `b.txt`, a file of a ticket of the stack `two`, so the full verifier of `two` sees it.
    adapter = registered(
        ScriptedAdapter(
            files_by_ticket={"a": {"a.txt": "a\n"}, "b": {"b.txt": "b\n"}},
            fixup_files={"b.txt": "changed by the fix-up\n"},
            verdict_sequence=["REJECT", "ACCEPT", "ACCEPT"],
            verdict_findings=FINDINGS,
        )
    )
    text = workflow(mode="economy", gate="ls *.txt", tickets="") + (
        '\n[stacks.two]\nspecialist = "python-specialist"\nverifier = "python-verifier"\ngates = ["true"]\nhotspots = ["LICENSE"]\n'
        "gate-timeout = 600\nagent-timeout = 600\n"
        '\n[[tickets]]\nid = "a"\ntext = "Ticket a adds its file."\nstack = "python"\nblocked-by = []\n'
        '\n[[tickets]]\nid = "b"\ntext = "Ticket b adds its file."\nstack = "two"\nblocked-by = ["a"]\n'
    )
    repo = make_repo(tmp_path, text)

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, ""), err
    assert any("fix-ups of the stacks before this stack" in call.prompt for call in adapter.verifier_calls)
    check("economy-stacks", prompts(adapter), repo, tmp_path)


def test_the_briefs_of_a_ticket_after_a_plan_change_keep_their_text(tmp_path, capsys, registered):
    # The ticket `c` commits `c-early.txt` first, so its gate is red and its specialist continues with the plan change.
    files = {t: {f"{t}.txt": f"{t}\n"} for t in "abx"} | {"c": {"c-early.txt": "c\n"}}
    adapter = registered(ScriptedAdapter(files_by_ticket=files, continuation_files=[{"c.txt": "c\n"}]))
    gate = "test ! -f c-early.txt || test -f c.txt"

    def interrupt(request):
        if ticket_of(request) == "a":
            raise KeyboardInterrupt

    adapter.during_verifier = interrupt
    repo = make_repo(tmp_path, workflow(gate=gate, tickets="abc"))
    code, out, err = start(repo, capsys)
    assert code == 130, err
    adapter.during_verifier = None
    for calls in (adapter.calls, adapter.verifier_calls):
        calls.clear()
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    (repo / "workflow.toml").write_text(workflow(gate=gate, tickets="abxc"))

    code, out, err = resume_main(repo, capsys, run_dir.name, "--reviewed", "c")

    assert (code, err) == (0, "")
    assert any("## Plan change" in call.prompt for call in adapter.calls)
    assert any("## Plan change" in call.prompt for call in adapter.continuation_calls)
    check("plan-change", prompts(adapter), repo, tmp_path)


def test_the_verifier_brief_after_a_commit_of_the_coordinator_keeps_its_text(tmp_path, capsys, registered):
    adapter = registered(ScriptedAdapter(files={"other.txt": "other\n"}))
    repo = make_repo(
        tmp_path,
        workflow(gate="grep -qx rendered LICENSE").replace('hotspots = ["LICENSE", ".github/workflows/*"]', 'hotspots = ["LICENSE", "NOTICE"]'),
    )
    code, out, err = start(repo, capsys)
    assert code == 1
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    worktree = adapter.calls[0].cwd
    (worktree / "LICENSE").write_text("rendered\n")
    git(worktree, "add", "-A")
    render = commit_head(worktree, "-m", "chore(agents): render the agent pair again")[0]
    for calls in (adapter.calls, adapter.continuation_calls, adapter.fixup_calls, adapter.verifier_calls):
        calls.clear()

    code, out, err = resume_main(repo, capsys, run_dir.name, "--retry", "a", "--coordinator-commit", render)

    assert (code, err) == (0, "")
    check("coordinator", prompts(adapter), repo, tmp_path)


DELEGATION_DOC = """# Delegation

## Verification gates

### Python

Paths: `src/`

```
python3 -m pytest
```

### Docs

Paths: `docs/`

```
mkdocs build --strict
```

## Hotspots
"""


def test_the_briefs_of_the_verifier_brief_command_keep_their_text(tmp_path, capsys):
    repo = committed_repo(tmp_path / "repo", {"docs/agents/delegation.md": DELEGATION_DOC, "seed.txt": "seed\n"})
    git(repo, "checkout", "-q", "-b", "feat")
    (repo / "src").mkdir()
    (repo / "src" / "feature.py").write_text("def answer():\n    return 41\n")
    git(repo, "add", "-A")
    rejected, _ = commit_head(repo, "-m", "add the answer")
    (repo / "src" / "feature.py").write_text("def answer():\n    return 42\n")
    git(repo, "add", "-A")
    commit_head(repo, "-m", "correct the answer")
    findings = tmp_path / "findings.md"
    findings.write_text("- FINDING-8851 the answer is wrong.\n")
    found = {}
    for name, argv in {
        "full": ["full", "--repo", str(repo), "--branch", "feat", "--base", "main", "--task", "The answer is 42."],
        "full-stack": ["full", "--repo", str(repo), "--branch", "feat", "--task", "The answer is 42.", "--stack", "Docs"],
        "fixup": ["fixup", "--repo", str(repo), "--branch", "feat", "--rejected", rejected, "--findings", str(findings)],
        "fixup-stack": [
            "fixup", "--repo", str(repo), "--branch", "feat", "--rejected", rejected, "--findings", str(findings),
            "--authorised", "A missing test.", "--stack", "Docs", "--stack", "Python",
        ],
    }.items():
        assert brief.main(argv) == 0
        found[name] = [capsys.readouterr().out]
    check("command", found, repo, tmp_path)


def parameter_count(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    arguments = node.args
    names = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
    return len(names) + (arguments.vararg is not None) + (arguments.kwarg is not None)


def test_no_function_of_the_brief_module_takes_more_than_six_parameters():
    found = {}
    for node in ast.walk(ast.parse(BRIEF_MODULE.read_text())):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and parameter_count(node) > PARAMETER_LIMIT:
            found[node.name] = parameter_count(node)

    assert found == {}
