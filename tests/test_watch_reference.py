"""The exit codes of `watch` are distinct, and the readme links the reference of `status` and `watch`.

The options and the exit-code table of the reference are generated; `test_docs.py` holds their drift tests.

The reference is outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

from pathlib import Path

import pytest

from delegate.run import watch

from .pages import repository_root

ROOT = repository_root(Path(__file__))

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


def test_each_reason_to_exit_has_its_own_exit_code():
    assert len(set(watch.EXIT_CODES)) == len(watch.EXIT_CODES) == 9
    # 130 is the exit status that a shell gives for SIGINT: Ctrl-C ends the watch. There is no code 8 (#118): with no
    # run id and more than one active run, a watch that is not on a terminal exits 2.
    assert sorted(watch.EXIT_CODES) == [0, 1, 2, 3, 4, 5, 6, 7, 130]


@outside_the_package
def test_the_site_navigation_lists_the_reference():
    """The README links the site, not each page, so the navigation is where a reader finds this page."""
    assert '"reference/status-and-watch.md"' in (ROOT / "zensical.toml").read_text()
