"""verifier-brief selects the gate block for the stack that the change touches.

A monorepo delegation doc has one sub-heading for each stack under
"Verification gates", and each sub-heading names its path in backticks. The
brief must carry the blocks whose path holds a changed file, and no other
block. In a monorepo, a brief for a docs-only change once carried the
backend test suite, because the brief took the first block.

Every case runs against a scratch repository that real git commands build,
because the selection reads the changed files from real git output.
"""

import os
import subprocess

import pytest

from agent_definitions import brief

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


def _write(repo, name, text):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    _git(repo, "add", name)


def _branch(repo, name, files, base="main"):
    """A branch from base with one commit that writes each file."""
    _git(repo, "checkout", "-b", name, base)
    for path, text in files.items():
        _write(repo, path, text)
    _git(repo, "commit", "-m", f"change on {name}")
    sha = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "main")
    return sha


# The shape of a monorepo doc: prose under the gates heading, one
# sub-heading for each stack, and a sub-heading that claims no path. The
# frontend block holds a bash comment at the start of a line, which looks
# like a heading to a parser that does not track fences.
STACK_DOC = """# Working in this repository as an agent

## Verification gates

A change is proven at these seams, depending on which project it touches:

### Backend (`api/`)

```bash
cd api && ./gradlew test
```

This runs compile and tests.

### Frontend (`app/`)

```bash
# all four must pass
cd app && npm test
```

### Contract drift

After any change to the API's endpoints:

```bash
git diff --exit-code app/src/api/generated.ts
```

## Hotspots

```bash
not a gate
```
"""

BACKEND = "### Backend (`api/`)\n\n```bash\ncd api && ./gradlew test\n```"
FRONTEND = "### Frontend (`app/`)\n\n```bash\n# all four must pass\ncd app && npm test\n```"
CONTRACT = "### Contract drift\n\n```bash\ngit diff --exit-code app/src/api/generated.ts\n```"
NO_CLAIM = (
    "No gate block in `docs/agents/delegation.md` claims a changed file. "
    "Ask the coordinator for the gate commands before you report."
)


@pytest.fixture
def monorepo(tmp_path):
    r = tmp_path / "monorepo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.name", "Scratch Agent")
    _git(r, "config", "user.email", "scratch@example.invalid")
    _git(r, "config", "commit.gpgsign", "false")
    _write(r, "docs/agents/delegation.md", STACK_DOC)
    _write(r, "api/Main.java", "class Main {}\n")
    _write(r, "app/main.ts", "export {};\n")
    _git(r, "commit", "-m", "base")
    return r


def _gates(out):
    """The body of the brief's last section, "## Gates"."""
    head, sep, body = out.rpartition("## Gates\n\n")
    assert sep, out
    return body.rstrip("\n")


def _full(capsys, repo, branch, *extra):
    code = brief.main(["full", "--repo", str(repo), "--branch", branch, "--task", "t", *extra])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_a_docs_only_change_does_not_get_the_first_stack_block(monorepo, capsys):
    _branch(monorepo, "docs", {"docs/guide.md": "a guide\n"})
    code, out, _err = _full(capsys, monorepo, "docs")
    assert code == 0
    assert _gates(out) == NO_CLAIM


def test_a_change_under_one_stack_path_carries_only_that_stack_block(monorepo, capsys):
    _branch(monorepo, "api-only", {"api/Main.java": "class Main { int x; }\n"})
    _code, out, _err = _full(capsys, monorepo, "api-only")
    assert _gates(out) == BACKEND


def test_a_change_under_two_stack_paths_carries_both_blocks_in_doc_order(monorepo, capsys):
    _branch(
        monorepo,
        "both",
        {"app/main.ts": "export const x = 1;\n", "api/Main.java": "class Main { int x; }\n"},
    )
    _code, out, _err = _full(capsys, monorepo, "both")
    assert _gates(out) == BACKEND + "\n\n" + FRONTEND


def test_a_path_claims_a_directory_and_not_a_name_that_starts_the_same(monorepo, capsys):
    # `api/` claims api/Main.java. It does not claim apiary/hive.txt.
    _branch(monorepo, "apiary", {"apiary/hive.txt": "bees\n"})
    _code, out, _err = _full(capsys, monorepo, "apiary")
    assert _gates(out) == NO_CLAIM


def test_stack_selects_a_block_by_heading_text_and_overrides_the_path_match(monorepo, capsys):
    # The change is under api/, but the coordinator names another block. The
    # contract block sits after a fence that holds a bash comment line.
    _branch(monorepo, "api-only", {"api/Main.java": "class Main { int x; }\n"})
    _code, out, _err = _full(capsys, monorepo, "api-only", "--stack", "Contract drift")
    assert _gates(out) == CONTRACT


def test_stack_matches_a_heading_with_or_without_its_backticks(monorepo, capsys):
    _branch(monorepo, "docs", {"docs/guide.md": "a guide\n"})
    _code, plain, _err = _full(capsys, monorepo, "docs", "--stack", "Frontend (app/)")
    _code, ticked, _err = _full(capsys, monorepo, "docs", "--stack", "Frontend (`app/`)")
    assert _gates(plain) == FRONTEND
    assert _gates(ticked) == FRONTEND


def test_stack_given_twice_carries_both_blocks_in_the_order_given(monorepo, capsys):
    _branch(monorepo, "api-only", {"api/Main.java": "class Main { int x; }\n"})
    _code, out, _err = _full(
        capsys, monorepo, "api-only", "--stack", "Contract drift", "--stack", "Backend (api/)"
    )
    assert _gates(out) == CONTRACT + "\n\n" + BACKEND


def test_an_unknown_stack_exits_1_and_names_the_known_headings(monorepo, capsys):
    _branch(monorepo, "api-only", {"api/Main.java": "class Main { int x; }\n"})
    code, out, err = _full(capsys, monorepo, "api-only", "--stack", "Mobile")
    assert code == 1
    assert out == ""
    assert '"Mobile"' in err
    assert '"Backend (api/)", "Frontend (app/)", "Contract drift"' in err
    # A heading outside the gates section is not a gate block.
    assert "Hotspots" not in err


def test_the_gates_section_ends_at_the_next_peer_heading(monorepo, capsys):
    # The "## Hotspots" fence is after the gates section. No selection reaches it.
    _branch(monorepo, "everything", {"api/a": "1\n", "app/a": "1\n", "docs/a": "1\n"})
    _code, out, _err = _full(capsys, monorepo, "everything")
    assert "not a gate" not in out
    assert _gates(out) == BACKEND + "\n\n" + FRONTEND


FINDINGS = "1. The frontend test is missing.\n"


def _fixup(capsys, repo, rejected, branch, findings, *extra):
    code = brief.main(
        ["fixup", "--repo", str(repo), "--branch", branch, "--rejected", rejected,
         "--findings", str(findings), *extra]
    )
    out = capsys.readouterr()
    return code, out.out, out.err


def test_fixup_selects_the_gates_from_the_files_its_delta_changes(monorepo, capsys, tmp_path):
    # The rejected commit changed api/. The fix-up changes only app/, so the
    # scoped verifier runs the frontend gates.
    rejected = _branch(monorepo, "feat", {"api/Main.java": "class Main { int x; }\n"})
    _branch(monorepo, "feat-fix", {"app/main.ts": "export const x = 1;\n"}, base=rejected)
    findings = tmp_path / "findings.md"
    findings.write_text(FINDINGS)
    code, out, _err = _fixup(capsys, monorepo, rejected, "feat-fix", findings)
    assert code == 0
    assert _gates(out) == FRONTEND


def test_fixup_takes_the_stack_flag_and_the_placeholder_like_full(monorepo, capsys, tmp_path):
    rejected = _branch(monorepo, "feat", {"api/Main.java": "class Main { int x; }\n"})
    _branch(monorepo, "feat-fix", {"docs/guide.md": "a guide\n"}, base=rejected)
    findings = tmp_path / "findings.md"
    findings.write_text(FINDINGS)
    _code, unclaimed, _err = _fixup(capsys, monorepo, rejected, "feat-fix", findings)
    _code, chosen, _err = _fixup(
        capsys, monorepo, rejected, "feat-fix", findings, "--stack", "Backend (api/)"
    )
    code, out, err = _fixup(capsys, monorepo, rejected, "feat-fix", findings, "--stack", "Mobile")
    assert _gates(unclaimed) == NO_CLAIM
    assert _gates(chosen) == BACKEND
    assert code == 1 and out == "" and '"Contract drift"' in err


# A single-stack doc: prose and several fenced blocks under the gates
# heading, and no sub-heading. The brief carries the first block alone, in
# the same bytes as before stack selection existed.
SINGLE_DOC = """# Working in this repository as an agent

## Verification gates

Run these commands before a merge:

```
nix flake check
darwin-rebuild build --flake .
```

On the second host the gates add one command:

```
nix build --no-link .#default
```

## Hotspots
"""

SINGLE_GATES = "```bash\nnix flake check\ndarwin-rebuild build --flake .\n```"


@pytest.mark.parametrize(
    "doc",
    [
        SINGLE_DOC,
        # Sub-headings that claim no path keep the old behaviour too.
        SINGLE_DOC.replace("Run these commands", "### First host\n\nRun these commands").replace(
            "On the second host", "### Second host\n\nOn the second host"
        ),
    ],
    ids=["no-sub-headings", "sub-headings-without-paths"],
)
def test_a_doc_with_no_path_claim_gives_the_first_block_byte_for_byte(monorepo, capsys, tmp_path, doc):
    (monorepo / "docs" / "agents" / "delegation.md").write_text(doc)
    _git(monorepo, "commit", "-am", "single block doc")
    rejected = _branch(monorepo, "feat", {"api/Main.java": "class Main { int x; }\n"})
    _branch(monorepo, "feat-fix", {"docs/guide.md": "a guide\n"}, base=rejected)
    findings = tmp_path / "findings.md"
    findings.write_text(FINDINGS)
    _code, full, _err = _full(capsys, monorepo, "feat")
    _code, fixup, _err = _fixup(capsys, monorepo, rejected, "feat-fix", findings)
    assert full.endswith("\n\n## Gates\n\n" + SINGLE_GATES + "\n")
    assert fixup.endswith("\n\n## Gates\n\n" + SINGLE_GATES + "\n")
