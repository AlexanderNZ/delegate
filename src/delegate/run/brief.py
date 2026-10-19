"""Build a verifier's brief from the state of the repository. The `verifier-brief` command in `delegate.cli.brief` drives it.

The shape of a brief selects the verifier's mode. A brief with a
"## Findings under verification" section puts the verifier in fix-up mode. A
brief without one is a full pass. The mode is therefore a property of the
text, and a hand-composed brief can select the wrong mode by accident. This
command removes that choice from the coordinator.

Standard library only. The brief reads the repository through the
version-control port, and it runs no command itself. The package must not grow
a dependency to assemble text.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ..ports.vcs import VcsError, VersionControl
from .reports import (
    SPECIALIST_OPTIONAL, SPECIALIST_REQUIRED, SPECIALIST_STATUSES, VERIFIER_REQUIRED, VERIFIER_VERDICTS,
    specialist_example, verifier_example,
)

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


@contextmanager
def _vcs_errors() -> Iterator[None]:
    """Turn a failure of the version-control port into a BriefError with the same message."""
    try:
        yield
    except VcsError as error:
        raise BriefError(str(error)) from None


def _diff(vcs: VersionControl, repo: str | Path, since: str, until: str, *, from_merge_base: bool) -> str:
    """The text of the changes between two names. Raise BriefError when the port fails."""
    with _vcs_errors():
        return vcs.diff_text(Path(repo), since, until, from_merge_base=from_merge_base)


def _changed_files(vcs: VersionControl, repo: str | Path, since: str, until: str, *, from_merge_base: bool) -> list[str]:
    with _vcs_errors():
        return vcs.changed_paths(Path(repo), since, until, from_merge_base=from_merge_base, follow_renames=True)


def _paths(vcs: VersionControl, repo: str | Path, since: str, until: str, *, from_merge_base: bool) -> list[str]:
    """The changed paths between two names. A renamed path counts as its old name and its new name."""
    with _vcs_errors():
        return vcs.changed_paths(Path(repo), since, until, from_merge_base=from_merge_base)


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


def _field_list(fields: dict[str, str], values: dict[str, Sequence[str]]) -> str:
    """One line for each report field: its name and its JSON type, from the tables of the report module.

    `values` gives the permitted values of a field that has a fixed set.
    """
    lines = []
    for name, kind in fields.items():
        line = f"- `{name}`: {kind}"
        if name in values:
            line += ", one of " + ", ".join(f"`{value}`" for value in values[name])
        lines.append(line)
    return "\n".join(lines)


def _report_shape(path: str | Path, required: str, optional: str | None, notes: str, example: dict[str, object]) -> str:
    """The text of a `## Report` section: the path, the type of each field, the notes, and one example object."""
    parts = [
        f"When you finish, write one JSON object to `{path}`. "
        "Give each field the JSON type that this list names. The engine rejects a report with a field of a different type.",
        "Required fields:\n\n" + required,
    ]
    if optional is not None:
        parts.append("Optional fields:\n\n" + optional)
    parts.append(notes)
    parts.append("Example:\n\n```json\n" + json.dumps(example, indent=2) + "\n```")
    return "\n\n".join(parts)


def _report_rule(place: str, report_path: str | Path) -> str:
    """The rule for the one file that an agent writes outside its `place`: the report, at its absolute path.

    The report path is outside the place where the agent works, and the engine
    keeps its state in the git directory. An agent that makes a relative path to
    the report can make a directory there, and then remove it.
    """
    return (
        f"The report path is outside the {place}. Write the report only at the absolute path `{report_path}`. "
        f"Use this path as it is. Do not make a relative path to it from the {place}. "
        f"Except for the {place} and the report, create, change, or remove nothing inside the git directory of the repository. "
        f"Change no other file outside the {place}."
    )


# How a verifier writes its verdict (#98). The first way is the Write tool: the renderer gives each verifier the Write
# tool, and a guard limits it to an absolute path in a temp directory. The second way is one Bash command, for a
# verifier with no Write tool or a harness that refuses the Write. The verifier guard refuses a heredoc (it reads each
# line of the body as a command), a here-string, `tee` with no input, and each `$(` and backtick of a command, also
# inside quotes. It reads a `<`, `>` or `&` in the text as a redirection or a divider. A JSON escape keeps each of these
# characters out of the command text, and a JSON parser reads it back as the same character. The last way is the final
# message: when no file is written, the engine reads the verdict from the final text.
def _verdict_write_rule(report_path: str | Path) -> str:
    """The text that tells a verifier how to write its verdict at `report_path`: the Write tool first, then the printf form."""
    return (
        f"Write the report with the Write tool, with `file_path` `{report_path}`. "
        "Give the absolute path as it is: the guard of the Write tool permits only an absolute path in a temp directory.\n\n"
        "If you have no Write tool, or the harness refuses the Write, "
        "write the report with one Bash command of this form, with the whole JSON object on one line:\n\n"
        "```bash\nprintf '%s\\n' '<the JSON object>' > <the absolute report path>\n```\n\n"
        "Do not use a heredoc or a here-string: the verifier guard refuses them. "
        "The guard also refuses some characters in the command text, also inside quotes. "
        "So in the JSON text, write each `$` as `\\u0024`, each backtick as `\\u0060`, each apostrophe as `\\u0027`, "
        "each `<` as `\\u003c`, each `>` as `\\u003e`, and each `&` as `\\u0026`. "
        "A JSON parser reads each escape back as the same character, so the report keeps your text.\n\n"
        "In each case, end your final message with the same JSON object, as written in the file, in a `json` code fence. "
        "When the harness refuses each way to write the file, the engine reads the verdict from that object."
    )


FOREGROUND_RULE = (
    "Run every command in the foreground. Do not use background tasks or a monitor tool. "
    "In a headless session, their notifications do not come to you, and a monitor that stays open "
    "keeps the session alive after your report. For a long command, give the command a long timeout."
)


# The text of the `## Gates` section of a specialist brief, before the gate commands. The engine runs the gates
# after the report. A specialist that runs them too runs the full suite twice for each ticket.
GATES_RULE = "Do not run a gate command yourself. Run only the tests of the code that you change."
GATES_AFTER_YOU = "The engine runs these gates after your report, and their result decides the step. " + GATES_RULE
GATES_AT_CHAIN_END = (
    "The engine runs these gates once, on the tip of the chain, after the last ticket, and their result decides the chain. "
    "It does not run them after this ticket. " + GATES_RULE
)


def _specialist_gates(gates: Sequence[str], gates_at_chain_end: bool) -> str:
    """The text of the `## Gates` section of a specialist brief: the rule and each gate command of the stack."""
    return (GATES_AT_CHAIN_END if gates_at_chain_end else GATES_AFTER_YOU) + "\n\n" + _bash("\n".join(gates))


def _sections(*pairs: tuple[str, str]) -> str:
    return "\n\n".join(f"{title}\n\n{body}" for title, body in pairs) + "\n"


# The title of the section that the brief of a later ticket gets after a plan change of its run.
PLAN_CHANGE_HEADING: str = "## Plan change"


def plan_change_text(changes: Sequence[tuple[str, str, str]], location: str, verifier: bool) -> str:
    """The text of the section "Plan change" of the brief of a ticket that comes after an added or changed ticket.

    `changes` holds each added or changed ticket before the ticket of the brief:
    its id, `added` or `changed`, and its text. `location` says where the work
    of those tickets is. A specialist that finds that its ticket no longer fits
    reports `blocked`, and builds nothing. A verifier checks the diff against
    the changed plan too.
    """
    parts = [
        "The coordinator changed the plan of this run after this ticket was written. "
        "These tickets were added or changed, and they come before this ticket in the plan:"
    ]
    parts += [f"### Ticket {ticket} ({kind})\n\n{text.rstrip(chr(10))}" for ticket, kind, text in changes]
    parts.append(location)
    if verifier:
        parts.append(
            "The ticket under verification was written before this change. Check the diff against the changed plan too. "
            "A diff that does not fit the work of these tickets is a finding."
        )
    else:
        parts.append(
            "Check each assumption of this ticket against that work before you change a file. "
            "If this ticket no longer fits the changed plan, build nothing: report the status `blocked`, "
            "and give the reason in `blocked_reason`."
        )
    return "\n\n".join(parts)


def plan_change_section(text: str | None) -> str:
    """The section "Plan change" with `text`, to add at the end of a brief. Empty when `text` is None."""
    return "" if text is None else "\n" + _sections((PLAN_CHANGE_HEADING, text))


def full_brief(
    vcs: VersionControl,
    repo: str | Path,
    branch: str,
    base: str,
    task: str,
    stacks: Sequence[str] = (),
    gate_commands: Sequence[str] | None = None,
    plan_change: str | None = None,
) -> str:
    """The first-pass brief: the task, the three-dot diff, and the gates.

    The diff is the three-dot form. A two-dot diff reports the base's later
    commits as removals, and the verifier reads those as deletions the
    specialist never made.

    The gates come from the delegation doc of the repository. `gate_commands`
    replaces them: the workflow engine knows the gate commands of the stack,
    and a repository need not hold a delegation doc.

    With `plan_change`, the text of `plan_change_text`, the brief has the
    section "Plan change" after the task.
    """
    command = f"git diff {base}...{branch}"
    diff = _diff(vcs, repo, base, branch, from_merge_base=True)
    if not diff.strip():
        raise BriefError(f"{command} is empty. The branch holds no change to verify.")
    if gate_commands is None:
        gates = read_gates(repo, _changed_files(vcs, repo, base, branch, from_merge_base=True), stacks)
    else:
        gates = _bash("\n".join(gate_commands))
    plan = [(PLAN_CHANGE_HEADING, plan_change)] if plan_change is not None else []
    return _sections(
        ("## Task", task),
        *plan,
        ("## Diff", f"`{command}`\n\n" + _fenced_diff(diff)),
        ("## Gates", gates),
    )


@dataclass(frozen=True)
class ChainSegment:
    """One ticket of a chain: its id, its text, and the commits `base..tip` that it added to the chain."""

    ticket: str
    text: str
    base: str
    tip: str


def finding_labels_section() -> str:
    """The section that tells a verifier of a chain to label each finding with a ticket id.

    The engine maps a finding to a ticket by the label `[<ticket id>]` at the
    start of the finding (`map_findings` in `run/reports.py` reads it). The text
    starts with a line break, so it follows another brief as `verifier_run_sections` does.
    """
    return "\n" + _sections((
        "## Finding labels",
        "This branch holds the work of more than one ticket. Start each finding with the id of the ticket it belongs to, "
        "in square brackets, for example `[b] The header row is missing.` "
        "A finding that fits no ticket starts with no label.",
    ))


def green_gates_text(commit: str, outputs: Sequence[tuple[str, str]]) -> str:
    """The text of the `## Gates` section of a verifier brief when the engine ran the gates green on `commit`.

    `outputs` holds each gate command with its output. The text gives the
    commands, the commit and the output, and tells the verifier not to run the
    gates again. The verifier can still run a test to check a claim.
    """
    shown = "\n\n".join(f"$ {command}\n{output.rstrip(chr(10))}" for command, output in outputs)
    return (
        f"The engine ran these gates on the commit `{commit}`, and they are green. "
        "Do not run these gates again. To check a claim, you can run a single test or another command.\n\n"
        + _bash("\n".join(command for command, _ in outputs))
        + "\n\nThe output of the gates:\n\n```\n" + shown + "\n```"
    )


def chain_brief(
    vcs: VersionControl,
    repo: str | Path,
    segments: Sequence[ChainSegment],
    gate_commands: Sequence[str],
    gates_text: str | None = None,
    fixups: tuple[str, str] | None = None,
    earlier_fixups: tuple[str, str] | None = None,
) -> str:
    """The first-pass brief for a chain: the task and the three-dot diff of each ticket, and the gates.

    Each ticket has its own diff `git diff <base>...<tip>`, so the brief holds
    the commits of the tickets in `segments` and no other commit of the chain.
    `full_brief` is the form for one ticket, and this is the same form for
    many, with the labels that map a finding to a ticket.

    `gates_text` replaces the gate commands in the `## Gates` section: the
    engine gives the output of the gates that it ran green (see
    `green_gates_text`). `fixups` is the pair `(start, tip)` of the fix-up
    commits that red gates caused before the first verifier of the stack. The
    brief then holds their delta `git diff <start>..<tip>` too.

    `earlier_fixups` is the pair `(start, tip)` of the fix-up commits of the
    stacks that the engine verified before this stack. When they change a file
    that a ticket in `segments` changed (by its old or its new name), the brief
    holds their delta `git diff <start>..<tip>` and names those files. When
    they change no such file, the brief does not hold them.
    """
    tasks: list[str] = []
    diffs: list[str] = []
    files: set[str] = set()  # each file that a ticket of the stack changed
    for segment in segments:
        command = f"git diff {segment.base}...{segment.tip}"
        diff = _diff(vcs, repo, segment.base, segment.tip, from_merge_base=True)
        if not diff.strip():
            raise BriefError(f"{command} is empty. The ticket {segment.ticket} holds no change to verify.")
        tasks.append(f"Ticket {segment.ticket}\n\n{segment.text.rstrip(chr(10))}")
        diffs.append(f"### Ticket {segment.ticket}\n\n`{command}`\n\n" + _fenced_diff(diff))
        if earlier_fixups is not None:
            files.update(_paths(vcs, repo, segment.base, segment.tip, from_merge_base=True))
    if earlier_fixups is not None:
        start, tip = earlier_fixups
        shared = sorted(files.intersection(_paths(vcs, repo, start, tip, from_merge_base=False)))
        if shared:
            command = f"git diff {start}..{tip}"
            names = ", ".join(f"`{name}`" for name in shared)
            diffs.append(
                "### The fix-ups of the stacks before this stack\n\n"
                f"The fix-up commits of the stacks that the engine verified before this stack change these files of "
                f"the tickets of this stack: {names}. Check these changes against the tickets of this stack.\n\n"
                f"`{command}`\n\n" + _fenced_diff(_diff(vcs, repo, start, tip, from_merge_base=False))
            )
    if fixups is not None:
        start, tip = fixups
        command = f"git diff {start}..{tip}"
        diff = _diff(vcs, repo, start, tip, from_merge_base=False)
        diffs.append(
            f"### The fix-ups of the red gates\n\nThe gates were red on the commit `{start}`. "
            f"These commits fix them.\n\n`{command}`\n\n" + _fenced_diff(diff)
        )
    return _sections(
        ("## Task", "\n\n".join(tasks)),
        ("## Diff", "\n\n".join(diffs)),
        ("## Gates", gates_text if gates_text is not None else _bash("\n".join(gate_commands))),
    ) + finding_labels_section()


def fixup_brief(
    vcs: VersionControl,
    repo: str | Path,
    branch: str,
    rejected: str,
    findings: str,
    authorised: str | None = None,
    stacks: Sequence[str] = (),
    gate_commands: Sequence[str] | None = None,
    gates_text: str | None = None,
) -> str:
    """The scoped brief: the findings verbatim, the delta, and the gates.

    The delta exists only when the specialist added the fix-up on top of the
    rejected commit. An amend or a rebase destroys it, so both states stop the
    command instead of producing a brief that hides the loss.

    The gates come from the delegation doc of the repository. `gate_commands`
    replaces them, as in `full_brief`. `gates_text` replaces the whole text of
    the `## Gates` section, as in `chain_brief`.
    """
    with _vcs_errors():
        vcs.commit_of(Path(repo), rejected)
    try:
        holds = vcs.is_ancestor(Path(repo), rejected, branch)
    except VcsError:
        # A branch that cannot be read is no branch that holds the rejected commit.
        holds = False
    if not holds:
        raise BriefError(
            f"the rejected commit {rejected} is not an ancestor of {branch}. "
            "The specialist amended or rewrote it, so no delta exists. "
            "Ask the specialist to keep the rejected commit and to add the fix-up as a new commit."
        )
    command = f"git diff {rejected}..{branch}"
    delta = _diff(vcs, repo, rejected, branch, from_merge_base=False)
    if not delta.strip():
        raise BriefError(
            f"{command} is empty. The tip of {branch} is the rejected commit, "
            "so there is no fix-up to verify."
        )
    pairs = [(FINDINGS_HEADING, findings.rstrip("\n"))]
    if authorised:
        pairs.append(("## Coordinator-authorised additions", authorised))
    pairs.append(("## Delta", f"`{command}`\n\n" + _fenced_diff(delta)))
    if gates_text is not None:
        gates = gates_text
    elif gate_commands is None:
        gates = read_gates(repo, _changed_files(vcs, repo, rejected, branch, from_merge_base=False), stacks)
    else:
        gates = _bash("\n".join(gate_commands))
    pairs.append(("## Gates", gates))
    return _sections(*pairs)


def _specialist_report_shape(ticket: str, worktree: str | Path, report_path: str | Path) -> str:
    """The text of the `## Report` section of a specialist: the fields, their types, and one example object."""
    return _report_shape(
        report_path,
        _field_list(SPECIALIST_REQUIRED, {"status": SPECIALIST_STATUSES}),
        _field_list(SPECIALIST_OPTIONAL, {}),
        "`gates_green` states whether the tests that you ran pass. "
        "The engine does not trust this field: the result of the gates that the engine runs decides. "
        "A string field holds one string. To give more than one item in a string field, join the items with \"; \".",
        specialist_example(ticket, str(worktree)),
    )


def specialist_brief(
    ticket: str,
    text: str,
    worktree: str | Path,
    hotspots: Sequence[str],
    gates: Sequence[str],
    report_path: str | Path,
    rejected: str | None = None,
    findings: str | None = None,
    red_gates: bool = False,
    gates_at_chain_end: bool = False,
    plan_change: str | None = None,
) -> str:
    """The brief the engine sends a specialist: the ticket, the file boundary, the foreground rule, the gates, the report path.

    The ticket text is behavioural and holds no path. The file boundary is the
    worktree, the report rule, and the hotspot patterns that only the coordinator
    may change.

    With `rejected` and `findings` the brief is a fix-up brief. It adds the
    findings verbatim and the rejected commit, and it tells the specialist to
    add the fix as a new commit on top of the rejected commit. With `red_gates`,
    the findings are the output of the gates that were red on the rejected
    commit, and not the findings of a verifier.

    With `gates_at_chain_end`, the engine runs the gates once, on the tip of an
    `economy` chain, and not after this ticket. The specialist runs the tests of
    what it changes.

    With `plan_change`, the text of `plan_change_text`, the brief has the
    section "Plan change" after the task.
    """
    if hotspots:
        reserved = "These paths are reserved. Only the coordinator changes them. Do not change them:\n\n" + "\n".join(
            f"- `{pattern}`" for pattern in hotspots
        )
    else:
        reserved = "No path is reserved for the coordinator in this stack."
    boundary = (
        f"Work only in the worktree `{worktree}`.\n\n"
        f"{_report_rule('worktree', report_path)}\n\n"
        f"{reserved}\n\n"
        "Never push. Commit your work on the branch of this worktree. Do not amend or rewrite a commit."
    )
    report = _specialist_report_shape(ticket, worktree, report_path)
    pairs = [("## Task", f"Ticket {ticket}\n\n{text.rstrip(chr(10))}")]
    if plan_change is not None:
        pairs.append((PLAN_CHANGE_HEADING, plan_change))
    if rejected is not None and findings is not None:
        pairs.append(("## Findings to fix", findings.rstrip("\n")))
        cause = (
            f"The gates of the engine are red on the commit `{rejected}`. Fix the cause of each red gate above in this worktree."
            if red_gates else f"A verifier rejected the commit `{rejected}`. Fix the findings above in this worktree."
        )
        pairs.append((
            "## Rejected commit",
            f"{cause} Add the fix as a new commit on top of `{rejected}`. "
            "Do not amend, rebase or reset: a verifier reads only the delta from the rejected commit, "
            "and the engine refuses a branch that no longer holds it.",
        ))
    pairs += [
        ("## File boundary", boundary),
        ("## Commands", FOREGROUND_RULE),
        ("## Gates", _specialist_gates(gates, gates_at_chain_end)),
        ("## Report", report),
    ]
    return _sections(*pairs)


def continuation_brief(
    ticket: str,
    text: str,
    worktree: str | Path,
    hotspots: Sequence[str],
    gates: Sequence[str],
    report_path: str | Path,
    commits: Sequence[str],
    reason: str,
    resumed: bool,
    gate_output: str | None = None,
    gates_at_chain_end: bool = False,
    plan_change: str | None = None,
) -> str:
    """The brief the engine sends a specialist that continues its work in the same worktree.

    It holds the ticket id, the reason the last run stopped, the commits on the
    branch so far, and the output of the red gates when a gate was the reason.

    A resumed session keeps the context of its first brief, so a resumed
    brief holds only the continuation and the gates again: the rule not to run
    a gate command must be near the output of red gates. A new agent has no
    context, so its brief is the full specialist brief with the continuation added, and with the section "Plan
    change" when `plan_change` is set.
    """
    lines = [
        f"Ticket {ticket}",
        "",
        f"The last run of the specialist on this ticket stopped before the work was done: {reason}.",
        f"Continue the work in the worktree `{worktree}`. Do not start again. "
        "Do not amend or rewrite a commit. Add each new change as a new commit.",
        "",
    ]
    if commits:
        lines += ["The branch holds these commits so far:", "", *(f"- {commit}" for commit in commits)]
    else:
        lines += ["The branch holds no commit yet."]
    if gate_output:
        lines += ["", "The engine ran the gates after the last run. This is the output of the red gates:", "", "```", gate_output.rstrip("\n"), "```"]
    lines += ["", "When you finish, write the report as the first brief says. " + _report_rule("worktree", report_path)]
    continuation = ("## Continuation", "\n".join(lines))
    if resumed:
        return _sections(continuation, ("## Gates", _specialist_gates(gates, gates_at_chain_end)))
    section = _sections(continuation)
    return specialist_brief(
        ticket, text, worktree, hotspots, gates, report_path, gates_at_chain_end=gates_at_chain_end, plan_change=plan_change
    ) + "\n" + section


def report_correction_brief(
    ticket: str,
    worktree: str | Path,
    report_path: str | Path,
    commits: Sequence[str],
    problems: Sequence[str],
) -> str:
    """The brief the engine sends a specialist that committed work but wrote a report that is missing or not valid.

    It asks only for a corrected report. It holds the ticket id, the commits on
    the branch, each problem of the report check, the report rule, the foreground
    rule, and the shape of the report. It does not give the task again, so a
    resumed session and a new agent get the same brief.
    """
    lines = [
        f"Ticket {ticket}",
        "",
        f"The last run of the specialist on this ticket committed work in the worktree `{worktree}`, "
        f"but the report at `{report_path}` is missing or not valid. The check of the report found these problems:",
        "",
        *(f"- {problem}" for problem in problems),
        "",
        "The branch holds these commits:",
        "",
        *(f"- {commit}" for commit in commits),
        "",
        "Write only a corrected report. Do not change a file in the worktree, and do not add a commit. "
        + _report_rule("worktree", report_path),
    ]
    return _sections(
        ("## Continuation", "\n".join(lines)),
        ("## Commands", FOREGROUND_RULE),
        ("## Report", _specialist_report_shape(ticket, worktree, report_path)),
    )


def red_proof_copy(copy: str | Path) -> Path:
    """The path of the second copy that the verifier brief gives for a red proof: beside `copy`, in the scratch directory.

    The engine removes this path with `copy` when the verifier ends, and it
    does not move it into the evidence directory with the other files of the
    scratch directory.
    """
    return Path(copy).parent / "proof"


def verifier_run_sections(copy: str | Path, report_path: str | Path, mode: str, engine_gates: bool = False) -> str:
    """The sections the engine adds to a verifier brief: the working copy, the report rule, the foreground rule, and the report path.

    `copy` is the temporary copy of the branch that the engine prepared. `mode`
    is `full` or `fix-up`, and the report must name it. With `engine_gates`, the
    brief gives the output of the gates that the engine ran green, and the
    verifier does not run them.
    """
    # The second copy goes beside the copy. The engine removes both when the verifier ends, and keeps neither as evidence.
    proof = red_proof_copy(copy)
    working_copy = (
        f"The temporary copy `{copy}` is a copy of the branch under verification. Read the branch and run the gates in this copy. "
        "The verifier guards deny a write inside a git tree, and a write that holds one. A git tree is a directory "
        "with a `.git` entry, and this copy is one. "
        "To prove a test red, break a second copy. From this copy, make the second copy and remove its `.git` entry "
        f"in one command: `cp -R {copy} {proof} && rm -rf {proof}/.git`. Then go to it with `cd {proof}`, break it, "
        "and run the test there. Do not go to the second copy before you remove its `.git` entry, and do not remove the entry "
        "in a later command. A second copy with a `.git` entry is a git tree, and the guards deny the removal. "
        "Never push.\n\n" + _report_rule("copy", report_path)
    )
    gate_output = (
        "`gate_output` holds the gate output that the brief gives, and the output of each test that you ran, as one string. "
        if engine_gates else "`gate_output` holds the output of the gates that you ran, as one string. "
    )
    report = _report_shape(
        report_path,
        _field_list(VERIFIER_REQUIRED, {"verdict": VERIFIER_VERDICTS}),
        None,
        f"`mode` is `{mode}`. "
        + gate_output
        + "A REJECT lists at least one finding in `findings`. "
        "Write each finding as one string with its file, its line and its text, not as an object. "
        "`unverified` lists what you could not verify.",
        verifier_example(mode),
    )
    report += "\n\n" + _verdict_write_rule(report_path)
    return "\n" + _sections(("## Working copy", working_copy), ("## Commands", FOREGROUND_RULE), ("## Report", report))


def verifier_run_brief(
    vcs: VersionControl,
    copy: str | Path, branch: str, base: str, task: str, gates: Sequence[str], report_path: str | Path,
    plan_change: str | None = None,
) -> str:
    """The brief the engine sends a verifier: the full brief, the working copy, and the report path.

    The full brief comes from `full_brief`, so it holds the task, the diff and
    the gates, and never the specialist's report. `copy` is the temporary copy
    of the branch that the engine prepared. With `plan_change`, the brief has the
    section "Plan change" after the task.
    """
    return full_brief(vcs, copy, branch, base, task, gate_commands=gates, plan_change=plan_change) + verifier_run_sections(
        copy, report_path, "full"
    )
