"""The agent report: a JSON file that an agent writes to the path its brief names.

`read_specialist_report` reads a specialist's report and checks it against the
schema below. A missing file, text that is not JSON, a missing field, a field of
the wrong type, and a value outside the permitted set each raise ReportError.
The message names the field. A field that the schema does not list is ignored.

`read_verifier_report` does the same for a verifier's verdict report.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .domain import ACCEPT, COMMITTED, VERDICTS

# The values of the `status` field of a specialist report.
SPECIALIST_STATUSES: tuple[str, ...] = ("committed", "blocked", "partial")

# The required fields of a specialist report, with the type of each.
SPECIALIST_REQUIRED: dict[str, str] = {
    "ticket": "string",
    "status": "string",
    "branch": "string",
    "head_sha": "string",
    "commits": "list of strings",
    "gates_green": "boolean",
    "summary": "string",
}

# The optional fields of a specialist report, with the type of each.
SPECIALIST_OPTIONAL: dict[str, str] = {
    "worktree": "string",
    "blocked_reason": "string",
    "judgement_calls": "string",
}


# The values of the `verdict` field of a verifier report.
VERIFIER_VERDICTS: tuple[str, ...] = VERDICTS

# The required fields of a verifier report, with the type of each.
VERIFIER_REQUIRED: dict[str, str] = {
    "mode": "string",
    "verdict": "string",
    "criteria": "list of objects with the strings criterion and evidence",
    "gate_output": "string",
    "findings": "list of strings",
    "unverified": "list of strings",
}


def specialist_example(ticket: str, worktree: str) -> dict[str, object]:
    """One specialist report for `ticket` that passes `read_specialist_report`.

    The brief shows it to the specialist as the exact shape of the report. It
    holds each field of the tables above, with a value of the type that the
    table names. A text in angle brackets tells the agent what to put there.
    """
    return {
        "ticket": ticket,
        "status": COMMITTED,
        "branch": "<the branch of the worktree>",
        "head_sha": "<the full SHA of the tip of the branch>",
        "commits": ["<the full SHA of the first commit that you added>", "<the full SHA of the next commit>"],
        "gates_green": True,
        "summary": "<what you changed, in one or two sentences>",
        "worktree": worktree,
        "judgement_calls": "<the first decision that you made>; <the next decision>",
    }


def verifier_example(mode: str) -> dict[str, object]:
    """One verdict report for `mode` that passes `read_verifier_report`.

    The brief shows it to the verifier as the exact shape of the report, as
    `specialist_example` does for the specialist.
    """
    return {
        "mode": mode,
        "verdict": ACCEPT,
        "criteria": [
            {"criterion": "<the text of one acceptance criterion>", "evidence": "<the file, the line, or the output that proves it>"}
        ],
        "gate_output": "<the output of the first gate>\n<the output of the next gate>",
        "findings": [],
        "unverified": ["<one thing that you could not verify>"],
    }


class ReportError(Exception):
    """A report is missing or invalid. The message names the path or the field.

    `problems` holds each problem of the check as one text, so a brief can name
    each one. A report that is missing or is no JSON object has one problem: the
    message.
    """

    def __init__(self, message: str, problems: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.problems: tuple[str, ...] = tuple(problems) or (message,)


@dataclass(frozen=True)
class SpecialistReport:
    ticket: str
    status: str
    branch: str
    head_sha: str
    commits: list[str]
    gates_green: bool
    summary: str
    blocked_reason: str | None
    judgement_calls: str | None


@dataclass(frozen=True)
class VerifierReport:
    mode: str
    verdict: str
    criteria: list[dict[str, str]]
    gate_output: str
    findings: list[str]
    unverified: list[str]


def _has_type(value: object, kind: str) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind.startswith("list of objects"):
        return isinstance(value, list) and all(
            isinstance(item, dict)
            and isinstance(item.get("criterion"), str)
            and isinstance(item.get("evidence"), str)
            for item in value
        )
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _read_json_object(path: Path, who: str) -> dict[str, object]:
    """The JSON object in the file at `path`. Raise ReportError when there is none."""
    try:
        text = path.read_text()
    except FileNotFoundError:
        raise ReportError(f"report missing: the {who} wrote no file at {path}") from None
    except OSError as error:
        raise ReportError(f"report cannot be read at {path}: {error}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise ReportError(f"report at {path} is not valid JSON: {error}") from None
    if not isinstance(data, dict):
        raise ReportError(f"report at {path} must be a JSON object, not {type(data).__name__}")
    return data


def read_specialist_report(path: Path, ticket: str) -> SpecialistReport:
    """Read and check the report of a specialist for `ticket`. Raise ReportError."""
    data = _read_json_object(path, "specialist")
    problems: list[str] = []
    for name, kind in SPECIALIST_REQUIRED.items():
        if name not in data:
            problems.append(f"{name}: field is missing")
        elif not _has_type(data[name], kind):
            problems.append(f"{name}: must be a {kind}, not {data[name]!r}")
    for name, kind in SPECIALIST_OPTIONAL.items():
        if name in data and not _has_type(data[name], kind):
            problems.append(f"{name}: must be a {kind}, not {data[name]!r}")
    if not problems:
        if data["status"] not in SPECIALIST_STATUSES:
            problems.append(f"status: {data['status']!r} is not a status; known statuses: {', '.join(SPECIALIST_STATUSES)}")
        if data["ticket"] != ticket:
            problems.append(f"ticket: the report is for ticket {data['ticket']!r}, but this step builds ticket {ticket!r}")
    if problems:
        raise ReportError(f"report at {path} is invalid: " + "; ".join(problems), problems)
    return SpecialistReport(
        data["ticket"], data["status"], data["branch"], data["head_sha"], data["commits"], data["gates_green"],
        data["summary"], data.get("blocked_reason"), data.get("judgement_calls"),
    )


def verdict_in_text(text: str) -> dict[str, object] | None:
    """The last top-level JSON object in `text` that has a `verdict` field, or None.

    A verifier that cannot write its verdict file ends its final message with
    the verdict object, as the brief asks. The object can stand in a code fence.
    """
    decoder = json.JSONDecoder()
    last: dict[str, object] | None = None
    start = text.find("{")
    while start != -1:
        try:
            found, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        # A whole object: an object inside it, with a `verdict` key of its own, is never the verdict.
        if isinstance(found, dict) and "verdict" in found:
            last = found
        start = text.find("{", end)
    return last


def read_verifier_report(path: Path, mode: str) -> VerifierReport:
    """Read and check the verdict report of a verifier that ran in `mode`. Raise ReportError.

    The report must name the mode of the run, and a REJECT must hold at least
    one finding. An ACCEPT must hold evidence for at least one criterion.
    """
    data = _read_json_object(path, "verifier")
    problems: list[str] = []
    for name, kind in VERIFIER_REQUIRED.items():
        if name not in data:
            problems.append(f"{name}: field is missing")
        elif not _has_type(data[name], kind):
            problems.append(f"{name}: must be a {kind}, not {data[name]!r}")
    if not problems:
        if data["verdict"] not in VERIFIER_VERDICTS:
            problems.append(f"verdict: {data['verdict']!r} is not a verdict; known verdicts: {', '.join(VERIFIER_VERDICTS)}")
        if data["mode"] != mode:
            problems.append(f"mode: the report names mode {data['mode']!r}, but this verifier ran in mode {mode!r}")
        if data["verdict"] == "REJECT" and not data["findings"]:
            problems.append("findings: a REJECT needs at least one finding")
        if data["verdict"] == "ACCEPT" and not data["criteria"]:
            problems.append("criteria: an ACCEPT needs evidence for at least one criterion")
    if problems:
        raise ReportError(f"report at {path} is invalid: " + "; ".join(problems), problems)
    return VerifierReport(
        data["mode"], data["verdict"], data["criteria"], data["gate_output"], data["findings"], data["unverified"]
    )


# A finding of a chain verifier starts with `[<ticket id>]` to name the ticket that it belongs to.
FINDING_LABEL = re.compile(r"\[([^\]]+)\][ \t]*(.*)", re.DOTALL)


def map_findings(findings: Sequence[str], tickets: Sequence[str]) -> tuple[dict[str, list[str]], list[str]]:
    """Split the findings of a chain verifier into the findings of each ticket and the findings that map to none.

    A finding maps to a ticket when it starts with `[<id>]` and `<id>` is one
    of `tickets`. The mapped text is the finding without its label. A finding
    with an unknown label, or with no label, is unmapped: the second value
    holds it unchanged, in order, so no finding is dropped.
    """
    mapped: dict[str, list[str]] = {}
    unmapped: list[str] = []
    for finding in findings:
        match = FINDING_LABEL.match(finding)
        if match is not None and match.group(1) in tickets:
            mapped.setdefault(match.group(1), []).append(match.group(2))
        else:
            unmapped.append(finding)
    return mapped, unmapped
