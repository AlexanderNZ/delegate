"""The repository root that the docs tests read.

A Nix build copies only the package. On Linux the build directory is short
(/build/source), so the test files have fewer than five parents. The root must
then be a path with no README, so the docs tests skip and do not crash.
"""

from pathlib import Path

from .pages import README, repository_root


def test_the_root_of_a_short_package_path_has_no_readme():
    root = repository_root(Path("/build/source/tests/test_docs.py"))

    assert not (root / "README.md").is_file()


def test_the_root_of_the_working_copy_holds_the_readme():
    assert repository_root(Path(__file__)) / "README.md" == README
