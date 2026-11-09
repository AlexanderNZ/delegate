"""A failed append or a torn last line does not make a journal unreadable (#191).

The journal class is the interface of the first cases: they append to a real file. The other cases call `delegate.main`
against a real temporary git repository.
"""

import errno
import json
from pathlib import Path

import pytest

from delegate.cli import delegate
from delegate.run.journal import Journal, read_events

from .test_run_multi import HEAD, ticket
from .test_run_resume import cut_journal_after
from .support import without_take_over
from .support import (
    bare_repo, finished_run, journal_path, make_repo, read_journal_file, resume_main, run_end, run_start, step_start, write_journal,
)


def test_an_append_that_cannot_be_serialised_leaves_a_readable_journal_and_the_next_append_gets_the_next_seq(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.append("run-start")

    with pytest.raises(TypeError):
        journal.append("step-start", bad=object())
    journal.append("step-end")

    assert [(e["seq"], e["event"]) for e in read_events(journal.path)] == [(1, "run-start"), (2, "step-end")]


def test_an_append_whose_write_fails_leaves_a_readable_journal_and_the_next_append_gets_the_next_seq(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.append("run-start")
    real_open = Path.open

    class FullDisk:
        """A file whose write fails after it took a part of the line, as a full disk does."""

        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.handle.close()

        def write(self, data):
            self.handle.write(data[:5])
            self.handle.flush()
            raise OSError(errno.ENOSPC, "No space left on device")

        def __getattr__(self, name):
            return getattr(self.handle, name)

    def open_that_fails(self, *args, **kwargs):
        return FullDisk(real_open(self, *args, **kwargs))

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_that_fails)
        with pytest.raises(OSError):
            journal.append("step-start")
    journal.append("step-end")

    assert [(e["seq"], e["event"]) for e in read_events(journal.path)] == [(1, "run-start"), (2, "step-end")]


def test_reopen_removes_a_torn_last_line_and_records_the_bytes_that_it_removed(tmp_path):
    path = write_journal_file(tmp_path, 2)
    complete = path.read_bytes()
    path.write_bytes(complete + b'{"seq": 3, "time": "2026-')

    journal, events = Journal.reopen(path)
    journal.append("step-end")

    assert [e["event"] for e in events] == ["run-start", "step-start", "journal-repaired"]
    assert events[-1]["bytes_removed"] == len(b'{"seq": 3, "time": "2026-')
    on_disk = read_events(path)
    assert [(e["seq"], e["event"]) for e in on_disk] == [(1, "run-start"), (2, "step-start"), (3, "journal-repaired"), (4, "step-end")]
    assert path.read_bytes().startswith(complete)


def test_reopen_of_a_journal_with_no_torn_line_writes_no_repair(tmp_path):
    path = write_journal_file(tmp_path, 2)
    before = path.read_bytes()

    _, events = Journal.reopen(path)

    assert [e["event"] for e in events] == ["run-start", "step-start"]
    assert path.read_bytes() == before


def write_journal_file(tmp_path, count):
    journal = Journal(tmp_path / "journal.jsonl")
    for name in ["run-start", "step-start", "step-end"][:count]:
        journal.append(name)
    return journal.path


def test_a_run_with_a_torn_last_line_resumes_and_its_journal_holds_journal_repaired(tmp_path, capsys, scripted):
    repo = make_repo(tmp_path, HEAD + ticket("a"))
    run_id = finished_run(repo, capsys)
    cut_journal_after(repo, run_id, "run-branch-advance")  # the file that a kill right after the event leaves
    path = journal_path(repo, run_id)
    torn = b'{"seq": 99, "time": "2026-10'
    path.write_bytes(path.read_bytes() + torn)

    code, out, err = resume_main(repo, capsys, run_id)

    assert (code, without_take_over(err)) == (0, "")
    events = read_journal_file(repo, run_id)
    repaired = [e for e in events if e["event"] == "journal-repaired"]
    assert [e["bytes_removed"] for e in repaired] == [len(torn)]
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert events[events.index(repaired[0]) + 1]["event"] == "resume"


def test_status_with_no_run_id_shows_the_good_run_when_another_journal_cannot_be_read(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    good = write_journal(repo, "20261001T000000Z-aaaa", run_start("20261001T000000Z-aaaa", "a"), step_start("a"), run_end("built", ["a"]))
    broken = journal_path(repo, "20261002T000000Z-bbbb")
    broken.parent.mkdir(parents=True)
    broken.write_text("this is not an event\n")

    code = delegate.main(["status", "--repo", str(repo)])
    captured = capsys.readouterr()

    assert code == 0
    assert captured.out.splitlines()[0].startswith("run 20261001T000000Z-aaaa: built")
    assert str(broken) in captured.err
    assert good.exists()


def test_status_with_no_run_id_names_each_unreadable_journal_when_no_journal_is_good(tmp_path, capsys):
    repo = bare_repo(tmp_path)
    broken = journal_path(repo, "20261002T000000Z-bbbb")
    broken.parent.mkdir(parents=True)
    broken.write_text("this is not an event\n")

    code = delegate.main(["status", "--repo", str(repo)])
    captured = capsys.readouterr()

    assert code == 2
    assert str(broken) in captured.err
    assert captured.out == ""
