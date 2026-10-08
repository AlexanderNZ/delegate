"""verifier-brief: build a verifier's brief from git state.

The shape of a brief selects the verifier's mode. A brief with a
"## Findings under verification" section puts the verifier in fix-up mode. A
brief without one is a full pass. The mode is therefore a property of the
text, and a hand-composed brief can select the wrong mode by accident. This
command removes that choice from the coordinator.

Standard library only, and git through subprocess. The package must not grow
a dependency to assemble text.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .reports import SPECIALIST_OPTIONAL, SPECIALIST_REQUIRED, SPECIALIST_STATUSES, VERIFIER_REQUIRED, VERIFIER_VERDICTS

DELEGATION_DOC = Path("docs") / "agents" / "delegation.md"
FINDINGS_HEADING = "## Findings under verification"
GATES_HEADING = re.compile(r"^#+[ \t]+.*verification gates[ \t]*$", re.IGNORECASE | re.MULTILINE)
FENCED_BLOCK = re.compile(r"^```[^\n]*\n(.*?)^```", re.DOTALL | re.MULTILINE)
GATES_PLACEHOLDER = (
    "This repository declares no verification gates in `docs/agents/delegation.md`. "
    "Ask the coordinator for the gate commands before you report."
)
NO_CLAIM_PLACEHOLDER = (
    "No gate block in `docs/agents/delegation.md` claims a changed file. "
    "Ask the coordinator for the gate commands before you report."
)
HEADING_LINE = re.compile(r"^(#+)[ \t]+(.*?)[ \t]*$")
BACKTICKED = re.compile(r"`([^`]+)`")


class BriefError(Exception):
    """A git state or a gate selection that makes a correct brief impossible."""


@dataclass(frozen=True)
class GateBlock:
    """One stack's gates: a sub-heading under "Verification gates" and its first fenced block."""

    heading: str
    paths: tuple[str, ...]
    commands: str

    @property
    def name(self) -> str:
        return _plain(self.heading)

    def claims(self, changed: str) -> bool:
        return any(_holds(path, changed) for path in self.paths)

    def render(self) -> str:
        return f"### {self.heading}\n\n" + _bash(self.commands)


def _plain(heading: str) -> str:
    """The heading text without backticks, which is the name --stack compares."""
    return heading.replace("`", "").strip()


def _holds(path: str, changed: str) -> bool:
    """True when the claimed path is the changed file or a directory above it."""
    path = path.strip().removeprefix("./").rstrip("/")
    if path in ("", "."):
        return True
    return changed == path or changed.startswith(path + "/")


def _bash(commands: str) -> str:
    return "```bash\n" + commands.strip("\n") + "\n```"


def _git(repo: str | Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise BriefError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.returncode}")
    return r.stdout


def _changed_files(repo: str | Path, diff_range: str) -> list[str]:
    return _git(repo, "diff", "--name-only", diff_range).splitlines()


def gate_blocks(text: str, gates_start: int) -> list[GateBlock]:
    """The stack blocks in the "Verification gates" section that opens at gates_start.

    A stack block is a heading deeper than the gates heading, inside its
    section, with a fenced block before the next heading. Each backticked word
    in the heading text is a path the block claims. A line inside a fence is
    never a heading, so a bash comment cannot end the section.
    """
    lines = text[gates_start:].splitlines(keepends=True)
    gates_level = len(HEADING_LINE.match(lines[0]).group(1))
    blocks: list[GateBlock] = []
    heading: str | None = None
    body: list[str] = []
    fence: list[str] | None = None
    for line in lines[1:]:
        if fence is not None:
            if line.startswith("```"):
                if heading is not None and not body:
                    body = fence
                fence = None
            else:
                fence.append(line)
            continue
        if line.startswith("```"):
            fence = []
            continue
        m = HEADING_LINE.match(line)
        if m is None:
            continue
        if heading is not None and body:
            blocks.append(GateBlock(heading, tuple(BACKTICKED.findall(heading)), "".join(body)))
        if len(m.group(1)) <= gates_level:
            return blocks
        heading, body = m.group(2), []
    if heading is not None and body:
        blocks.append(GateBlock(heading, tuple(BACKTICKED.findall(heading)), "".join(body)))
    return blocks


def _select(blocks: list[GateBlock], changed: list[str], stacks: list[str]) -> str:
    """The gates for one brief.

    A --stack name selects its block and overrides the path match. Without a
    name, the brief carries each block that claims a changed file, in the
    order of the doc. A change that no block claims gets a placeholder and
    never the first block, because the first block belongs to another stack.
    """
    if stacks:
        by_name = {b.name: b for b in blocks}
        unknown = [s for s in stacks if _plain(s) not in by_name]
        if unknown:
            known = ", ".join(f'"{b.name}"' for b in blocks) or "none"
            raise BriefError(
                f'no gate block in {DELEGATION_DOC} has the heading "{unknown[0]}". '
                f"Known headings: {known}."
            )
        chosen = [by_name[_plain(s)] for s in stacks]
    else:
        chosen = [b for b in blocks if any(b.claims(f) for f in changed)]
    if not chosen:
        return NO_CLAIM_PLACEHOLDER
    return "\n\n".join(b.render() for b in chosen)


def read_gates(repo: str | Path, changed: Sequence[str] = (), stacks: Sequence[str] = ()) -> str:
    """The gate commands from the repository's delegation doc, or a placeholder.

    The doc is read from the working tree, because that is the state the
    verifier runs the gates against. A doc whose gates section has no
    sub-heading that claims a path gives the first fenced block under the
    heading, as before stack selection existed.
    """
    doc = Path(repo) / DELEGATION_DOC
    text = doc.read_text() if doc.is_file() else ""
    heading = GATES_HEADING.search(text)
    blocks = gate_blocks(text, heading.start()) if heading else []
    if stacks or any(b.paths for b in blocks):
        return _select(blocks, list(changed), list(stacks))
    if heading is None:
        return GATES_PLACEHOLDER
    block = FENCED_BLOCK.search(text, heading.end())
    if block is None:
        return GATES_PLACEHOLDER
    return _bash(block.group(1))


def read_task(task: str) -> str:
    """The task text. A value that starts with "@" names a file to read."""
    if task.startswith("@"):
        return Path(task[1:]).read_text().rstrip("\n")
    return task


def _fenced_diff(diff: str) -> str:
    return "```diff\n" + diff.rstrip("\n") + "\n```"


def _sections(*pairs: tuple[str, str]) -> str:
    return "\n\n".join(f"{title}\n\n{body}" for title, body in pairs) + "\n"


def full_brief(
    repo: str | Path,
    branch: str,
    base: str,
    task: str,
    stacks: Sequence[str] = (),
    gate_commands: Sequence[str] | None = None,
) -> str:
    """The first-pass brief: the task, the three-dot diff, and the gates.

    The diff is the three-dot form. A two-dot diff reports the base's later
    commits as removals, and the verifier reads those as deletions the
    specialist never made.

    The gates come from the delegation doc of the repository. `gate_commands`
    replaces them: the workflow engine knows the gate commands of the stack,
    and a repository need not hold a delegation doc.
    """
    command = f"git diff {base}...{branch}"
    diff = _git(repo, "diff", f"{base}...{branch}")
    if not diff.strip():
        raise BriefError(f"{command} is empty. The branch holds no change to verify.")
    if gate_commands is None:
        gates = read_gates(repo, _changed_files(repo, f"{base}...{branch}"), stacks)
    else:
        gates = _bash("\n".join(gate_commands))
    return _sections(
        ("## Task", task),
        ("## Diff", f"`{command}`\n\n" + _fenced_diff(diff)),
        ("## Gates", gates),
    )


def fixup_brief(
    repo: str | Path,
    branch: str,
    rejected: str,
    findings: str,
    authorised: str | None = None,
    stacks: Sequence[str] = (),
) -> str:
    """The scoped brief: the findings verbatim, the delta, and the gates.

    The delta exists only when the specialist added the fix-up on top of the
    rejected commit. An amend or a rebase destroys it, so both states stop the
    command instead of producing a brief that hides the loss.
    """
    _git(repo, "rev-parse", "--verify", f"{rejected}^{{commit}}")
    ancestor = subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", rejected, branch],
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        raise BriefError(
            f"the rejected commit {rejected} is not an ancestor of {branch}. "
            "The specialist amended or rewrote it, so no delta exists. "
            "Ask the specialist to keep the rejected commit and to add the fix-up as a new commit."
        )
    command = f"git diff {rejected}..{branch}"
    delta = _git(repo, "diff", f"{rejected}..{branch}")
    if not delta.strip():
        raise BriefError(
            f"{command} is empty. The tip of {branch} is the rejected commit, "
            "so there is no fix-up to verify."
        )
    pairs = [(FINDINGS_HEADING, findings.rstrip("\n"))]
    if authorised:
        pairs.append(("## Coordinator-authorised additions", authorised))
    pairs.append(("## Delta", f"`{command}`\n\n" + _fenced_diff(delta)))
    pairs.append(("## Gates", read_gates(repo, _changed_files(repo, f"{rejected}..{branch}"), stacks)))
    return _sections(*pairs)


def specialist_brief(
    ticket: str, text: str, worktree: str | Path, hotspots: Sequence[str], gates: Sequence[str], report_path: str | Path
) -> str:
    """The brief the engine sends a specialist: the ticket, the file boundary, the gates, the report path.

    The ticket text is behavioural and holds no path. The file boundary is the
    worktree, and the hotspot patterns that only the coordinator may change.
    """
    if hotspots:
        reserved = "These paths are reserved. Only the coordinator changes them. Do not change them:\n\n" + "\n".join(
            f"- `{pattern}`" for pattern in hotspots
        )
    else:
        reserved = "No path is reserved for the coordinator in this stack."
    boundary = (
        f"Work only in the worktree `{worktree}`. Change no file outside it.\n\n"
        f"{reserved}\n\n"
        "Never push. Commit your work on the branch of this worktree. Do not amend or rewrite a commit."
    )
    fields = ", ".join(f"`{name}`" for name in SPECIALIST_REQUIRED)
    report = (
        f"When you finish, write a JSON object to `{report_path}` with these fields: {fields}. "
        "`status` is one of " + ", ".join(f"`{s}`" for s in SPECIALIST_STATUSES) + ". "
        "`gates_green` states whether the gates passed in your run. "
        "Optional fields: " + ", ".join(f"`{name}`" for name in SPECIALIST_OPTIONAL) + "."
    )
    return _sections(
        ("## Task", f"Ticket {ticket}\n\n{text.rstrip(chr(10))}"),
        ("## File boundary", boundary),
        ("## Gates", "The engine runs these gates itself after you finish.\n\n" + _bash("\n".join(gates))),
        ("## Report", report),
    )


def verifier_run_brief(
    copy: str | Path, branch: str, base: str, task: str, gates: Sequence[str], report_path: str | Path
) -> str:
    """The brief the engine sends a verifier: the full brief, the working copy, and the report path.

    The full brief comes from `full_brief`, so it holds the task, the diff and
    the gates, and never the specialist's report. `copy` is the temporary copy
    of the branch that the engine prepared.
    """
    working_copy = (
        f"Work only in the temporary copy `{copy}`. It is a copy of the branch under verification. "
        "You may break the copy to prove a test red. Change no file outside it. Never push."
    )
    fields = ", ".join(f"`{name}`" for name in VERIFIER_REQUIRED)
    report = (
        f"When you finish, write a JSON object to `{report_path}` with these fields: {fields}. "
        "`mode` is `full`. `verdict` is one of " + ", ".join(f"`{v}`" for v in VERIFIER_VERDICTS) + ". "
        "Each entry of `criteria` is an object with the strings `criterion` and `evidence`. "
        "`gate_output` holds the output of the gates that you ran. "
        "A REJECT lists at least one finding in `findings`. "
        "`unverified` lists what you could not verify."
    )
    brief = full_brief(copy, branch, base, task, gate_commands=gates)
    return brief + "\n" + _sections(("## Working copy", working_copy), ("## Report", report))


def main(argv: list[str] | None = None) -> int:
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

    args = p.parse_args(argv)
    try:
        if args.mode == "full":
            out = full_brief(args.repo, args.branch, args.base, read_task(args.task), args.stack)
        else:
            out = fixup_brief(
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
