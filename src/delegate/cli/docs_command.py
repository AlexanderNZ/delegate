"""The `delegate docs` command: parse the arguments, then write the generated sections of the reference pages from the code, or check that they are current.

The generator is in `delegate.docs.reference`. It reads the argument parsers of the other commands in this package,
so this module imports it only when the command runs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="delegate docs", description="Write the generated sections of the reference pages from the code, or check that they are current."
    )
    parser.add_argument("--root", type=Path, default=Path("."), help="the repository root, which holds docs/ and skills/; default is the current directory")
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 and name each section and file that differs from the code")
    return parser


def main(argv: list[str] | None = None) -> int:
    from ..docs import reference  # late: the generator imports this package's parsers, and this module is one of them

    args = build_parser().parse_args(argv)
    try:
        if args.check:
            found = False
            for problem in reference.problems(args.root):
                print(f"delegate docs: {problem}", file=sys.stderr)
                found = True
            return 1 if found else 0
        for line in reference.update(args.root):
            print(line)
    except reference.DocsError as error:
        print(f"delegate docs: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
