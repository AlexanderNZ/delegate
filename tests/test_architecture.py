"""The dependency rule of the package: dependencies point inward.

The rule, in words:

* `run` and `definitions` are the two bounded contexts. They hold the domain.
  They never import `adapters` or `cli`.
* `ports` holds the interfaces that the domain drives. It imports neither
  `adapters` nor `cli`.
* `adapters` implement the ports, and `cli` drives the domain. Both point
  inward, so both may import the contexts and the ports. `adapters` never
  imports `cli`.
* The command-line driver is the composition point: it picks the adapter and
  hands it to the engine. The three commands that take the git backend, `brief`,
  `status` and `watch`, are thin modules at the top of the package for now. Each
  one makes the backend and calls the module of the same name in `run`, until a
  later ticket moves them into `cli`.
* The run context never runs git. It keeps the work of a run through the
  version-control port, and only the git backend in `adapters` runs the
  command. A call to `subprocess` that names git breaks the rule. A call to
  `subprocess` for something else, such as a gate command, does not.

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
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "delegate"

# Layer -> the layers that its files never import.
FORBIDDEN = {
    "run": {"adapters", "cli"},
    "definitions": {"adapters", "cli"},
    "ports": {"adapters", "cli"},
    "adapters": {"cli"},
}

# The layers whose files never run git.
GIT_FREE_LAYERS = {"run"}

# The modules of the run context, by their path under the package.
RUN_MODULES = (
    "engine", "workflow", "journal", "lock", "reports", "brief", "guards", "runs", "status", "watch",
)


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


def test_the_run_modules_are_not_flat_modules_of_the_package_any_more():
    flat = {path.stem for path in PACKAGE.glob("*.py")}

    assert flat.isdisjoint({"engine", "workflow", "journal", "lock", "reports", "guards", "runs"})


def test_a_flat_module_that_stands_in_for_a_run_module_is_only_a_thin_driver():
    # The flat `brief`, `status` and `watch` make the git backend and call the run module of the same name.
    for name in ("brief", "status", "watch"):
        tree = ast.parse((PACKAGE / f"{name}.py").read_text(encoding="utf-8"))
        defined = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}

        assert defined == {"main"}, name
