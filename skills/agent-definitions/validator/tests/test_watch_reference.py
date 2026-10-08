"""The reference of `status` and `watch` names each option, each exit code and each problem reason.

The reference is outside the package source, as the README is, so a Nix build
that copies only the package skips these cases with a reason.
"""

from pathlib import Path

import pytest

from agent_definitions import status, watch

ROOT = Path(__file__).resolve().parents[4]
REFERENCE = ROOT / "docs" / "reference" / "status-and-watch.md"

outside_the_package = pytest.mark.skipif(
    not (ROOT / "README.md").is_file(), reason="the docs are outside the package source, as in a Nix build"
)


def options_of(parser):
    return [flag for action in parser._actions for flag in action.option_strings if flag.startswith("--") and flag != "--help"]  # noqa: SLF001


@outside_the_package
@pytest.mark.parametrize("flag", [*options_of(watch.build_parser()), *options_of(status.build_parser())])
def test_the_reference_names_each_option_of_status_and_watch(flag):
    assert f"`{flag}" in REFERENCE.read_text()


@outside_the_package
@pytest.mark.parametrize(("code", "reason"), sorted(watch.EXIT_CODES.items()))
def test_the_reference_lists_each_exit_code_of_watch_with_its_reason(code, reason):
    assert f"| {code} | {reason} |" in REFERENCE.read_text()


def test_each_reason_to_exit_has_its_own_exit_code():
    assert len(set(watch.EXIT_CODES)) == len(watch.EXIT_CODES) == 7
    assert sorted(watch.EXIT_CODES) == [0, 1, 2, 3, 4, 5, 6]


@outside_the_package
def test_the_readme_links_the_reference():
    assert "docs/reference/status-and-watch.md" in (ROOT / "README.md").read_text()
