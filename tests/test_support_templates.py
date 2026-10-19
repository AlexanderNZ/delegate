"""The test templates of `tests/support.py`: a copy holds no lock file of a git process that ran in the template.

A commit can start `git maintenance run --auto` in the background. It holds `.git/objects/maintenance.lock` for a short
time, so a copy of the template can find the file and then lose it (`shutil.Error ... No such file or directory`).
"""

from pathlib import Path

from .support import copy_of


def _build_with_locks(repo: Path) -> None:
    (repo / ".git" / "objects").mkdir(parents=True)
    (repo / ".git" / "objects" / "maintenance.lock").write_text("")
    (repo / ".git" / "index.lock").write_text("")
    (repo / "seed.txt").write_text("seed\n")


def test_a_copy_of_a_template_holds_its_files_and_no_lock_file(tmp_path):
    dest = copy_of(tmp_path / "copy", ("support-templates-locks",), _build_with_locks)

    assert (dest / "seed.txt").read_text() == "seed\n"
    assert sorted(p.name for p in dest.rglob("*.lock")) == []
