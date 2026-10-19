"""The three new lines of `delegate run`, frame 1k of the design (#119).

The spec is `design/design_handoff_delegate_terminal_output/docs/copy-deck.md` (run start, run end):

* When the run holds its lock and its journal records the start, after `run <id>` and `journal <path>`:
  `follow it with delegate watch · pause it with delegate pause <short id>`.
* At the end: `run <result> · <used> · <n> built · <n> failed · <n> skipped · <new> new · <cache> cache`.
* After each failed step: `retry with delegate run --resume <short id> --retry <ticket>`.

The per-ticket lines on stderr do not change. On a terminal, the result has the colour of its role (tokens.md). With
`NO_COLOR`, or when stdout is not a terminal, the lines have no SGR code.

Each case runs `delegate.main(["run", ...])` against a real temporary git repository, with the scripted adapter. The
system boundaries are faked: the terminal (a standard output whose `isatty` is true), the locale and `NO_COLOR`.
"""

from __future__ import annotations

import re
import sys

import pytest

from delegate import adapters

from .support import ScriptedAdapter, Terminal, make_repo, read_journal, resume_main, run_main, ticket_of
from .test_run_multi import HEAD, ticket
from .test_run_pause import RUN_PAUSED, pause_during_the_specialist_of

# An SGR sequence: the paint of a role.
SGR = re.compile(r"\x1b\[[0-9;]*m")

# A duration in the short form of `format_duration`: `45s`, `12m05s`, `2h03m`.
USED = r"\d+[smhd](?:\d\d[smh])?"


@pytest.fixture(autouse=True)
def plain_environment(monkeypatch):
    """A UTF-8 locale, so the separator is `·`, no `NO_COLOR`, and no title sequence on a terminal."""
    monkeypatch.setenv("LC_ALL", "en_NZ.UTF-8")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("DELEGATE_NO_TITLE", "1")


@pytest.fixture
def scripted(git_identity):
    adapter = ScriptedAdapter(files_by_ticket={t: {f"{t}.txt": f"{t}\n"} for t in "abc"})
    adapters.register("scripted", adapter)
    yield adapter
    adapters.unregister("scripted")


def start(repo, capsys) -> tuple[int, str, str]:
    return run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))


def run_id_of(out: str) -> str:
    return next(e for e in read_journal(out) if e["event"] == "run-start")["run_id"]


def short(run_id: str) -> str:
    return run_id.rsplit("-", 1)[-1]


def words(out: str) -> list[str]:
    """The lines of the output of a run with no run id, no path and no time used: the words that two runs share."""
    run_id = out.split("\n", 1)[0].removeprefix("run ")
    out = out.replace(run_id, "<id>").replace(f" {short(run_id)}", " <short>")
    return [re.sub(rf"/\S+|· {USED} ·", "", line) for line in out.splitlines()]


def test_a_built_run_prints_the_follow_hint_after_the_journal_and_the_summary_at_the_end(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)))

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    run_id = run_id_of(out)
    lines = out.splitlines()
    assert lines[:2] == [f"run {run_id}", f"journal {repo / '.git' / 'delegate' / 'runs' / run_id / 'journal.jsonl'}"]
    assert lines[2] == f"follow it with delegate watch · pause it with delegate pause {short(run_id)}"
    assert len(lines) == 4, out
    assert re.fullmatch(rf"run built · {USED} · 2 built · 0 failed · 0 skipped · 0 new · 0 cache", lines[3]), lines[3]
    assert "\x1b" not in out


def test_a_failed_run_prints_the_summary_and_a_retry_line_and_keeps_the_per_ticket_lines_on_stderr(tmp_path, capsys, scripted):
    scripted.failing_tickets = {"a"}
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b", ("a",)) + ticket("c"))

    code, out, err = start(repo, capsys)

    assert code == 1
    run_id = run_id_of(out)
    lines = out.splitlines()
    assert lines[2] == f"follow it with delegate watch · pause it with delegate pause {short(run_id)}"
    assert re.fullmatch(rf"run failed · {USED} · 1 built · 1 failed · 1 skipped · 0 new · 0 cache", lines[3]), lines[3]
    assert lines[4:] == [f"retry with delegate run --resume {short(run_id)} --retry a"]
    errors = err.splitlines()
    assert len(errors) == 2, err
    assert errors[0].startswith("delegate run: ticket a: ")
    assert errors[1] == "delegate run: ticket b: skipped: blocked by a, which failed"
    assert "\x1b" not in out + err


def test_a_paused_run_prints_the_summary_before_the_line_that_tells_how_to_go_on(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a") + ticket("b"))
    pause_during_the_specialist_of(repo, capsys, scripted, "a")

    code, out, err = start(repo, capsys)

    assert (code, err) == (RUN_PAUSED, "")
    (run_dir,) = (repo / ".git" / "delegate" / "runs").iterdir()
    run_id = run_dir.name
    lines = out.splitlines()
    # The pause came from a second command while the specialist of a worked, so the start lines went to that read.
    assert len(lines) == 2, out
    assert re.fullmatch(rf"run paused · {USED} · 0 built · 0 failed · 0 skipped · 0 new · 0 cache", lines[0]), lines[0]
    assert lines[1].startswith("paused before the gate ") and lines[1].endswith(f" of ticket a; go on with delegate run --resume {run_id}")


def test_the_start_lines_are_on_stdout_before_the_first_agent_starts(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    seen: list[str] = []
    scripted.on_specialist = lambda request: seen.append(capsys.readouterr().out)

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    (before,) = seen
    lines = before.splitlines()
    assert [line.split()[0] for line in lines] == ["run", "journal", "follow"]
    assert lines[2].endswith(f"pause it with delegate pause {short(lines[0].split()[1])}")
    assert out.startswith("run built · ")


def test_a_resume_that_the_engine_refuses_prints_no_start_line(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    _, first, _ = start(repo, capsys)

    code, out, err = resume_main(repo, capsys, run_id_of(first), "--retry", "a")

    assert code == 1
    assert out == ""
    assert err.startswith("delegate run: ")


def test_on_a_terminal_the_result_has_the_colour_of_its_role_and_the_words_are_the_same(tmp_path, capsys, monkeypatch, scripted):
    scripted.failing_tickets = {"a"}
    workflow = HEAD + ticket("a") + ticket("b")
    _, plain, _ = start(make_repo(tmp_path / "pipe", workflow), capsys)
    repo = make_repo(tmp_path / "terminal", workflow)
    terminal = Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)

    code = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))[0]

    assert code == 1
    painted = terminal.getvalue().splitlines()
    # The words are the same as with no terminal: only the run id, the journal and the time differ between two runs.
    assert words(SGR.sub("", "\n".join(painted))) == words(plain)
    summary = next(line for line in painted if SGR.sub("", line).startswith("run failed"))
    assert summary.startswith("\x1b[31m\x1b[1mrun failed\x1b[0m\x1b[0m · ")  # the result is in `bad`, bold
    assert "\x1b[2m0 new · 0 cache\x1b[0m" in summary  # the token counts are faint
    assert [SGR.search(line) for line in painted[:3]] == [None] * 3  # the start lines are plain


def test_with_no_color_set_the_lines_on_a_terminal_have_no_sgr_code(tmp_path, capsys, monkeypatch, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    monkeypatch.setenv("NO_COLOR", "1")
    terminal = Terminal()
    monkeypatch.setattr(sys, "stdout", terminal)

    code = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))[0]

    assert code == 0
    assert "\x1b" not in terminal.getvalue()
    assert terminal.getvalue().splitlines()[-1].startswith("run built · ")


def test_with_a_locale_that_is_not_utf8_the_separator_is_the_ascii_glyph(tmp_path, capsys, monkeypatch, scripted):
    monkeypatch.setenv("LC_ALL", "C")
    repo = make_repo(tmp_path, HEAD + ticket("a"))

    code, out, err = start(repo, capsys)

    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert lines[2] == f"follow it with delegate watch . pause it with delegate pause {short(run_id_of(out))}"
    assert re.fullmatch(rf"run built \. {USED} \. 1 built \. 0 failed \. 0 skipped \. 0 new \. 0 cache", lines[3]), lines[3]
