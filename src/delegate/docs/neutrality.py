"""The neutrality check: the kit holds no personal or company term.

The kit is the two skill directories `agent-delegation/` and
`agent-definitions/`, in `skills/` of the repository, and the two plugin
directories `plugins/` and `.claude-plugin/`, in the repository root. The
consumer keeps a private denylist outside the kit and gives its path in
AGENT_DEFINITIONS_DENYLIST. The check reads every file in the kit and reports
each line that holds a denylisted term, unless the allowlist names that file
and that term.

A skill file has a path relative to the kit root, the directory that holds both
skill directories. A plugin file has a path relative to the parent of the kit
root, which is the repository root.

Matching is case-insensitive and by substring. The allowlist is in the kit,
at `agent-definitions/neutrality-allowlist.txt`. It names only terms that the
kit already holds, so it shows no term that the kit does not show. The check
reports a STALE_ALLOWLIST_ENTRY for an entry whose file does not hold its
term, or that names a file that the check does not scan.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

DENYLIST_VAR = "AGENT_DEFINITIONS_DENYLIST"
KIT_ROOT_VAR = "AGENT_DEFINITIONS_KIT_ROOT"
KIT_DIRS = ("agent-delegation", "agent-definitions")
# The directories that ship the plugins, in the parent of the kit root.
PLUGIN_DIRS = ("plugins", ".claude-plugin")
ALLOWLIST = "agent-definitions/neutrality-allowlist.txt"

# A pip build, pytest and macOS write these into a source tree. They copy or
# describe kit files, so a scan of them reports each match a second time.
GENERATED_DIRS = frozenset({"__pycache__", ".pytest_cache", "build"})
GENERATED_DIR_SUFFIXES = (".egg-info",)
GENERATED_FILES = frozenset({".DS_Store"})
# Claude Code writes type declarations into a plugin that it loads from a
# folder, at `plugins/<name>/.claude-plugin/types/`.
GENERATED_PLUGIN_DIR = (".claude-plugin", "types")


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    term: str


@dataclass(frozen=True, order=True)
class StaleEntry:
    """An allowlist entry whose file does not hold its term, or does not exist."""

    line: int
    path: str
    term: str


@dataclass(frozen=True)
class Report:
    skipped: str | None
    findings: tuple[Finding, ...] = ()
    files: int = 0
    terms: int = 0
    stale: tuple[StaleEntry, ...] = ()

    @property
    def passed(self) -> bool:
        return self.skipped is None and not self.findings and not self.stale

    def summary(self) -> str:
        if self.skipped is not None:
            return self.skipped
        return (
            f"neutrality check ran: {self.files} kit file(s), {self.terms} denylist "
            f"term(s), {len(self.findings)} finding(s) outside the allowlist, "
            f"{len(self.stale)} stale allowlist entr(y/ies)"
        )

    def format(self) -> str:
        lines = [
            f"DENYLISTED_TERM {f.path}:{f.line}: [{f.term}] is not in {ALLOWLIST}"
            for f in self.findings
        ]
        lines += [
            f"STALE_ALLOWLIST_ENTRY {ALLOWLIST}:{s.line}: [{s.path} {s.term}] "
            "names a file that does not hold the term, or a file that is not in the kit; "
            "remove the entry"
            for s in self.stale
        ]
        return "\n".join([*lines, self.summary()])


def _load_terms(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"{DENYLIST_VAR} names [{path}], and no file is there")
    terms = []
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(line.split()) != 1:
            raise ValueError(
                f"{path}:{number}: a denylist term has no space, because an "
                f"allowlist entry cannot name it; got [{line}]"
            )
        terms.append(line.lower())
    if not terms:
        raise ValueError(f"{path}: the denylist holds no term, so the check proves nothing")
    return terms


def _load_allowlist(path: Path) -> dict[tuple[str, str], int]:
    """Read the `<path> <term>` entries, each with its line number.

    A kit with no allowlist has none.
    """
    if not path.is_file():
        return {}
    entries: dict[tuple[str, str], int] = {}
    for number, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) != 2:
            raise ValueError(
                f"{path}:{number}: an allowlist entry is `<path> <term>`, got [{line}]"
            )
        entries.setdefault((fields[0], fields[1].lower()), number)
    return entries


def _is_generated(rel_parts: tuple[str, ...]) -> bool:
    *dirs, name = rel_parts
    if name in GENERATED_FILES:
        return True
    return any(
        d in GENERATED_DIRS or d.endswith(GENERATED_DIR_SUFFIXES) for d in dirs
    )


def _is_generated_plugin_file(rel_parts: tuple[str, ...]) -> bool:
    # plugins/<name>/.claude-plugin/types/...
    return rel_parts[0] == "plugins" and rel_parts[2:4] == GENERATED_PLUGIN_DIR


def _files_under(root: Path, top_dirs: tuple[str, ...], missing: str) -> list[tuple[str, Path]]:
    files = []
    for top_dir in top_dirs:
        top = root / top_dir
        if not top.is_dir():
            raise FileNotFoundError(f"[{root}] has no {top_dir}/ directory; {missing}")
        for path in sorted(top.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(root)
            if _is_generated(rel.parts) or _is_generated_plugin_file(rel.parts):
                continue
            # The allowlist names the terms it permits, so it is not scanned.
            if rel.as_posix() == ALLOWLIST:
                continue
            files.append((rel.as_posix(), path))
    return files


def _kit_files(kit_root: Path) -> list[tuple[str, Path]]:
    skills = _files_under(
        kit_root,
        KIT_DIRS,
        f"set {KIT_ROOT_VAR} to the directory that holds both skill directories",
    )
    plugins = _files_under(
        kit_root.parent,
        PLUGIN_DIRS,
        f"the parent of the kit root must hold both plugin directories {PLUGIN_DIRS}",
    )
    return skills + plugins


def check_kit(environ: Mapping[str, str], default_kit_root: Path) -> Report:
    """Scan the kit. Skip with a notice when the denylist variable is not set.

    `default_kit_root` is the directory that holds both skill directories. The
    KIT_ROOT_VAR variable replaces it, for a build that copies the package
    source away from the kit. The parent of the kit root holds both plugin
    directories.
    """
    denylist = environ.get(DENYLIST_VAR, "")
    if not denylist:
        return Report(
            skipped=(
                f"neutrality check skipped: {DENYLIST_VAR} is not set. "
                "Set it to the path of a denylist file to run the check."
            )
        )
    terms = _load_terms(Path(denylist))
    kit_root = Path(environ.get(KIT_ROOT_VAR) or default_kit_root)
    allowed = _load_allowlist(kit_root / ALLOWLIST)
    files = _kit_files(kit_root)
    findings = []
    texts = {}
    for rel, path in files:
        texts[rel] = path.read_text().lower()
        for number, text in enumerate(texts[rel].splitlines(), start=1):
            for term in terms:
                if term in text and (rel, term) not in allowed:
                    findings.append(Finding(rel, number, term))
    # An entry is stale when the scanned file it names does not hold its term,
    # or when it names no scanned file. A stale entry makes the allowlist the
    # only kit file that shows the term.
    stale = [
        StaleEntry(number, rel, term)
        for (rel, term), number in allowed.items()
        if term not in texts.get(rel, "")
    ]
    return Report(
        skipped=None,
        findings=tuple(sorted(findings)),
        files=len(files),
        terms=len(terms),
        stale=tuple(sorted(stale)),
    )
