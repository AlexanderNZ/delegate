"""delegate: one command over the renderer, the validator, bootstrap and the brief generator.

This module only dispatches. One table, `SUBCOMMANDS`, names each subcommand, its module and its line of help. `render`,
`schema`, `validate` and `bootstrap` go to `delegate.cli.agent_definitions.main`, and `brief` goes to `delegate.cli.brief.main`.
Each one receives the argument list it would receive from its standalone
command (`agent-definitions` or `verifier-brief`), so the exit code, the output
and the files written match by construction, and the two standalone parsers
stay the single source of every option. Only the top-level help, the two
usage errors of the umbrella itself, and the title of the terminal (`delegate.cli.title`) are written here.

The command imports the module of a subcommand only when it runs that subcommand. The start of `status` so loads no definitions, no
PyYAML and no `importlib.metadata`, and the same holds for `watch`, `runs` and `run` (#344).

Ctrl-C ends each subcommand with the exit status 130 (`EXIT_INTERRUPTED`) and no traceback. `watch` and `run` print
their own line: the position to go on from, and the run to resume. Each other subcommand prints `delegate: interrupted`.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from dataclasses import dataclass

from ..run.watch import EXIT_INTERRUPTED
from ..shared.version import VersionAction
from .epilog import COMMANDS_PAGE, RUN_PAGE, WORKFLOW_PAGE, HelpFormatter, epilog
from .title import Title

TIERS_OPTION: str = "--tiers"


@dataclass(frozen=True)
class Subcommand:
    """One subcommand of `delegate`: its name, the module that runs it, and its line in `delegate --help`.

    `standalone` is the command that the subcommand is also known as, and the help line ends with `(as <standalone>)`.
    `receives_tiers` is true for a subcommand that takes the option `--tiers` before its name: it then gets the whole argument
    list, as its standalone command would. `sets_title` is false for `watch`, which sets its own title when it found the run."""

    name: str
    module: str
    help: str
    standalone: str
    receives_tiers: bool = False
    sets_title: bool = True

    def main(self, args: list[str]) -> int:
        """Import the module of the subcommand now, and run it with `args`."""
        return importlib.import_module(f".{self.module}", __package__).main(args)


def _agent_definitions(name: str, help: str) -> Subcommand:
    return Subcommand(name, "agent_definitions", help, f"agent-definitions {name}", receives_tiers=True)


# One table of the subcommands, in the order of `delegate --help`.
SUBCOMMANDS: tuple[Subcommand, ...] = (
    _agent_definitions("render", "render a declaration into <out>/claude-code and <out>/opencode"),
    _agent_definitions("schema", "print the keys of a declaration as JSON"),
    _agent_definitions("validate", "validate rendered agent directories"),
    _agent_definitions("bootstrap", "write a repository's context skill, its declaration, and its rendered pair"),
    Subcommand("brief", "brief", "print a verifier's brief: `brief full` or `brief fixup`", "verifier-brief"),
    Subcommand(
        "run",
        "run_command",
        "build a workflow's tickets, check it with `run <workflow> --dry-run`, or go on with `run --resume <run-id>`",
        "delegate run",
    ),
    Subcommand(
        "pause", "pause", "ask a run to stop at its next step boundary; go on later with `run --resume <run-id>`", "delegate pause"
    ),
    Subcommand("stop", "stop", "stop a run now with SIGTERM; go on later with `run --resume <run-id>`", "delegate stop"),
    Subcommand("status", "status", "print the state of each ticket of a run, from its journal", "delegate status"),
    Subcommand("runs", "runs", "list the runs of this machine in one table, across each repository", "delegate runs"),
    Subcommand(
        "watch",
        "watch",
        "follow the journal of a run, print each event, and exit with a code for the reason",
        "delegate watch",
        sets_title=False,
    ),
    Subcommand(
        "clean",
        "clean",
        "remove the worktrees and the merged branches that ended runs left; check it with `clean --dry-run`",
        "delegate clean",
    ),
    Subcommand(
        "docs",
        "docs_command",
        "write the generated sections of the reference pages from the code, or check that they are current",
        "delegate docs",
    ),
)
BY_NAME: dict[str, Subcommand] = {subcommand.name: subcommand for subcommand in SUBCOMMANDS}


def _help_parser() -> argparse.ArgumentParser:
    """The parser that only prints the help of the umbrella. It parses nothing else."""
    parser = argparse.ArgumentParser(
        prog="delegate",
        description="A delegation kit. Each subcommand takes the arguments of its standalone command.",
        epilog=epilog(COMMANDS_PAGE, RUN_PAGE, WORKFLOW_PAGE),
        formatter_class=HelpFormatter,
    )
    parser.add_argument(
        TIERS_OPTION,
        help="path to a tiers.toml; default is the bundled table. Valid before render, validate and bootstrap",
    )
    parser.add_argument("--version", action=VersionAction, help="print the version of the package and exit")
    sub = parser.add_subparsers(dest="command", metavar="command")
    for subcommand in SUBCOMMANDS:
        sub.add_parser(subcommand.name, help=f"{subcommand.help} (as {subcommand.standalone})", add_help=False)
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


def _set_title(command: str) -> None:
    """Set the title of the terminal to `delegate <command>`. `watch` sets its own title when it found the run."""
    Title(sys.stdout).set(f"delegate {command}")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        # Ctrl-C (SIGINT) before `watch` or `run` can handle it, or in any other subcommand: one line, no traceback.
        print("delegate: interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED


def _dispatch(args: list[str]) -> int:
    """Give the arguments to the subcommand that they name, and return its exit code."""
    parser = _help_parser()
    at = _subcommand_index(args)
    if at >= len(args):
        parser.print_help(sys.stderr)
        return 2
    command = args[at]
    if command.startswith("-"):
        parser.parse_args(args)  # -h prints the help and exits 0; any other option is a usage error
        return 2
    subcommand = BY_NAME.get(command)
    if subcommand is None:
        known = ", ".join(BY_NAME)
        print(f"delegate: unknown subcommand {command!r}; expected one of: {known}", file=sys.stderr)
        return 2
    if at and not subcommand.receives_tiers:
        print(
            f"delegate: {TIERS_OPTION} is an option of render, validate and bootstrap, not of {command}",
            file=sys.stderr,
        )
        return 2
    if subcommand.sets_title:
        _set_title(command)
    return subcommand.main(args if subcommand.receives_tiers else args[at + 1 :])


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
