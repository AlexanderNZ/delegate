"""The OpenCode column override (ADR 0003).

The tier table is global. The OpenCode provider on one machine can belong to
one job, for example a company LLM gateway that the job configuration sets
up and that a job change removes. The override therefore lives with the
configuration that sets up the provider, and not in `tiers.toml`.

Two parts, and they are separate on purpose. The tier map moves a tier onto
another model. The allow list extends the set the validator accepts. A tier map
that extended the allow list by itself could never be wrong, so an override onto
a model that no provider serves would stay green.
"""

from __future__ import annotations

import pytest

from agent_definitions.cli import main
from agent_definitions.tiers import load_tiers
from agent_definitions.validate import split_frontmatter
from tests.conftest import EXAMPLE

GATEWAY = {
    "strong": "gateway/azure-claude-opus-5",
    "standard": "gateway/azure-claude-sonnet-4-6",
    "cheap": "gateway/azure-claude-haiku-4-5",
    "verifier": "gateway/azure-claude-opus-5",
}


def _fm(path):
    fm, _body, err = split_frontmatter(path.read_text())
    assert err is None, err
    return fm


def _model(path):
    return _fm(path)["model"]


def test_the_override_moves_only_the_named_tier_and_only_the_opencode_column():
    tiers = load_tiers().with_opencode_override({"strong": "gateway/azure-claude-opus-5"}, [])
    assert tiers.model_for("strong", "opencode") == "gateway/azure-claude-opus-5"
    assert tiers.model_for("strong", "claude-code") == "opus"
    assert tiers.model_for("standard", "opencode") == "anthropic/claude-sonnet-5"


def test_the_override_leaves_the_loaded_table_alone():
    """The defaults are the single source. A caller that overrides one Tiers
    must not change the table the next caller loads."""
    tiers = load_tiers()
    tiers.with_opencode_override(GATEWAY, list(GATEWAY.values()))
    assert tiers.model_for("strong", "opencode") == "anthropic/claude-opus-5"
    assert load_tiers().model_for("strong", "opencode") == "anthropic/claude-opus-5"


def test_the_tier_map_does_not_extend_the_allowed_set_by_itself():
    tiers = load_tiers().with_opencode_override({"strong": "gateway/azure-claude-opus-5"}, [])
    assert "gateway/azure-claude-opus-5" not in tiers.allowed_models["opencode"]


def test_the_allow_list_extends_the_defaults_and_does_not_replace_them():
    tiers = load_tiers().with_opencode_override({}, ["gateway/azure-claude-opus-5"])
    assert "gateway/azure-claude-opus-5" in tiers.allowed_models["opencode"]
    assert "anthropic/claude-opus-5" in tiers.allowed_models["opencode"]
    assert tiers.allowed_models["claude-code"] == load_tiers().allowed_models["claude-code"]


def test_an_unknown_tier_name_is_loud():
    with pytest.raises(ValueError, match="unknown tier"):
        load_tiers().with_opencode_override({"strongest": "gateway/x"}, [])


def test_render_writes_the_override_model_into_every_opencode_file(tmp_path, capsys):
    argv = ["render", str(EXAMPLE), "-o", str(tmp_path)]
    for tier, model in GATEWAY.items():
        argv += ["--opencode-model", f"{tier}={model}"]
    assert main(argv) == 0
    capsys.readouterr()
    files = sorted((tmp_path / "opencode").glob("*.md"))
    assert files, "the render wrote no OpenCode file"
    for f in files:
        assert _model(f).startswith("gateway/"), f"{f.name} kept {_model(f)}"
    # The Claude Code half carries no model at all, and the override must not
    # give it one.
    for f in sorted((tmp_path / "claude-code").glob("*.md")):
        assert "model" not in _fm(f)


def test_render_without_the_flag_keeps_the_defaults(tmp_path, capsys):
    assert main(["render", str(EXAMPLE), "-o", str(tmp_path)]) == 0
    capsys.readouterr()
    for f in sorted((tmp_path / "opencode").glob("*.md")):
        assert _model(f).startswith("anthropic/")


def test_validate_reports_bad_model_when_the_allow_list_is_not_extended(tmp_path, capsys):
    argv = ["render", str(EXAMPLE), "-o", str(tmp_path)]
    for tier, model in GATEWAY.items():
        argv += ["--opencode-model", f"{tier}={model}"]
    main(argv)
    capsys.readouterr()
    assert main(["validate", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "BAD_MODEL" in out
    assert "gateway/azure-claude-opus-5" in out


def test_validate_accepts_the_override_when_the_allow_list_is_extended(tmp_path, capsys):
    argv = ["render", str(EXAMPLE), "-o", str(tmp_path)]
    for tier, model in GATEWAY.items():
        argv += ["--opencode-model", f"{tier}={model}"]
    main(argv)
    capsys.readouterr()
    argv = ["validate", str(tmp_path)]
    for model in sorted(set(GATEWAY.values())):
        argv += ["--opencode-allow", model]
    assert main(argv) == 0
    assert capsys.readouterr().out.strip().endswith("ok")


def test_an_allow_list_entry_does_not_make_an_unknown_model_pass(tmp_path, capsys):
    """The allow list names what a provider serves. A model outside it stays a
    finding, which is what keeps the agent-contract check red on a typo."""
    # The example declares the standard tier, so that is the tier whose model
    # reaches the specialist file.
    main(["render", str(EXAMPLE), "-o", str(tmp_path),
          "--opencode-model", "standard=gateway/azure-claude-no-such-model"])
    capsys.readouterr()
    assert _model(tmp_path / "opencode" / "java-spring-specialist.md") == (
        "gateway/azure-claude-no-such-model"
    )
    assert main(["validate", str(tmp_path),
                 "--opencode-allow", "gateway/azure-claude-opus-5"]) == 1
    out = capsys.readouterr().out
    assert "BAD_MODEL" in out and "no-such-model" in out


@pytest.mark.parametrize("bad", ["strong", "=gateway/x", "strong=", ""])
def test_a_malformed_pair_stops_the_command(bad, tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        main(["render", str(EXAMPLE), "-o", str(tmp_path), "--opencode-model", bad])
    assert e.value.code == 2
    # The message must name the shape. Without this the test also passes while
    # the flag does not exist, because argparse rejects an unknown flag with 2.
    assert "TIER=MODEL" in capsys.readouterr().err
    assert not (tmp_path / "opencode").exists(), "a bad flag must write nothing"
