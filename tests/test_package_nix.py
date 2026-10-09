"""package.nix: the Nix package takes its version from pyproject.toml, so `scripts/release.py apply` keeps both in step."""

import re
from pathlib import Path

from .pages import outside_the_package

PACKAGE_NIX = Path(__file__).resolve().parents[1] / "package.nix"


# The Nix build copies only the package, its tests and pyproject.toml, so package.nix is not in its source.
@outside_the_package
def test_package_nix_holds_no_literal_version_and_reads_it_from_pyproject():
    text = PACKAGE_NIX.read_text()

    assert re.findall(r'^\s*version\s*=\s*"[^"]*"\s*;', text, flags=re.MULTILINE) == []
    assert re.search(r"^\s*version\s*=\s*\(builtins\.fromTOML \(builtins\.readFile \./pyproject\.toml\)\)\.project\.version\s*;", text, flags=re.MULTILINE)
