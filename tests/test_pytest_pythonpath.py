"""pyproject.toml: pytest imports the package from the `src` of the tree that it runs in, with no `.git` (#170).

The proof copy of a verifier (#153) has no `.git` entry. The dev shell puts `src` on `PYTHONPATH` only in a git
checkout, so in the proof copy a test did not import the package under review.
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


def _path_without_the_package() -> str:
    """The entries of PYTHONPATH that hold no `delegate` package.

    The dev shell gives only the `src` of the working copy, so no entry stays. A Nix build gives the test
    dependencies (pytest, pyyaml) on PYTHONPATH and in no other way, so those entries stay.
    """
    entries = os.environ.get("PYTHONPATH", "").split(os.pathsep)
    return os.pathsep.join(entry for entry in entries if entry and not (Path(entry) / "delegate").exists())


def test_a_copy_with_no_git_entry_runs_its_tests_against_its_own_src(tmp_path):
    copy = tmp_path / "proof"
    copy.mkdir()
    shutil.copy2(ROOT / "pyproject.toml", copy / "pyproject.toml")
    shutil.copytree(ROOT / "src", copy / "src", ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    (copy / "tests").mkdir()
    (copy / "tests" / "test_probe.py").write_text(PROBE)
    env = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "PYTEST_ADDOPTS")}
    remaining = _path_without_the_package()
    if remaining:
        env["PYTHONPATH"] = remaining

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "tests/test_probe.py"],
        cwd=copy, env=env, capture_output=True, text=True, timeout=300,
    )

    assert not (copy / ".git").exists()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
