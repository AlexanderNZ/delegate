"""agent-definitions: render a declaration, validate rendered agent directories, or bootstrap a repository."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .bootstrap import DEFAULT_SKILLS_ROOT, run_from_args
from .declaration import load_declaration
from .render import render
from .tiers import Tiers, load_tiers
from .validate import validate_set


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


def tiers_from_args(args: argparse.Namespace) -> Tiers:
    """The tier table with any override applied."""
    return apply_opencode_override(load_tiers(args.tiers), args)


def cmd_render(args: argparse.Namespace) -> int:
    tiers = tiers_from_args(args)
    agents = load_declaration(Path(args.declaration))
    out = Path(args.out)
    written = []
    for harness, files in render(agents, tiers).items():
        d = out / harness
        d.mkdir(parents=True, exist_ok=True)
        for fname, content in files.items():
            (d / fname).write_text(content)
            written.append(d / fname)
    for p in written:
        print(p)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    tiers = tiers_from_args(args)
    cc = Path(args.claude_code) if args.claude_code else None
    oc = Path(args.opencode) if args.opencode else None
    if args.rendered:
        root = Path(args.rendered)
        cc = cc or (root / "claude-code" if (root / "claude-code").is_dir() else None)
        oc = oc or (root / "opencode" if (root / "opencode").is_dir() else None)
    if cc is None and oc is None:
        print("nothing to validate: give a rendered root, --claude-code, or --opencode", file=sys.stderr)
        return 2
    user_sd = Path(args.user_skills_dir) if args.user_skills_dir else None
    findings = validate_set(tiers, cc, oc, Path(args.skills_dir) if args.skills_dir else None, user_skills_dir=user_sd)
    for f in findings:
        print(f)
    if findings:
        print(f"{len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("ok")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agent-definitions")
    p.add_argument("--tiers", help="path to a tiers.toml; default is the bundled table")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render a declaration into <out>/claude-code and <out>/opencode")
    r.add_argument("declaration", help="the declaration file (TOML) to render")
    r.add_argument("-o", "--out", required=True, help="the output directory; it receives the subdirectories claude-code and opencode")
    add_opencode_override_flags(r)
    r.set_defaults(fn=cmd_render)
    v = sub.add_parser("validate", help="validate rendered agent directories")
    v.add_argument("rendered", nargs="?", help="a root with claude-code/ and opencode/ subdirectories")
    v.add_argument("--claude-code", help="a directory of rendered Claude Code agents to validate")
    v.add_argument("--opencode", help="a directory of rendered OpenCode agents to validate")
    v.add_argument("--skills-dir", help="check that every preloaded skill exists here")
    v.add_argument(
        "--user-skills-dir",
        help="a second skills directory to search; default: ~/.claude/skills",
    )
    add_opencode_override_flags(v)
    v.set_defaults(fn=cmd_validate)
    b = sub.add_parser(
        "bootstrap",
        help="write a repository's context skill, its declaration, and its rendered pair",
    )
    b.add_argument("--repo", required=True, help="the repository root")
    b.add_argument("--name", required=True, help="the agent name; the skill is <name>-context")
    b.add_argument("--domain", required=True, help="the domain in one sentence; it is also the skill description")
    b.add_argument("--tier", required=True, help="the tier of the agent: strong, standard or cheap")
    b.add_argument(
        "--skill",
        action="append",
        required=True,
        metavar="NAME",
        help="a preloaded skill; repeat it. <name>-context is added first",
    )
    b.add_argument(
        "--reference",
        action="append",
        required=True,
        metavar="PATH",
        help="a read-first document; repeat it. A note may follow the backticked path",
    )
    b.add_argument("--prompt-file", metavar="FILE", help="the domain rules; the file body becomes the prompt")
    b.add_argument("--max-turns", type=int, metavar="N", help="the turn cap for both halves")
    b.add_argument(
        "--gate-command",
        action="append",
        metavar="CMD",
        help="a gate the verifier may run, alone or with arguments; repeat it",
    )
    b.add_argument(
        "--get-only-command",
        action="append",
        metavar="CMD",
        help="a command the verifier may run only without -X, alone or with arguments; repeat it",
    )
    b.add_argument(
        "--output-language",
        metavar="LANGUAGE",
        help="the language of agent text: none | ste (ASD-STE100); default: write no field",
    )
    b.add_argument(
        "--tracked-file-build",
        action="store_true",
        help="the build reads tracked files only, as a flake does; the specialist then stages every new file",
    )
    b.add_argument("--skills-root", default=DEFAULT_SKILLS_ROOT, help=f"default {DEFAULT_SKILLS_ROOT}")
    b.add_argument(
        "--user-skills-dir",
        help="a second skills directory to search; default: ~/.claude/skills",
    )
    b.add_argument("--dry-run", action="store_true", help="print every file with its content and write nothing")
    b.set_defaults(fn=run_from_args)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
