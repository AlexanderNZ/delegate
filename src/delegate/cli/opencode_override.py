"""The OpenCode column override of `render`, `validate` and `run` (ADR 0003): the two flags, and the tier table with the override applied.

This module holds no import of the definitions. `delegate run` needs the flags and not the renderer, so the start of `run` loads no PyYAML (#344).
"""

from __future__ import annotations

import argparse

from ..shared.tiers import Tiers


def opencode_model_pair(text: str) -> tuple[str, str]:
    """Parse one --opencode-model value. argparse turns a failure into exit 2."""
    tier, separator, model = text.partition("=")
    if not separator or not tier.strip() or not model.strip():
        raise argparse.ArgumentTypeError(f"expected TIER=MODEL, got {text!r}")
    return tier.strip(), model.strip()


def add_opencode_override_flags(parser: argparse.ArgumentParser) -> None:
    """The OpenCode column override (ADR 0003).

    Both flags are optional, so every caller that does not pass them keeps the
    behaviour it had. The flags are on `render` and `validate` only, and not on
    the top-level parser: `bootstrap` writes a committed render that must carry
    the default identifiers, and a flag it accepted and ignored would be silent.
    """
    parser.add_argument(
        "--opencode-model",
        action="append",
        metavar="TIER=MODEL",
        type=opencode_model_pair,
        help="use MODEL for TIER in the OpenCode column; repeat it",
    )
    parser.add_argument(
        "--opencode-allow",
        action="append",
        metavar="MODEL",
        help="add MODEL to the OpenCode allowed set; repeat it. It extends the set, never replaces it",
    )


def apply_opencode_override(tiers: Tiers, args: argparse.Namespace) -> Tiers:
    """The tier table with the OpenCode override of the arguments applied. Raise ValueError for an unknown tier."""
    models = dict(getattr(args, "opencode_model", None) or [])
    allowed = list(getattr(args, "opencode_allow", None) or [])
    if models or allowed:
        tiers = tiers.with_opencode_override(models, allowed)
    return tiers
