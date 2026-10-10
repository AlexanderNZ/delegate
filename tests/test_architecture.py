"""The dependency rule of the package: dependencies point inward.

The rule, in words:

* `run` and `definitions` are the two bounded contexts. They hold the domain.
  They never import `adapters`, `cli` or `docs`. The definitions context never
  imports `run`.
* `shared` holds what the two contexts share: the tier table. It imports no
  other layer, so every layer may import it.
* `docs` is the docs tooling: the reference generator and the neutrality check.
  It reads the other layers, and none of them imports it.
* `ports` holds the interfaces that the domain drives. It imports neither
  `adapters`, `cli` nor `docs`.
* `adapters` implement the ports, and `cli` drives the domain. Both point
  inward, so both may import the contexts and the ports. `adapters` never
  imports `cli` or `docs`.
* `cli` holds the command-line drivers: the `delegate` umbrella command, `agent-definitions`,
  `verifier-brief`, `delegate run`, `delegate status`, `delegate watch` and `delegate docs`. A driver
  is the composition point: it parses its arguments, makes the adapters (the git
  backend of the version-control port, the harness adapter), calls a use case and
  prints the result. No module outside `cli` parses command-line arguments: no
  `ArgumentParser`, no `parse_args` and no `sys.argv` outside `cli`.
* The run context never runs git. It keeps the work of a run through the
  version-control port, and only the git backend in `adapters` runs the
  command. A call to `subprocess` that names git breaks the rule. A call to
  `subprocess` for something else, such as a gate command, does not.

* The domain module of the run context, `run/domain.py`, holds the rules of a run
  as functions of plain values. It imports nothing but `__future__`,
  `collections.abc` and `typing`: no module of the package, no git, no harness,
  no file system, no subprocess. `DOMAIN_ALLOWED` lists the modules it may import.

The rules are in `FORBIDDEN`, the one place to change when a later ticket grows
the rule. The test reads the import statements with the `ast` module of the
standard library. It runs no code of the package and needs no other tool.

A layer is a subpackage directory of the package. A flat module is not part of a
layer. It joins the layer when a later ticket moves it into the subpackage. An
import is a violation by the name of its target, so `from delegate import
adapters` breaks the rule whether `adapters` is a module or a subpackage.
"""

import ast
import shutil
import tomllib
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "delegate"

# Layer -> the layers that its files never import.
FORBIDDEN = {
    "run": {"adapters", "cli", "docs"},
    "definitions": {"adapters", "cli", "docs", "run"},
    "ports": {"adapters", "cli", "docs"},
    "adapters": {"cli", "docs"},
    "shared": {"adapters", "cli", "docs", "run", "definitions", "ports"},
}

# The layers whose files never run git.
GIT_FREE_LAYERS = {"run"}

# The modules of the run context, by their path under the package.
RUN_MODULES = (
    "engine", "workflow", "journal", "lock", "reports", "brief", "guards", "runs", "status", "watch", "domain",
)

# The modules of the definitions context, by their path under the package.
DEFINITIONS_MODULES = ("declaration", "render", "validate", "bootstrap")

# The modules that the two contexts share: the tier table.
SHARED_MODULES = ("tiers",)

# The modules of the docs tooling, the supporting package that reads the code of the kit to write the docs and to check the kit.
DOCS_MODULES = ("reference", "neutrality")

# The modules of the command-line drivers, by their path under the package.
CLI_MODULES = ("delegate", "agent_definitions", "brief", "run_command", "status", "watch", "docs_command")

# The domain module of the run context: the rules of a run, as functions of plain values.
DOMAIN_MODULE = "run/domain.py"

# The only modules that the domain module imports. They hold no I/O. It imports nothing from the package.
DOMAIN_ALLOWED = {"__future__", "collections.abc", "typing"}


def layer_of(parts: tuple[str, ...]) -> str | None:
    """The layer of a file by its module path, or None for a file outside any layer."""
    return parts[1] if len(parts) > 2 else None


def targets(node: ast.AST, package: tuple[str, ...], name: str) -> list[tuple[str, ...]]:
    """The module paths that one import statement names."""
    if isinstance(node, ast.Import):
        return [tuple(alias.name.split(".")) for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        base = tuple(node.module.split(".")) if node.module else ()
        if node.level:
            base = package[: len(package) - (node.level - 1)] + base
        # `from delegate import adapters` imports the submodule `adapters`.
        return [base] + [base + (alias.name,) for alias in node.names]
    return []


def violations(package_dir: Path) -> list[str]:
    """Every import in the package that breaks `FORBIDDEN`, as `file:line: layer imports layer`."""
    name = package_dir.name
    found = []
    for path in sorted(package_dir.rglob("*.py")):
        relative = path.relative_to(package_dir.parent).with_suffix("")
        parts = relative.parts
        layer = layer_of(parts)
        rules = FORBIDDEN.get(layer) if layer else None
        if rules is None:
            continue
        package = parts[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for target in targets(node, package, name):
                if len(target) > 1 and target[0] == name and target[1] in rules:
                    found.append(f"{path.relative_to(package_dir.parent)}:{node.lineno}: {layer} imports {target[1]}")
    return sorted(set(found))


def _subprocess_names(tree: ast.AST) -> tuple[set[str], set[str]]:
    """The names under which a file reaches `subprocess`: the module aliases, and the functions imported from it."""
    modules: set[str] = set()
    functions: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules |= {alias.asname or alias.name for alias in node.names if alias.name == "subprocess"}
        elif isinstance(node, ast.ImportFrom) and node.module == "subprocess" and not node.level:
            functions |= {alias.asname or alias.name for alias in node.names}
    return modules, functions


def _names_git(node: ast.AST) -> bool:
    """Tell whether an argument of a call holds the word `git` as a command: `"git"`, or a string that starts with `git `."""
    return any(
        isinstance(part, ast.Constant) and isinstance(part.value, str) and (part.value == "git" or part.value.startswith("git "))
        for part in ast.walk(node)
    )


def git_calls(package_dir: Path) -> list[str]:
    """Every call to `subprocess` that names git in a file of the run context, as `file:line: run context runs git`."""
    found = []
    for path in sorted(package_dir.rglob("*.py")):
        parts = path.relative_to(package_dir.parent).with_suffix("").parts
        if layer_of(parts) not in GIT_FREE_LAYERS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        modules, functions = _subprocess_names(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            reaches = (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in modules) or (
                isinstance(func, ast.Name) and func.id in functions
            )
            if reaches and any(_names_git(argument) for argument in [*node.args, *(k.value for k in node.keywords)]):
                found.append(f"{path.relative_to(package_dir.parent)}:{node.lineno}: run context runs git")
    return sorted(set(found))


def planted_copy(tmp_path: Path, where: str, source: str) -> Path:
    """A copy of the real package with one extra file, so the real tree stays untouched."""
    copy = tmp_path / PACKAGE.name
    shutil.copytree(PACKAGE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    planted = copy / where
    planted.parent.mkdir(parents=True, exist_ok=True)
    init = planted.parent / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    planted.write_text(source, encoding="utf-8")
    return copy


def test_the_package_follows_the_dependency_rule():
    assert violations(PACKAGE) == []


@pytest.mark.parametrize(
    ("where", "source", "layer", "forbidden"),
    [
        ("definitions/bad.py", "from delegate.adapters import Adapter\n", "definitions", "adapters"),
        ("definitions/bad.py", "from delegate import cli\n", "definitions", "cli"),
        ("definitions/bad.py", "import delegate.cli\n", "definitions", "cli"),
        ("definitions/bad.py", "from ..adapters import Adapter\n", "definitions", "adapters"),
        ("definitions/bad.py", "from .. import cli\n", "definitions", "cli"),
        ("definitions/bad.py", "from delegate.run import engine\n", "definitions", "run"),
        ("definitions/bad.py", "from delegate.run.domain import MODES\n", "definitions", "run"),
        ("definitions/bad.py", "from ..run import workflow\n", "definitions", "run"),
        ("definitions/bad.py", "from .. import run\n", "definitions", "run"),
        ("definitions/render.py", "from ..run.engine import run_workflow\n", "definitions", "run"),
        ("definitions/bad.py", "from delegate.docs import reference\n", "definitions", "docs"),
        ("shared/bad.py", "from delegate.run import domain\n", "shared", "run"),
        ("shared/bad.py", "from ..definitions import render\n", "shared", "definitions"),
        ("shared/bad.py", "from delegate.adapters import get\n", "shared", "adapters"),
        ("shared/bad.py", "from .. import cli\n", "shared", "cli"),
        ("shared/bad.py", "from delegate.ports import vcs\n", "shared", "ports"),
        ("shared/tiers.py", "from ..docs import reference\n", "shared", "docs"),
        ("run/bad.py", "from delegate.docs import neutrality\n", "run", "docs"),
        ("adapters/bad.py", "from delegate.docs import reference\n", "adapters", "docs"),
        ("ports/bad.py", "from ..docs import reference\n", "ports", "docs"),
        ("run/bad.py", "from delegate.adapters import Adapter\n", "run", "adapters"),
        ("run/bad.py", "def late():\n    from ..cli import main\n", "run", "cli"),
        ("ports/bad.py", "from delegate.adapters import Adapter\n", "ports", "adapters"),
        ("ports/bad.py", "from .. import cli\n", "ports", "cli"),
        ("adapters/bad.py", "from delegate.cli import main\n", "adapters", "cli"),
        ("adapters/bad.py", "from .. import cli\n", "adapters", "cli"),
        ("run/engine.py", "from delegate.adapters import get\n", "run", "adapters"),
        ("run/engine.py", "from .. import adapters\n", "run", "adapters"),
        ("run/workflow.py", "from ..adapters import registered_names\n", "run", "adapters"),
        ("run/status.py", "from ..adapters.git import GitVersionControl\n", "run", "adapters"),
        ("run/watch.py", "from delegate.adapters.git import GitVersionControl\n", "run", "adapters"),
        ("run/brief.py", "from ..cli import main\n", "run", "cli"),
    ],
)
def test_a_planted_bad_import_fails_the_rule(tmp_path, where, source, layer, forbidden):
    copy = planted_copy(tmp_path, where, source)

    found = violations(copy)

    assert len(found) == 1
    assert found[0].endswith(f"{layer} imports {forbidden}")
    assert found[0].startswith(f"delegate/{where}:")


def test_an_inward_import_passes_the_rule(tmp_path):
    source = "from delegate.ports import something\nfrom ..definitions import other\nfrom . import sibling\n"
    copy = planted_copy(tmp_path, "run/fine.py", source)

    assert violations(copy) == []


def test_an_outer_layer_may_import_inward(tmp_path):
    source = "from delegate.run import something\nfrom delegate.ports.harness import other\n"
    copy = planted_copy(tmp_path, "adapters/fine.py", source)

    assert violations(copy) == []


@pytest.mark.parametrize(
    ("where", "source"),
    [
        ("run/fine.py", "from delegate.shared.tiers import Tiers\nfrom ..shared import tiers\n"),
        ("definitions/fine.py", "from delegate.shared.tiers import load_tiers\nfrom ..shared import tiers\n"),
        ("docs/fine.py", "from delegate.run import brief\nfrom delegate.definitions import render\nfrom delegate import cli\n"),
        ("adapters/fine.py", "from delegate.shared import tiers\n"),
    ],
)
def test_both_contexts_and_the_outer_layers_may_import_the_shared_tier_table(tmp_path, where, source):
    copy = planted_copy(tmp_path, where, source)

    assert violations(copy) == []


def test_the_definitions_context_holds_its_modules_and_the_templates():
    present = {path.stem for path in (PACKAGE / "definitions").glob("*.py")}

    assert set(DEFINITIONS_MODULES) <= present
    assert {path.name for path in (PACKAGE / "definitions" / "templates").glob("*.md")} >= {
        "claude-code-specialist.md",
        "claude-code-verifier.md",
        "context-skill-section.md",
        "context-skill.md",
        "opencode-specialist.md",
        "opencode-verifier.md",
    }


def test_the_tier_table_is_a_shared_module_with_its_data_beside_it():
    assert {path.name for path in (PACKAGE / "shared").iterdir()} >= {"tiers.py", "tiers.toml"}


def test_the_docs_tooling_is_its_own_package():
    present = {path.stem for path in (PACKAGE / "docs").glob("*.py")}

    assert set(DOCS_MODULES) <= present


def test_the_moved_modules_are_not_flat_modules_of_the_package_any_more():
    flat = {path.stem for path in PACKAGE.glob("*.py")}

    assert flat.isdisjoint({*DEFINITIONS_MODULES, *SHARED_MODULES, *DOCS_MODULES})
    assert not (PACKAGE / "templates").exists()
    assert not (PACKAGE / "tiers.toml").exists()


def test_every_package_of_the_source_tree_is_in_the_packages_list_of_the_build():
    pyproject = tomllib.loads((PACKAGE.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    listed = set(pyproject["tool"]["setuptools"]["packages"])
    on_disk = {".".join(path.parent.relative_to(PACKAGE.parent).parts) for path in PACKAGE.rglob("__init__.py")}

    assert on_disk == listed


def test_the_ports_and_the_adapters_are_inside_the_layers_that_the_rule_covers():
    covered = {
        ".".join(path.relative_to(PACKAGE.parent).with_suffix("").parts)
        for layer in ("ports", "adapters")
        for path in (PACKAGE / layer).rglob("*.py")
    }

    assert {
        "delegate.ports.harness",
        "delegate.ports.vcs",
        "delegate.adapters.claude_code",
        "delegate.adapters.opencode",
        "delegate.adapters.git",
    } <= covered


def test_the_run_context_runs_no_git():
    assert git_calls(PACKAGE) == []


@pytest.mark.parametrize(
    ("where", "source"),
    [
        ("run/engine.py", 'import subprocess\nsubprocess.run(["git", "-C", ".", "status"])\n'),
        ("run/engine.py", 'import subprocess as sp\nsp.check_output(["git", "log"])\n'),
        ("run/engine.py", 'from subprocess import run\nrun(["git", "log"])\n'),
        ("run/engine.py", 'import subprocess\nsubprocess.run(["bash", "-c", "git push"], cwd=".")\n'),
        ("run/workflow.py", 'import subprocess\nsubprocess.run(args=["git", "gc"])\n'),
        ("run/guards.py", 'import subprocess\nsubprocess.run(["git", "-C", ".", "config", "core.hooksPath", "x"])\n'),
        ("run/brief.py", 'import subprocess\nsubprocess.run(["git", "diff"], capture_output=True)\n'),
        ("run/bad.py", 'import subprocess\ndef late():\n    subprocess.Popen(["git", "fetch"])\n'),
    ],
)
def test_a_planted_git_call_fails_the_rule(tmp_path, where, source):
    copy = planted_copy(tmp_path, where, source)

    found = git_calls(copy)

    assert len(found) == 1
    assert found[0].startswith(f"delegate/{where}:")
    assert found[0].endswith("run context runs git")


def test_a_gate_command_through_subprocess_passes_the_git_rule(tmp_path):
    source = 'import subprocess\nsubprocess.run(["bash", "-c", "python -m pytest"], cwd=".")\n'
    copy = planted_copy(tmp_path, "run/engine.py", source)

    assert git_calls(copy) == []


def test_the_adapters_may_run_git(tmp_path):
    copy = planted_copy(tmp_path, "adapters/fine.py", 'import subprocess\nsubprocess.run(["git", "status"])\n')

    assert git_calls(copy) == []


def test_the_run_context_holds_its_modules():
    present = {path.stem for path in (PACKAGE / "run").glob("*.py")}

    assert set(RUN_MODULES) <= present


def pure_imports(package_dir: Path) -> list[str]:
    """Every import in the domain module of the run context that is not on `DOMAIN_ALLOWED`, as `file:line: domain imports <module>`.

    A relative import, and an import of `delegate`, are never allowed: the domain
    module imports nothing from the package, not even a module of its own context.
    """
    path = package_dir / DOMAIN_MODULE
    if not path.exists():
        return [f"{package_dir.name}/{DOMAIN_MODULE}: the domain module is missing"]
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = ["." * node.level + (node.module or "")]
        else:
            continue
        for imported in names:
            if imported not in DOMAIN_ALLOWED:
                found.append(f"{package_dir.name}/{DOMAIN_MODULE}:{node.lineno}: domain imports {imported}")
    return sorted(set(found))


def test_the_domain_module_of_the_run_context_imports_nothing_that_does_io():
    assert pure_imports(PACKAGE) == []


@pytest.mark.parametrize(
    "source",
    [
        "import subprocess\n",
        "import os\n",
        "import shutil\n",
        "import json\n",
        "import tempfile\n",
        "from pathlib import Path\n",
        "from subprocess import run\n",
        "from delegate.ports import vcs\n",
        "from delegate.ports.harness import Adapter\n",
        "from delegate.adapters import git\n",
        "from ..ports import vcs\n",
        "from ..adapters.git import GitVersionControl\n",
        "from .engine import run_workflow\n",
        "from . import journal\n",
        "def late():\n    import subprocess\n",
    ],
)
def test_a_planted_import_in_the_domain_module_fails_the_rule(tmp_path, source):
    copy = planted_copy(tmp_path, DOMAIN_MODULE, source)

    found = pure_imports(copy)

    assert len(found) == 1
    assert found[0].startswith(f"delegate/{DOMAIN_MODULE}:")
    assert " domain imports " in found[0]


def test_the_standard_library_modules_of_pure_values_pass_the_domain_rule(tmp_path):
    source = "from __future__ import annotations\nfrom collections.abc import Sequence\nimport typing\n"
    copy = planted_copy(tmp_path, DOMAIN_MODULE, source)

    assert pure_imports(copy) == []


def test_a_package_without_the_domain_module_fails_the_rule(tmp_path):
    copy = planted_copy(tmp_path, "run/other.py", "")
    (copy / DOMAIN_MODULE).unlink(missing_ok=True)

    assert pure_imports(copy) == [f"delegate/{DOMAIN_MODULE}: the domain module is missing"]


def test_the_run_modules_are_not_flat_modules_of_the_package_any_more():
    flat = {path.stem for path in PACKAGE.glob("*.py")}

    assert flat.isdisjoint({"engine", "workflow", "journal", "lock", "reports", "guards", "runs"})


def test_the_command_line_drivers_are_in_the_cli_package_and_no_flat_module_is_left():
    present = {path.stem for path in (PACKAGE / "cli").glob("*.py")}

    assert set(CLI_MODULES) <= present
    assert {path.stem for path in PACKAGE.glob("*.py")} == {"__init__"}


def parsing(package_dir: Path) -> list[str]:
    """Every place outside `cli` that parses command-line arguments, as `file:line: <layer> parses command-line arguments`.

    It finds a call of `ArgumentParser`, a call of `parse_args` or `parse_known_args`,
    and a read of `sys.argv`. A module that only reads a parser that `cli` built,
    such as the reference generator, does none of these.
    """
    found = []
    for path in sorted(package_dir.rglob("*.py")):
        parts = path.relative_to(package_dir.parent).with_suffix("").parts
        if layer_of(parts) == "cli":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            called = node.func if isinstance(node, ast.Call) else None
            name = called.attr if isinstance(called, ast.Attribute) else called.id if isinstance(called, ast.Name) else None
            reads_argv = isinstance(node, ast.Attribute) and node.attr == "argv" and isinstance(node.value, ast.Name) and node.value.id == "sys"
            if name in ("ArgumentParser", "parse_args", "parse_known_args") or reads_argv:
                found.append(f"{path.relative_to(package_dir.parent)}:{node.lineno}: {layer_of(parts) or 'a flat module'} parses command-line arguments")
    return sorted(set(found))


def test_no_module_outside_cli_parses_command_line_arguments():
    assert parsing(PACKAGE) == []


@pytest.mark.parametrize(
    ("where", "source", "layer"),
    [
        ("run/bad.py", "import argparse\nparser = argparse.ArgumentParser()\n", "run"),
        ("run/bad.py", "from argparse import ArgumentParser\nparser = ArgumentParser()\n", "run"),
        ("run/status.py", "def main(argv):\n    return build().parse_args(argv)\n", "run"),
        ("definitions/bad.py", "def late(parser):\n    parser.parse_known_args()\n", "definitions"),
        ("docs/reference.py", "import sys\nargs = sys.argv[1:]\n", "docs"),
        ("adapters/bad.py", "from sys import argv\nimport sys\nsys.argv\n", "adapters"),
        ("bad.py", "import argparse\nargparse.ArgumentParser(prog='x')\n", "a flat module"),
    ],
)
def test_a_planted_parser_outside_cli_fails_the_rule(tmp_path, where, source, layer):
    copy = planted_copy(tmp_path, where, source)

    found = parsing(copy)

    assert len(found) == 1
    assert found[0].startswith(f"delegate/{where}:")
    assert found[0].endswith(f"{layer} parses command-line arguments")


def test_a_parser_inside_cli_passes_the_rule(tmp_path):
    copy = planted_copy(tmp_path, "cli/fine.py", "import argparse\nargparse.ArgumentParser().parse_args()\n")

    assert parsing(copy) == []
