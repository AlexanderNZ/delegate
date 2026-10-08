"""delegate: one command over the renderer, the validator, bootstrap and the brief generator.

This module only dispatches. `render`, `validate` and `bootstrap` go to
`agent_definitions.cli.main`, and `brief` goes to `agent_definitions.brief.main`.
Each one receives the argument list it would receive from its standalone
command (`agent-definitions` or `verifier-brief`), so the exit code, the output
and the files written match by construction, and the two standalone parsers
stay the single source of every option. Only the top-level help and the two
usage errors of the umbrella itself are written here.
"""

from __future__ import annotations

import argparse
import sys

from . import brief, cli, run

# Subcommands that the `agent-definitions` command owns, with the line that
# `delegate --help` shows for each.
AGENT_DEFINITIONS_COMMANDS: dict[str, str] = {
    "render": "render a declaration into <out>/claude-code and <out>/opencode",
    "validate": "validate rendered agent directories",
    "bootstrap": "write a repository's context skill, its declaration, and its rendered pair",
}

# The subcommand that the `verifier-brief` command owns.
BRIEF_COMMAND: str = "brief"
BRIEF_HELP: str = "print a verifier's brief: `brief full` or `brief fixup`"

# The subcommand that `agent_definitions.run` owns.
RUN_COMMAND: str = "run"
RUN_HELP: str = "check a workflow file and print its plan: `run <workflow> --dry-run`"

TIERS_OPTION: str = "--tiers"


def _help_parser() -> argparse.ArgumentParser:
    """The parser that only prints the help of the umbrella. It parses nothing else."""
    parser = argparse.ArgumentParser(
        prog="delegate",
        description="A delegation kit. Each subcommand takes the arguments of its standalone command.",
    )
    parser.add_argument(
        TIERS_OPTION,
        help="path to a tiers.toml; default is the bundled table. Valid before render, validate and bootstrap",
    )
    sub = parser.add_subparsers(dest="command", metavar="command")
    for name, text in AGENT_DEFINITIONS_COMMANDS.items():
        sub.add_parser(name, help=f"{text} (as agent-definitions {name})", add_help=False)
    sub.add_parser(BRIEF_COMMAND, help=f"{BRIEF_HELP} (as verifier-brief)", add_help=False)
    sub.add_parser(RUN_COMMAND, help=f"{RUN_HELP} (as delegate run)", add_help=False)
    return parser


def _subcommand_index(args: list[str]) -> int:
    """The index of the first argument after the options that sit before the subcommand."""
    i = 0
    while i < len(args):
        if args[i] == TIERS_OPTION:
            i += 2
        elif args[i].startswith(f"{TIERS_OPTION}="):
            i += 1
        else:
            break
    return i


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    parser = _help_parser()
    at = _subcommand_index(args)
    if at >= len(args):
        parser.print_help(sys.stderr)
        return 2
    command = args[at]
    if command.startswith("-"):
        parser.parse_args(args)  # -h prints the help and exits 0; any other option is a usage error
        return 2
    if command in AGENT_DEFINITIONS_COMMANDS:
        return cli.main(args)
    if command == BRIEF_COMMAND:
        if at:
            print(
                f"delegate: {TIERS_OPTION} is an option of render, validate and bootstrap, not of {BRIEF_COMMAND}",
                file=sys.stderr,
            )
            return 2
        return brief.main(args[at + 1 :])
    if command == RUN_COMMAND:
        if at:
            print(
                f"delegate: {TIERS_OPTION} is an option of render, validate and bootstrap, not of {RUN_COMMAND}",
                file=sys.stderr,
            )
            return 2
        return run.main(args[at + 1 :])
    known = ", ".join([*AGENT_DEFINITIONS_COMMANDS, BRIEF_COMMAND, RUN_COMMAND])
    print(f"delegate: unknown subcommand {command!r}; expected one of: {known}", file=sys.stderr)
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
