"""Behaviour tests for the declaration options that `bootstrap` takes.

`--output-language`, `--tracked-file-build` and `--get-only-command` write the
neutral declaration options, so a repository need not edit its declaration by
hand after each bootstrap. Every case goes through `cli.main`. The expected
text comes from the specification: the ASD-STE100 sentence, the flake reason,
and the declaration keys.
"""

import shlex
import tomllib

from delegate.cli.agent_definitions import main
from tests.test_bootstrap import _agent_files, _args, _paths, repo  # noqa: F401  (repo is a fixture)

STE = "Write in ASD-STE100 Simplified Technical English."
FLAKE_REASON = "An unstaged file is invisible to a flake."


def _all_options():
    return (
        "--output-language", "ste",
        "--tracked-file-build",
        "--get-only-command", "./scripts/api",
        "--gate-command", "npm test",
    )


def _rerun_argv(declaration_text: str, repo) -> list[str]:
    """The re-run command from the comment at the top of a generated file."""
    lines = []
    for line in declaration_text.splitlines():
        if line.startswith("#   agent-definitions "):
            lines.append(line.removeprefix("#   "))
            break
    [command] = lines
    argv = shlex.split(command)[1:]  # drop the program name
    assert argv[argv.index("--repo") + 1] == "."
    argv[argv.index("--repo") + 1] = str(repo)
    return argv


def test_output_language_ste_writes_the_field_and_the_pair_carries_the_rule(repo):
    assert main(_args(repo, extra=("--output-language", "ste"))) == 0
    p = _paths(repo)
    declaration = tomllib.loads(p["declaration"].read_text())
    assert declaration["agents"]["acme-api"]["outputLanguage"] == "ste"
    for label in ("cc-specialist", "cc-verifier", "oc-specialist", "oc-verifier"):
        assert STE in p[label].read_text(), label


def test_output_language_none_writes_the_field_and_the_pair_carries_no_rule(repo):
    assert main(_args(repo, extra=("--output-language", "none"))) == 0
    p = _paths(repo)
    declaration = tomllib.loads(p["declaration"].read_text())
    assert declaration["agents"]["acme-api"]["outputLanguage"] == "none"
    for label in ("cc-specialist", "cc-verifier", "oc-specialist", "oc-verifier"):
        assert "ASD-STE100" not in p[label].read_text(), label


def test_tracked_file_build_writes_true_and_the_specialist_carries_the_flake_reason(repo):
    assert main(_args(repo, extra=("--tracked-file-build",))) == 0
    p = _paths(repo)
    declaration = tomllib.loads(p["declaration"].read_text())
    assert declaration["agents"]["acme-api"]["trackedFileBuild"] is True
    assert FLAKE_REASON in p["cc-specialist"].read_text()
    assert "ASD-STE100" not in p["cc-specialist"].read_text(), "the flag sets no language"


def test_get_only_commands_reach_the_declaration_and_the_verifier_guard(repo):
    extra = ("--get-only-command", "./scripts/api", "--get-only-command", "./scripts/read")
    assert main(_args(repo, extra=extra)) == 0
    p = _paths(repo)
    declaration = tomllib.loads(p["declaration"].read_text())
    assert declaration["agents"]["acme-api"]["getOnlyCommands"] == ["./scripts/api", "./scripts/read"]
    assert "./scripts/api" in p["cc-verifier"].read_text()
    assert "./scripts/api" not in p["cc-specialist"].read_text()


def test_with_none_of_the_options_the_declaration_and_the_pair_carry_no_trace_of_them(repo):
    assert main(_args(repo)) == 0
    p = _paths(repo)
    declaration = p["declaration"].read_text()
    for token in ("outputLanguage", "trackedFileBuild", "getOnlyCommands",
                  "--output-language", "--tracked-file-build", "--get-only-command"):
        assert token not in declaration, token
        assert token not in p["skill"].read_text(), token
    for label in ("cc-specialist", "cc-verifier", "oc-specialist", "oc-verifier"):
        text = p[label].read_text()
        assert "ASD-STE100" not in text, label
        assert FLAKE_REASON not in text, label


def test_an_unknown_output_language_exits_one_names_the_permitted_values_and_writes_nothing(repo, capsys):
    assert main(_args(repo, extra=("--output-language", "english"))) == 1
    err = capsys.readouterr().err
    assert "english" in err
    assert "none" in err and "ste" in err
    assert _agent_files(repo) == []
    assert not _paths(repo)["declaration"].exists()


def test_the_rerun_comment_names_each_option_flag(repo):
    assert main(_args(repo, extra=_all_options())) == 0
    p = _paths(repo)
    for label in ("declaration", "skill"):
        text = p[label].read_text()
        assert "--output-language ste" in text, label
        assert "--tracked-file-build" in text, label
        assert "--get-only-command ./scripts/api" in text, label


def test_running_the_command_in_the_rerun_comment_writes_the_same_bytes(repo):
    assert main(_args(repo, extra=_all_options())) == 0
    before = {label: path.read_bytes() for label, path in _paths(repo).items()}
    argv = _rerun_argv(_paths(repo)["declaration"].read_text(), repo)
    assert main(argv) == 0
    after = {label: path.read_bytes() for label, path in _paths(repo).items()}
    assert after == before
