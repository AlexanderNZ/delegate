"""The how-to page "Give the agents a command-line tool": its declaration gives the verifier the read subcommand and no other, and its workflow passes the dry run.

A coordinator copies the declaration of a page. An entry in `gateCommands` without the trailing `*` permits `codegraph explore`
alone, so the verifier cannot give it a question. An entry that names the tool and not the subcommand permits `codegraph sync`,
which writes in the index (#266). The case renders the declaration of the page, runs the rendered guard in bash on real
commands, and runs the dry run on the workflow of the page. No case needs `codegraph` on the path: the guard reads the text
of a command, and the dry run runs no `prepare` command.
"""

import json
import re
import subprocess
import tomllib

import pytest

from delegate.cli import delegate
from delegate.definitions.declaration import parse_declaration
from delegate.definitions.render import render, render_claude_code
from delegate.definitions.validate import split_frontmatter
from delegate.shared.tiers import load_tiers

from .pages import HOW_TO, ROOT, blocks, file_block, new_repo, outside_the_package, write_files
from .support import committed_repo

PAGE = HOW_TO / "give-the-agents-a-command-line-tool.md"
REFERENCE = ROOT / "docs" / "reference" / "workflow.md"


def _declaration_of_the_page() -> dict:
    return tomllib.loads(file_block(PAGE, "agents.toml").body)


def _guard_hook(gate_commands: list[str]) -> str:
    declaration = {"agents": {"python": {"description": "A python app.", "gateCommands": gate_commands}}}
    [agent] = parse_declaration(declaration)
    front, _body, err = split_frontmatter(render_claude_code(agent, load_tiers())["python-verifier.md"])
    assert err is None, err
    [bash] = [entry for entry in front["hooks"]["PreToolUse"] if entry["matcher"] == "Bash"]
    return bash["hooks"][0]["command"]


def _guard(hook: str, repo, command: str) -> subprocess.CompletedProcess:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(repo)})
    return subprocess.run(["bash", "-c", hook], input=payload, capture_output=True, text=True, cwd=repo)


@pytest.fixture
def hook_and_repo(tmp_path, git_identity):
    gates = _declaration_of_the_page()["agents"]["python"]["gateCommands"]
    return _guard_hook(gates), committed_repo(tmp_path / "repo", {"src/a.py": "x = 1\n"})


@outside_the_package
@pytest.mark.parametrize(
    "command",
    [
        "codegraph explore export command",
        'codegraph explore "how does the export command read the data set"',
        "cd src && codegraph explore export",
    ],
)
def test_the_verifier_guard_permits_the_read_subcommand_of_the_page_with_words_after_it(hook_and_repo, command):
    hook, repo = hook_and_repo

    result = _guard(hook, repo, command)

    assert (result.returncode, result.stderr) == (0, "")


@outside_the_package
@pytest.mark.parametrize(
    "command",
    ["codegraph sync", "codegraph init -y", "codegraph init -y .", "codegraph index", "codegraph explore {a,b}"],
)
def test_the_verifier_guard_denies_each_subcommand_that_writes_and_each_word_that_can_expand(hook_and_repo, command):
    hook, repo = hook_and_repo

    result = _guard(hook, repo, command)

    assert result.returncode == 2, (command, result.stderr)
    assert "verifier guard" in result.stderr


@outside_the_package
def test_the_entry_of_the_page_names_the_subcommand_and_ends_with_a_star():
    gates = _declaration_of_the_page()["agents"]["python"]["gateCommands"]

    assert "codegraph explore *" in gates
    assert not [gate for gate in gates if gate.split()[0] == "codegraph" and gate != "codegraph explore *"]


@outside_the_package
def test_without_the_star_the_verifier_cannot_give_the_subcommand_a_question():
    """The test of the guard has teeth: the entry with no star is the defect that the page warns about."""
    hook = _guard_hook(["codegraph explore"])

    result = _guard(hook, ROOT, "codegraph explore export command")

    assert result.returncode == 2


@outside_the_package
def test_the_workflow_of_the_page_passes_the_dry_run_with_the_declaration_of_the_page(
    tmp_path, home, git_identity, claude_stand_in, capsys
):
    repo = new_repo(tmp_path / "repo")
    assert sorted(write_files(PAGE, repo)) == [".gitignore", "agents.toml", "settings.json", "workflow.toml"]
    agents = repo / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name, content in render(parse_declaration(_declaration_of_the_page()), load_tiers())["claude-code"].items():
        (agents / name).write_text(content)

    code = delegate.main(["run", str(repo / "workflow.toml"), "--repo", str(repo), "--dry-run"])

    out, err = capsys.readouterr()
    assert (code, err) == (0, "")
    lines = out.splitlines()
    assert not any(line.startswith("warn ") for line in lines)
    prepare = tomllib.loads((repo / "workflow.toml").read_text())["stacks"]["python"]["prepare"]
    assert [line for line in lines if line.startswith("stack python prepare: ")] == [
        f"stack python prepare: {command}" for command in prepare
    ]
    assert any("codegraph sync" in command for command in prepare)
    assert any("codegraph init -y" in command for command in prepare)
    assert any("$DELEGATE_REPO" in command for command in prepare)


@outside_the_package
def test_each_field_that_the_page_names_in_code_is_in_the_references():
    page = PAGE.read_text()
    toml = next(block.body for block in blocks(PAGE) if block.path == "workflow.toml")
    fields = set(re.findall(r"^([a-z-]+) = ", toml, flags=re.MULTILINE))
    fields |= set(re.findall(r"`(prepare|DELEGATE_REPO)`", page))
    reference = REFERENCE.read_text()

    assert {"prepare", "DELEGATE_REPO"} <= fields
    missing = sorted(field for field in fields if f"`{field}`" not in reference)
    assert missing == []


def _prepare_commands_with_the_recipe() -> dict[str, list[str]]:
    """The prepare commands of the guide and of the reference that mention the code index, by page."""
    found = {}
    for page, block in [
        ("guide", file_block(PAGE, "workflow.toml")),
        ("reference", next(b for b in blocks(REFERENCE) if b.lang == "toml" and ".codegraph" in b.body)),
    ]:
        stacks = tomllib.loads(block.body)["stacks"]
        [commands] = [stack["prepare"] for stack in stacks.values() if any(".codegraph" in c for c in stack["prepare"])]
        found[page] = commands
    return found


@outside_the_package
def test_the_worked_example_of_the_guide_lists_the_index_directory_in_its_gitignore():
    """The `.gitignore` inside `.codegraph/` holds `*` and `!.gitignore`, so git still shows the directory (#266b)."""
    lines = file_block(PAGE, ".gitignore").body.splitlines()

    assert ".codegraph/" in lines


@outside_the_package
def test_no_page_holds_the_recursive_copy_or_the_claim_that_the_inner_gitignore_makes_the_tree_clean():
    pages = sorted((ROOT / "docs").rglob("*.md"))
    recursive_copy = re.compile(r"cp -R \\?\"\$DELEGATE_REPO/\.codegraph\\?\"")

    assert [page.name for page in pages if recursive_copy.search(page.read_text())] == []
    assert [page.name for page in pages if "`.codegraph/.gitignore` makes git ignore" in page.read_text()] == []
    assert [page.name for page in pages if "has its own `.gitignore`, so the check passes" in page.read_text()] == []


@outside_the_package
@pytest.mark.parametrize("page", ["guide", "reference"])
def test_the_recipe_copies_only_the_database_and_is_safe_to_run_again(tmp_path, page):
    """A copy of the lock file makes `codegraph sync` fail; a second run must not nest `.codegraph/.codegraph`."""
    command = _prepare_commands_with_the_recipe()[page][-1]
    main, tree, bin_dir = tmp_path / "main", tmp_path / "tree", tmp_path / "bin"
    (main / ".codegraph").mkdir(parents=True)
    for name, text in [("codegraph.db", "index"), ("codegraph.lock", "4242"), ("codegraph.pid", "4242")]:
        (main / ".codegraph" / name).write_text(text)
    tree.mkdir()
    bin_dir.mkdir()
    stand_in = bin_dir / "codegraph"
    stand_in.write_text(
        '#!/bin/sh\n[ "$1" = sync ] || exit 3\n[ -f .codegraph/codegraph.lock ] && exit 1\n'
        "[ -f .codegraph/codegraph.db ] || exit 4\nprintf '*\\n!.gitignore\\n' > .codegraph/.gitignore\n"
    )
    stand_in.chmod(0o755)
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "DELEGATE_REPO": str(main)}

    codes = [
        subprocess.run(["bash", "-c", command], cwd=tree, env=env, capture_output=True, text=True).returncode
        for _ in range(2)
    ]

    assert codes == [0, 0]
    assert sorted(path.name for path in (tree / ".codegraph").iterdir()) == [".gitignore", "codegraph.db"]
