"""verifier-brief builds a verifier's brief from git state.

Every case runs against a scratch repository that the fixture builds with
real git commands. The brief is a function of real git output, so a fixture
string would test the formatter and not the command.
"""

import os
import subprocess

import pytest

from delegate import brief

# The scratch repository must not read the user's git configuration. A global
# hook, a signing key, or a different default branch name would change what
# the test measures.
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(repo, *args):
    env = dict(os.environ)
    env.update(GIT_ENV)
    env["HOME"] = str(repo)
    r = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, env=env
    )
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout.strip()


def _commit(repo, name, text, message):
    (repo / name).write_text(text)
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


DELEGATION_DOC = """# Working in this repository as an agent

## Verification gates

```
nix flake check
darwin-rebuild build --flake .
```

## Hotspots
"""


class Scratch:
    """The repository shape a fix-up produces, plus the amend that breaks it."""

    def __init__(self, path, base, rejected, fixup, amended):
        self.path = path
        self.base = base
        self.rejected = rejected
        self.fixup = fixup
        self.amended = amended


@pytest.fixture
def scratch(tmp_path):
    r = tmp_path / "scratch"
    (r / "docs" / "agents").mkdir(parents=True)
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.name", "Scratch Agent")
    _git(r, "config", "user.email", "scratch@example.invalid")
    _git(r, "config", "commit.gpgsign", "false")
    (r / "docs" / "agents" / "delegation.md").write_text(DELEGATION_DOC)
    _git(r, "add", "docs")
    base = _commit(r, "README.md", "base\n", "base")

    _git(r, "checkout", "-b", "feat")
    rejected = _commit(r, "feature.py", "def answer():\n    return 41\n", "add the answer")
    fixup = _commit(r, "feature.py", "def answer():\n    return 42\n", "correct the answer")

    # A second branch that rewrote the rejected commit instead of adding to it.
    _git(r, "checkout", "-b", "amended", rejected)
    (r / "feature.py").write_text("def answer():\n    return 42\n")
    _git(r, "add", "feature.py")
    _git(r, "commit", "--amend", "-m", "add the answer, corrected")
    amended = _git(r, "rev-parse", "HEAD")

    # Work that lands on main after the branch starts. A two-dot diff shows it
    # as a removal; a three-dot diff does not.
    _git(r, "checkout", "main")
    _commit(r, "unrelated.py", "def other():\n    return 0\n", "unrelated work on main")
    return Scratch(r, base, rejected, fixup, amended)


def _run(capsys, argv):
    code = brief.main(argv)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_full_brief_has_the_task_the_diff_and_the_gates(scratch, capsys):
    code, out, _err = _run(
        capsys,
        ["full", "--repo", str(scratch.path), "--branch", "feat", "--task", "Ticket PROJ-7: verifier modes."],
    )
    assert code == 0
    assert "## Task" in out
    assert "Ticket PROJ-7: verifier modes." in out
    assert "## Diff" in out
    assert "+def answer():" in out
    assert "return 42" in out
    assert "## Gates" in out
    assert "nix flake check" in out


def test_a_full_brief_never_selects_fix_up_mode(scratch, capsys):
    # The verifier reads its mode from the findings heading. A full brief that
    # carries that heading would put the verifier in the wrong mode.
    _code, out, _err = _run(
        capsys,
        ["full", "--repo", str(scratch.path), "--branch", "feat", "--task", "t"],
    )
    assert "## Findings under verification" not in out


def test_full_brief_diff_holds_no_work_that_landed_on_the_base(scratch, capsys):
    # git diff base...branch is the three-dot form. The two-dot form reports
    # the base's later commits as removals, which reads as a deletion the
    # specialist never made.
    _code, out, _err = _run(
        capsys,
        ["full", "--repo", str(scratch.path), "--branch", "feat", "--task", "t"],
    )
    assert "unrelated.py" not in out


def test_full_brief_falls_back_to_a_placeholder_without_a_delegation_doc(scratch, capsys):
    (scratch.path / "docs" / "agents" / "delegation.md").unlink()
    _code, out, _err = _run(
        capsys,
        ["full", "--repo", str(scratch.path), "--branch", "feat", "--task", "t"],
    )
    assert "## Gates" in out
    assert "nix flake check" not in out
    assert "coordinator" in out.lower()


def test_task_is_read_from_a_file_when_it_starts_with_an_at_sign(scratch, capsys, tmp_path):
    task_file = tmp_path / "task.md"
    task_file.write_text("Ticket PROJ-7, with its comments, is the spec.\n")
    _code, out, _err = _run(
        capsys,
        ["full", "--repo", str(scratch.path), "--branch", "feat", "--task", f"@{task_file}"],
    )
    assert "Ticket PROJ-7, with its comments, is the spec." in out


def test_full_exits_non_zero_when_the_branch_holds_no_change(scratch, capsys):
    # A branch at the base has nothing to verify. An empty brief would send a
    # verifier to read a diff that does not exist.
    _git(scratch.path, "branch", "idle", scratch.base)
    code, out, err = _run(
        capsys,
        ["full", "--repo", str(scratch.path), "--branch", "idle", "--base", scratch.base, "--task", "t"],
    )
    assert code != 0
    assert out.strip() == ""
    assert "empty" in err


FINDINGS = """1. `answer()` returns 41. The ticket says 42.
   Evidence: `feature.py:2`.
2. No test covers the return value.
"""


def test_fixup_brief_holds_the_findings_verbatim_and_the_delta(scratch, capsys, tmp_path):
    findings = tmp_path / "findings.md"
    findings.write_text(FINDINGS)
    code, out, _err = _run(
        capsys,
        [
            "fixup",
            "--repo", str(scratch.path),
            "--branch", "feat",
            "--rejected", scratch.rejected,
            "--findings", str(findings),
        ],
    )
    assert code == 0
    assert "## Findings under verification" in out
    assert FINDINGS.rstrip("\n") in out, "the findings must reach the verifier unchanged"
    assert "## Delta" in out
    assert "-    return 41" in out
    assert "+    return 42" in out
    # The delta is the fix-up only. The rejected commit added the function, and
    # that addition belongs to the first pass.
    assert "+def answer():" not in out
    assert "## Gates" in out
    assert "nix flake check" in out


def test_fixup_brief_holds_the_authorised_additions_only_when_they_are_given(scratch, capsys, tmp_path):
    findings = tmp_path / "findings.md"
    findings.write_text(FINDINGS)
    args = [
        "fixup",
        "--repo", str(scratch.path),
        "--branch", "feat",
        "--rejected", scratch.rejected,
        "--findings", str(findings),
    ]
    _code, plain, _err = _run(capsys, args)
    assert "## Coordinator-authorised additions" not in plain
    _code, marked, _err = _run(capsys, args + ["--authorised", "A missing test-quality symlink."])
    assert "## Coordinator-authorised additions" in marked
    assert "A missing test-quality symlink." in marked


def test_fixup_exits_non_zero_when_the_tip_rewrote_the_rejected_commit(scratch, capsys, tmp_path):
    findings = tmp_path / "findings.md"
    findings.write_text(FINDINGS)
    code, out, err = _run(
        capsys,
        [
            "fixup",
            "--repo", str(scratch.path),
            "--branch", "amended",
            "--rejected", scratch.rejected,
            "--findings", str(findings),
        ],
    )
    assert code != 0
    assert out.strip() == "", "a failed brief prints nothing a verifier can read"
    assert scratch.rejected in err
    assert "amended" in err


def test_fixup_exits_non_zero_when_the_delta_is_empty(scratch, capsys, tmp_path):
    findings = tmp_path / "findings.md"
    findings.write_text(FINDINGS)
    code, out, err = _run(
        capsys,
        [
            "fixup",
            "--repo", str(scratch.path),
            "--branch", "feat",
            "--rejected", scratch.fixup,
            "--findings", str(findings),
        ],
    )
    assert code != 0
    assert out.strip() == ""
    assert "empty" in err
