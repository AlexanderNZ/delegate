"""The `verifier-brief` command: parse the arguments, build the git backend, call the brief generator of the run context and print the brief.

The use case lives in `delegate.run.brief`, which never imports an adapter. This module is the composition point
of the command: it makes the git backend of the version-control port and hands it to the use case.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..adapters.git import GitVersionControl
from ..run.brief import BriefError, fixup_brief, full_brief, read_task


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="verifier-brief",
        description="Print a verifier's brief. The mode of the brief sets the mode of the verifier.",
    )
    sub = p.add_subparsers(dest="mode", required=True)

    f = sub.add_parser("full", help="a first-pass brief: task, three-dot diff, gates")
    f.add_argument("--repo", required=True, help="path to the repository or worktree")
    f.add_argument("--branch", required=True, help="the branch under verification")
    f.add_argument("--base", default="main", help="the base of the three-dot diff; default main")
    f.add_argument("--task", required=True, help="the task text, or @PATH to read it from a file")

    x = sub.add_parser("fixup", help="a scoped brief: findings, delta, gates")
    x.add_argument("--repo", required=True, help="path to the repository or worktree")
    x.add_argument("--branch", required=True, help="the branch that holds the fix-up commit")
    x.add_argument("--rejected", required=True, help="the commit the first verifier rejected")
    x.add_argument("--findings", required=True, help="path to the first verifier's findings")
    x.add_argument("--authorised", help="additions the coordinator authorised beyond the findings")

    for mode in (f, x):
        mode.add_argument(
            "--stack",
            action="append",
            default=[],
            metavar="HEADING",
            help="select the gate block under this sub-heading of \"Verification gates\"; "
            "it overrides the path match; give it again for a second block",
        )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    vcs = GitVersionControl()
    try:
        if args.mode == "full":
            out = full_brief(vcs, args.repo, args.branch, args.base, read_task(args.task), args.stack)
        else:
            out = fixup_brief(
                vcs,
                args.repo,
                args.branch,
                args.rejected,
                Path(args.findings).read_text(),
                args.authorised,
                args.stack,
            )
    except (BriefError, OSError) as e:
        print(f"verifier-brief: {e}", file=sys.stderr)
        return 1
    print(out, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
