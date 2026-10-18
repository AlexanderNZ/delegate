"""The verifier writes its verdict with the Write tool, and only in a temp directory (#98).

The verifier guard reads a Bash command as text, and it refuses a heredoc and
each backtick, so a Bash write of a verdict often fails. The renderer therefore
gives each verifier the Write tool. On Claude Code, a guard hook on Write permits
only an absolute path in a temp directory. On OpenCode, the `edit` map permits
only a path outside the project, and the `external_directory` map permits only a
temp directory. The verifier stays read-only on the repository and the worktree.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from delegate.definitions.validate import (
    opencode_permission_action,
    split_frontmatter,
    validate_claude_code_dir,
    validate_opencode_dir,
    validate_set,
)
from delegate.run.brief import verifier_run_sections

# A path in a repository. The guard reads the path as text, so the path need not exist. A real checkout of
# this repository can sit in a temp directory (a verifier runs the suite in a temp copy), so the test does not
# use the path of the checkout.
REPOSITORY_FILE = "/Users/dev/project/src/app.py"


def _frontmatter(path: Path) -> dict:
    fm, _body, err = split_frontmatter(path.read_text())
    assert err is None, err
    return fm


def _write_hook(rendered: Path) -> str:
    """The command of the PreToolUse hook on Write of the rendered Claude Code verifier."""
    fm = _frontmatter(rendered / "claude-code" / "java-spring-verifier.md")
    (entry,) = [e for e in fm["hooks"]["PreToolUse"] if e["matcher"] == "Write"]
    (hook,) = entry["hooks"]
    assert hook["type"] == "command"
    return hook["command"]


def _write(hook: str, tool_input: dict, tmpdir: str) -> subprocess.CompletedProcess:
    """Run the hook on a Write tool input, as Claude Code does."""
    payload = json.dumps({"tool_name": "Write", "tool_input": tool_input})
    return subprocess.run(
        ["bash", "-c", hook], input=payload, capture_output=True, text=True, env={**os.environ, "TMPDIR": tmpdir}
    )


def _edit(path: Path, change) -> None:
    fm, body, err = split_frontmatter(path.read_text())
    assert err is None, err
    change(fm)
    path.write_text("---\n" + yaml.safe_dump(fm, sort_keys=False, width=100000) + "---" + body)


def _codes(findings) -> list[str]:
    return sorted(f.code for f in findings)


# --- Claude Code ------------------------------------------------------------


def test_the_rendered_claude_code_verifier_has_the_write_tool_and_a_hook_on_write(rendered):
    fm = _frontmatter(rendered / "claude-code" / "java-spring-verifier.md")

    assert [t.strip() for t in fm["tools"].split(",")] == ["Read", "Grep", "Glob", "Bash", "Write"]
    assert [e["matcher"] for e in fm["hooks"]["PreToolUse"]] == ["Bash", "Write"]


@pytest.mark.parametrize(
    "file_path",
    ["/tmp/delegate-verify-x/verdict.json", "/private/tmp/v/verdict.json", "/var/folders/ab/T/delegate-verify-x/verdict.json"],
)
def test_the_write_guard_permits_a_file_in_a_temp_directory(rendered, tmp_path, file_path):
    result = _write(_write_hook(rendered), {"file_path": file_path, "content": "{}"}, f"{tmp_path}/")

    assert result.returncode == 0, result.stderr


def test_the_write_guard_permits_a_file_in_the_temp_directory_of_the_environment(rendered, tmp_path):
    # The engine makes the report directory with mkdtemp, which uses $TMPDIR. On a host where $TMPDIR is not
    # under one of the fixed prefixes (macOS gives the real path /private/var/folders/...), the guard must still
    # permit the report path.
    path = tmp_path / "delegate-verify-x" / "verdict.json"

    result = _write(_write_hook(rendered), {"file_path": str(path), "content": "{}"}, f"{tmp_path}/")

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "file_path",
    [
        pytest.param(REPOSITORY_FILE, id="a-file-in-the-repository"),
        pytest.param("verdict.json", id="a-relative-path"),
        pytest.param("tmp/verdict.json", id="a-relative-path-that-starts-like-a-temp-directory"),
        pytest.param("/tmp/../Users/dev/project/src/app.py", id="a-dot-dot-component"),
        pytest.param("$TMPDIR/verdict.json", id="a-variable-that-the-write-tool-does-not-expand"),
        pytest.param("/tmpfoo/verdict.json", id="a-directory-that-only-starts-with-tmp"),
    ],
)
def test_the_write_guard_denies_a_file_outside_a_temp_directory(rendered, tmp_path, file_path):
    result = _write(_write_hook(rendered), {"file_path": file_path, "content": "{}"}, f"{tmp_path}/")

    assert result.returncode == 2
    assert file_path in result.stderr
    assert "temp directory" in result.stderr


def test_a_temp_directory_of_slash_does_not_open_every_path(rendered):
    result = _write(_write_hook(rendered), {"file_path": REPOSITORY_FILE, "content": "{}"}, "/")

    assert result.returncode == 2


@pytest.mark.parametrize(
    "tool_input",
    [pytest.param({"content": "{}"}, id="no-file-path"), pytest.param({"file_path": ["/tmp/x"]}, id="a-file-path-that-is-not-text")],
)
def test_the_write_guard_fails_closed_on_an_input_with_no_file_path(rendered, tmp_path, tool_input):
    result = _write(_write_hook(rendered), tool_input, f"{tmp_path}/")

    assert result.returncode == 2
    assert "fails closed" in result.stderr


def test_a_claude_code_verifier_with_write_and_no_write_guard_fails_validation(rendered, tiers):
    path = rendered / "claude-code" / "java-spring-verifier.md"
    _edit(path, lambda fm: fm["hooks"]["PreToolUse"].pop())

    findings = validate_claude_code_dir(rendered / "claude-code", tiers)

    assert any(f.code == "VERIFIER_WRITE_TOOL" and "Write" in f.message for f in findings)


def test_the_write_guard_on_bash_does_not_guard_write(rendered, tiers):
    path = rendered / "claude-code" / "java-spring-verifier.md"

    def retarget(fm):
        fm["hooks"]["PreToolUse"][1]["matcher"] = "Bash"

    _edit(path, retarget)

    assert "VERIFIER_WRITE_TOOL" in _codes(validate_claude_code_dir(rendered / "claude-code", tiers))


# --- OpenCode ---------------------------------------------------------------


def test_the_rendered_opencode_verifier_can_write_only_outside_the_project_and_in_a_temp_directory(rendered):
    permission = _frontmatter(rendered / "opencode" / "java-spring-verifier.md")["permission"]

    # OpenCode gives the edit rule the path relative to the worktree, and the external_directory rule the
    # absolute parent directory with "/*" (packages/opencode/src/tool/write.ts and external-directory.ts @ v1.18.31).
    assert opencode_permission_action(permission, "edit", "../delegate-verify-x/verdict.json") == "allow"
    assert opencode_permission_action(permission, "external_directory", "/tmp/delegate-verify-x/*") == "allow"
    assert opencode_permission_action(permission, "external_directory", "/var/folders/ab/T/delegate-verify-x/*") == "allow"
    assert opencode_permission_action(permission, "edit", "src/main/App.java") == "deny"
    assert opencode_permission_action(permission, "edit", ".git/config") == "deny"
    assert opencode_permission_action(permission, "external_directory", "/Users/dev/project/src/*") == "deny"
    assert opencode_permission_action(permission, "external_directory", "/etc/*") == "deny"
    assert opencode_permission_action(permission, "write", "src/main/App.java") == "deny"


def test_the_validator_accepts_the_rendered_pair(rendered, tiers):
    assert validate_set(tiers, rendered / "claude-code", rendered / "opencode") == []


@pytest.mark.parametrize(
    "edit_map",
    [
        pytest.param("allow", id="edit-allow"),
        pytest.param({"*": "deny", "*.json": "allow"}, id="edit-allows-a-file-in-the-project"),
        pytest.param({"*": "allow"}, id="edit-catch-all-allow"),
        pytest.param({"*": "deny", "../*": "ask"}, id="edit-ask"),
    ],
)
def test_an_opencode_verifier_whose_edit_map_opens_the_project_fails_validation(rendered, tiers, edit_map):
    path = rendered / "opencode" / "java-spring-verifier.md"
    _edit(path, lambda fm: fm["permission"].__setitem__("edit", edit_map))

    assert "VERIFIER_WRITE_TOOL" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


@pytest.mark.parametrize(
    "directories",
    [
        pytest.param({"*": "allow"}, id="every-directory"),
        pytest.param({"*": "deny", "/tmp/*": "allow", "/Users/*": "allow"}, id="a-home-directory"),
        pytest.param("allow", id="allow"),
    ],
)
def test_an_opencode_verifier_whose_external_directory_map_opens_a_path_outside_temp_fails_validation(rendered, tiers, directories):
    path = rendered / "opencode" / "java-spring-verifier.md"
    _edit(path, lambda fm: fm["permission"].__setitem__("external_directory", directories))

    assert "VERIFIER_WRITE_TOOL" in _codes(validate_opencode_dir(rendered / "opencode", tiers))


# --- The brief --------------------------------------------------------------


@pytest.mark.parametrize("mode", ["full", "fix-up"])
def test_the_brief_tells_the_verifier_to_write_the_verdict_with_the_write_tool_first(mode, tmp_path):
    report = tmp_path / "verdict.json"
    brief = verifier_run_sections(tmp_path / "copy", report, mode)

    write_tool = brief.index("Write the report with the Write tool")
    printf_form = brief.index("printf '%s\\n' '<the JSON object>' >")
    assert write_tool < printf_form
    assert f"`file_path` `{report}`" in brief
