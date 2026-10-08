"""delegate: one command over the renderer, the validator, bootstrap and the brief generator.

This module only dispatches. `render`, `validate` and `bootstrap` go to
`agent_definitions.cli.main`. Each one receives the argument list it would
receive from the standalone `agent-definitions` command, so the exit code, the
output and the files written match by construction. The two standalone parsers
stay the single source of every option.
"""

from __future__ import annotations

import sys

from . import cli

# Subcommands that the `agent-definitions` command owns.
AGENT_DEFINITIONS_COMMANDS: tuple[str, ...] = ("render", "validate", "bootstrap")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return cli.main(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
