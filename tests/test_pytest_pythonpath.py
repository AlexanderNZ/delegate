"""The tests import the package from the `src` of the tree that they run in, with no `.git` (#170).

The proof copy of a verifier (#153) has no `.git` entry. The dev shell puts `src` on `PYTHONPATH` only in a git
checkout, so in the proof copy a test did not import the package under review. A Python process that a test starts
must also import the package from that `src`.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = '''\
from pathlib import Path

import delegate


def test_delegate_comes_from_the_src_of_this_copy():
    source = Path(__file__).resolve().parents[1] / "src"
    assert Path(delegate.__file__).resolve().is_relative_to(source), delegate.__file__
'''

CHILD_PROBE = '''\
import subprocess
import sys
from pathlib import Path


def test_a_child_python_imports_delegate_from_the_src_of_this_copy():
    source = Path(__file__).resolve().parents[1] / "src"
    done = subprocess.run(
        [sys.executable, "-c", "import delegate; print(delegate.__file__)"], capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr
    assert Path(done.stdout.strip()).resolve().is_relative_to(source), done.stdout
'''


def _path_without_the_package() -> str:
    """The entries of PYTHONPATH that hold no `delegate` package.

    The dev shell gives only the `src` of the working copy, so no entry stays. A Nix build gives the test
    dependencies (pytest, pyyaml) on PYTHONPATH and in no other way, so those entries stay.
    """
    entries = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    return os.pathsep.join(entry for entry in entries if entry and not (Path(entry) / "delegate").exists())


def _copy_with_no_git_entry(tmp_path: Path) -> Path:
    """A copy of the package source, its tests and its example, with no `.git` entry (as the proof copy)."""
    copy = tmp_path / "proof"
    copy.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", copy / "pyproject.toml")
    ignore = shutil.ignore_patterns("__pycache__", "*.egg-info")
    shutil.copytree(ROOT / "src", copy / "src", ignore=ignore)
    shutil.copytree(ROOT / "tests", copy / "tests", ignore=ignore)
    (copy / "examples").mkdir()
    shutil.copy2(ROOT / "examples" / "java-spring.toml", copy / "examples" / "java-spring.toml")
    (copy / "tests" / "test_probe.py").write_text(PROBE)
    (copy / "tests" / "test_child_probe.py").write_text(CHILD_PROBE)
    assert not (copy / ".git").exists()
    return copy


def _pytest_in(copy: Path, *tests: str) -> subprocess.CompletedProcess:
    """Run pytest in the copy, with no entry on PYTHONPATH that holds a `delegate` package."""
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTEST_ADDOPTS")}
    remaining = _path_without_the_package()
    if remaining:
        env["PYTHONPATH"] = remaining
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", *tests],
        cwd=copy, env=env, capture_output=True, text=True, timeout=300,
    )


def test_a_copy_with_no_git_entry_runs_its_tests_against_its_own_src(tmp_path):
    copy = _copy_with_no_git_entry(tmp_path)

    result = _pytest_in(copy, "tests/test_probe.py")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout


def test_a_python_process_that_a_test_starts_in_a_copy_with_no_git_entry_imports_the_src_of_the_copy(tmp_path):
    copy = _copy_with_no_git_entry(tmp_path)

    result = _pytest_in(copy, "-rA", "tests/test_child_probe.py", "tests/test_cli.py")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASSED tests/test_child_probe.py::test_a_child_python_imports_delegate_from_the_src_of_this_copy" in result.stdout
    # The test of the module entry point starts `python -m delegate.cli.agent_definitions` (#170).
    assert "PASSED tests/test_cli.py::test_module_entry_point_runs" in result.stdout
