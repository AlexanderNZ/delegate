"""The repository root that the docs tests read.

The root is the directory above the tests directory. A Nix build copies only
the package, its tests and its example to /build/source, so there the root holds
no README, and the docs tests skip and do not crash.
"""

from pathlib import Path

from .pages import README, repository_root


def test_the_root_of_a_short_package_path_has_no_readme():
    root = repository_root(Path("/build/source/tests/test_docs.py"))

    assert not (root / "README.md").is_file()


def test_the_root_of_a_path_with_no_directory_above_the_tests_has_no_readme():
    root = repository_root(Path("/tests/test_docs.py"))

    assert not (root / "README.md").is_file()


def test_the_root_of_the_working_copy_holds_the_pyproject_and_the_readme():
    root = repository_root(Path(__file__))

    assert (root / "pyproject.toml").is_file()
    assert root / "README.md" == README
