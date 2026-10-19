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
# A machine can have nix with the flakes feature off.
FLAKES = ("--extra-experimental-features", "nix-command flakes")
SKILL = ROOT / "skills" / "agent-definitions" / "SKILL.md"


def _eval(attribute: str) -> object:
    result = subprocess.run(
        ["nix", *FLAKES, "eval", "--json", f"{ROOT}#{attribute}"], capture_output=True, text=True, check=True, cwd=ROOT,
    )
    return json.loads(result.stdout)


@outside_the_package
@pytest.mark.skipif(NIX is None, reason="nix is not installed, so the flake cannot be evaluated")
def test_the_package_runs_no_tests_and_the_tests_check_runs_the_suite_in_parallel():
    system = subprocess.run(
        ["nix", *FLAKES, "eval", "--impure", "--raw", "--expr", "builtins.currentSystem"], capture_output=True, text=True, check=True,
    ).stdout

    assert _eval(f"packages.{system}.agent-definitions.doInstallCheck") is False
    assert _eval(f"checks.{system}.tests.doInstallCheck") is True
    flags = _eval(f"checks.{system}.tests.pytestFlags")
    assert "-rs" in flags
    assert flags[flags.index("-n") + 1] == "auto"
    # The suite runs in parallel only when pytest-xdist is a build input of the check.
    assert any("pytest-xdist" in path for path in _eval(f"checks.{system}.tests.nativeBuildInputs"))


@outside_the_package
def test_the_skill_page_names_the_flake_check_that_runs_the_suite():
    text = SKILL.read_text()

    assert "runs in `nix flake check` as the `tests` check" in text
    assert "as the `agent-definitions` check" not in text


# With a denylist, the build scans a second source: the kit. The check finds the plugin directories in the
# parent of the kit root, so the second source keeps the layout of the repository (#102).
@outside_the_package
@pytest.mark.skipif(NIX is None, reason="nix is not installed, so the flake cannot be evaluated")
def test_with_a_denylist_the_build_gives_a_kit_root_whose_parent_holds_the_plugins(tmp_path):
    denylist = tmp_path / "denylist.txt"
    denylist.write_text("zorblax\n")
    expr = (
        f'let flake = builtins.getFlake "{ROOT}"; '
        "package = flake.packages.${builtins.currentSystem}.agent-definitions; "
        f"in (package.override {{ neutralityDenylist = {denylist}; }}).AGENT_DEFINITIONS_KIT_ROOT"
    )
    kit_root = Path(subprocess.run(
        ["nix", *FLAKES, "eval", "--impure", "--raw", "--expr", expr], capture_output=True, text=True, check=True, cwd=ROOT,
    ).stdout)

    assert sorted(p.name for p in kit_root.iterdir()) == ["agent-definitions", "agent-delegation"]
    assert (kit_root.parent / "plugins" / "claude-code" / "hooks" / "register.tsx").is_file()
    assert (kit_root.parent / ".claude-plugin" / "marketplace.json").is_file()
