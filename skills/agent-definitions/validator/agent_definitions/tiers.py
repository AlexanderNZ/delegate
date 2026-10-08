"""Load the tier table."""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from importlib import resources
from pathlib import Path

HARNESSES = ("claude-code", "opencode")


@dataclass(frozen=True)
class Tiers:
    tiers: dict[str, dict[str, str]]
    allowed_models: dict[str, list[str]]
    effort_levels: list[str]
    opencode_variants: list[str]
    description_budget_chars: int

    def model_for(self, tier: str, harness: str) -> str:
        if tier not in self.tiers:
            raise ValueError(f"unknown tier {tier!r}; known: {sorted(self.tiers)}")
        return self.tiers[tier][harness]

    def with_opencode_override(
        self, models: Mapping[str, str], allowed: Iterable[str]
    ) -> "Tiers":
        """Return a copy whose OpenCode column carries an override.

        ADR 0003. The tier table is global and it names an Anthropic
        provider. A machine reaches Claude through whatever provider it has,
        and that provider can belong to one job. The override therefore lives
        with the configuration that sets up the provider: that configuration
        passes the override while it is on, and passes none when it is off,
        so the defaults come back. `tiers.toml` stays the single source of
        the defaults and of the Claude Code column, which this method never
        touches.

        `models` maps a tier name to an OpenCode model identifier. `allowed`
        extends `allowed-models.opencode`; it does not replace it.

        The two arguments are separate on purpose. A tier map that extended the
        allowed set by itself would accept whatever it named, so an override
        onto a model that no provider serves would stay green. The caller must
        state the models the provider serves, and the validator then reports
        BAD_MODEL for anything else.

        This returns a new object. The loaded table is not changed, so the next
        caller of `load_tiers` gets the defaults.
        """
        unknown = sorted(set(models) - set(self.tiers))
        if unknown:
            raise ValueError(f"unknown tier(s) {unknown}; known: {sorted(self.tiers)}")
        tiers = {name: dict(columns) for name, columns in self.tiers.items()}
        for name, model in models.items():
            tiers[name]["opencode"] = model
        allowed_models = {harness: list(v) for harness, v in self.allowed_models.items()}
        for model in allowed:
            if model not in allowed_models["opencode"]:
                allowed_models["opencode"].append(model)
        return replace(self, tiers=tiers, allowed_models=allowed_models)


def load_tiers(path: Path | None = None) -> Tiers:
    if path is None:
        text = resources.files(__package__).joinpath("tiers.toml").read_text()
    else:
        text = Path(path).read_text()
    data = tomllib.loads(text)
    return Tiers(
        tiers=data["tiers"],
        allowed_models=data["allowed-models"],
        effort_levels=data["effort"]["levels"],
        opencode_variants=data["effort"]["opencode"]["variants"],
        description_budget_chars=data["budget"]["claude-code-description-chars"],
    )
