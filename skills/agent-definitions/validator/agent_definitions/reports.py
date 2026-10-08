"""The agent report: a JSON file that an agent writes to the path its brief names.

`read_specialist_report` reads a specialist's report and checks it against the
schema below. A missing file, text that is not JSON, a missing field, a field of
the wrong type, and a value outside the permitted set each raise ReportError.
The message names the field. A field that the schema does not list is ignored.

`read_verifier_report` does the same for a verifier's verdict report.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

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
VERIFIER_VERDICTS: tuple[str, ...] = ("ACCEPT", "REJECT")

# The required fields of a verifier report, with the type of each.
VERIFIER_REQUIRED: dict[str, str] = {
    "mode": "string",
    "verdict": "string",
    "criteria": "list of objects with the strings criterion and evidence",
    "gate_output": "string",
    "findings": "list of strings",
    "unverified": "list of strings",
}


class ReportError(Exception):
    """A report is missing or invalid. The message names the path or the field."""


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
        raise ReportError(f"report at {path} is invalid: " + "; ".join(problems))
    return SpecialistReport(
        data["ticket"], data["status"], data["branch"], data["head_sha"], data["commits"], data["gates_green"],
        data["summary"], data.get("blocked_reason"), data.get("judgement_calls"),
    )


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
        raise ReportError(f"report at {path} is invalid: " + "; ".join(problems))
    return VerifierReport(
        data["mode"], data["verdict"], data["criteria"], data["gate_output"], data["findings"], data["unverified"]
    )
