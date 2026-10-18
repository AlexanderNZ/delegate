"""package.nix: the Nix package takes its version from pyproject.toml, so `scripts/release.py apply` keeps both in step."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from .pages import outside_the_package

PACKAGE_NIX = Path(__file__).resolve().parents[1] / "package.nix"


# The Nix build copies only the package, its tests and pyproject.toml, so package.nix is not in its source.
@outside_the_package
def test_package_nix_holds_no_literal_version_and_reads_it_from_pyproject():
    text = PACKAGE_NIX.read_text()

    assert re.findall(r'^\s*version\s*=\s*"[^"]*"\s*;', text, flags=re.MULTILINE) == []
    assert re.search(r"^\s*version\s*=\s*\(builtins\.fromTOML \(builtins\.readFile \./pyproject\.toml\)\)\.project\.version\s*;", text, flags=re.MULTILINE)


# The package that a consumer builds runs no tests; `checks.<system>.tests` runs the suite in parallel (#100).
# The Python builder of nixpkgs runs pytestCheckHook in the install check, so `doInstallCheck` tells it.
# CI runs the suite before each tag, and on a developer Mac the serial suite in the Nix sandbox took about 30 minutes.
ROOT = PACKAGE_NIX.parent
NIX = shutil.which("nix")


def _eval(attribute: str) -> object:
    result = subprocess.run(
        ["nix", "eval", "--json", f"{ROOT}#{attribute}"], capture_output=True, text=True, check=True, cwd=ROOT,
    )
    return json.loads(result.stdout)


@outside_the_package
@pytest.mark.skipif(NIX is None, reason="nix is not installed, so the flake cannot be evaluated")
def test_the_package_runs_no_tests_and_the_tests_check_runs_the_suite_in_parallel():
    system = subprocess.run(
        ["nix", "eval", "--impure", "--raw", "--expr", "builtins.currentSystem"], capture_output=True, text=True, check=True,
    ).stdout

    assert _eval(f"packages.{system}.agent-definitions.doInstallCheck") is False
    assert _eval(f"checks.{system}.tests.doInstallCheck") is True
    flags = _eval(f"checks.{system}.tests.pytestFlags")
    assert "-rs" in flags
    assert flags[flags.index("-n") + 1] == "auto"
