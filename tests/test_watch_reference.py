"""The exit codes of `watch` are distinct, and the readme links the reference of `status` and `watch`.

The options and the exit-code table of the reference are generated; `test_docs.py` holds their drift tests.

The reference is outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

from pathlib import Path

import pytest

from delegate import watch

from .pages import repository_root

ROOT = repository_root(Path(__file__))

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


def test_each_reason_to_exit_has_its_own_exit_code():
    assert len(set(watch.EXIT_CODES)) == len(watch.EXIT_CODES) == 7
    assert sorted(watch.EXIT_CODES) == [0, 1, 2, 3, 4, 5, 6]


@outside_the_package
def test_the_readme_links_the_reference():
    assert "docs/reference/status-and-watch.md" in (ROOT / "README.md").read_text()
