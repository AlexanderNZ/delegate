"""The dependency rule of the package: dependencies point inward.

The rule, in words:

* `run` and `definitions` are the two bounded contexts. They hold the domain.
  They never import `adapters` or `cli`.
* `ports` holds the interfaces that the domain drives. It imports neither
  `adapters` nor `cli`.
* `adapters` implement the ports, and `cli` drives the domain. Both point
  inward, so both may import the contexts and the ports.

The rules are in `FORBIDDEN`, the one place to change when a later ticket grows
the rule. The test reads the import statements with the `ast` module of the
standard library. It runs no code of the package and needs no other tool.

A layer is a subpackage directory of the package. A flat module that carries the
name of a layer, such as `adapters.py` or `run.py`, is not part of that layer
yet. It joins the layer when a later ticket moves it into the subpackage. An
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
}


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
        if layer not in FORBIDDEN:
            continue
        package = parts[:-1]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            for target in targets(node, package, name):
                if len(target) > 1 and target[0] == name and target[1] in FORBIDDEN[layer]:
                    found.append(f"{path.relative_to(package_dir.parent)}:{node.lineno}: {layer} imports {target[1]}")
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
    source = "from delegate.run import something\nfrom delegate.ports import other\n"
    copy = planted_copy(tmp_path, "adapters/fine.py", source)

    assert violations(copy) == []
