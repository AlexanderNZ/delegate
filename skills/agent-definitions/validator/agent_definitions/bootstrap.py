"""Write a repository's context skill, its declaration, and its rendered pair.

The command replaces a bootstrap by hand, so the context skills of different
repositories come from one template and do not drift apart. It composes
`render` and `validate`; it keeps no second copy of either. It is a pure
function of its arguments, so a second run with the same arguments writes the
same bytes.

Two templates in `templates/` hold the shape of the context skill, the same way
the agent templates hold the shape of an agent file. Both are
`string.Template` files, so every dollar sign the generated text needs is
written twice in the template: `$$(...)` in `context-skill.md` renders as
`$(...)` in the skill. The load-time blocks hold no dollar sign: each one is
one plain command, because a compound block can stop a subagent from
starting (see `live_read`).
"""

from __future__ import annotations

import re
import shlex
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from string import Template

import yaml

from .declaration import parse_declaration
from .render import render
from .tiers import Tiers, load_tiers
from .validate import validate_set

#: Claude Code reads a project skill from `.claude/skills/<name>/SKILL.md` and
#: from nowhere else, so that is the default. A repository that deploys its
#: skills from another directory passes `--skills-root`.
DEFAULT_SKILLS_ROOT = ".claude/skills"

#: The harness directories the rendered pair is copied into.
AGENT_DIRS = {"claude-code": ".claude/agents", "opencode": ".opencode/agents"}

_BACKTICKED = re.compile(r"`([^`]+)`")


def _template(name: str) -> Template:
    return Template(resources.files(__package__).joinpath("templates", f"{name}.md").read_text())


def reference_path(value: str) -> str:
    """The repository-relative path a `--reference` value names.

    A reference is written as it lands in the declaration, so it may carry a
    note: ```docs/operate.md` — the component map``. The path is then the first
    backticked word. A value with no backtick is the path itself.
    """
    m = _BACKTICKED.search(value)
    return (m.group(1) if m else value).strip()


def _heading(value: str) -> str:
    v = value.strip()
    return v if "`" in v else f"`{v}`"


def _frontmatter(fm: dict) -> str:
    # The same writer the agent files use: a description that holds a colon is
    # not a valid plain YAML scalar, and a hand-built line would emit one.
    return (
        "---\n"
        + yaml.safe_dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100000)
        + "---\n"
    )


def skill_description(name: str, domain: str, references: list[str]) -> str:
    """The frontmatter description: the domain sentence, then a trigger clause.

    A skill that is preloaded by an agent file needs no trigger. This skill is
    also found by discovery, from the main session and from an agent that does
    not preload it, and discovery reads the description alone. A description
    with no "use when" clause and no document names is a skill that is never
    reached that way.
    """
    head = domain.strip()
    if head and head[-1] not in ".!?":
        head += "."
    paths = ", ".join(f"`{reference_path(r)}`" for r in references)
    return f"{head} Use when you work in {name} and you need one of its reference documents: {paths}."


#: A reference of at most this many lines, counted when bootstrap writes the
#: skill, is printed in full. A longer one gives its heading index.
FULL_PRINT_LINES = 200


def live_read(repo: Path, path: str) -> tuple[str, str]:
    """The note and the load-time command for one reference.

    The command is one plain command. Claude Code runs a load-time block only
    when it can analyse the command: $(...), ;, if or a pipe stops a subagent
    from starting in the manual, acceptEdits and auto permission modes
    (measured on Claude Code 2.1.283, 2026-09-26). So the choice between the full
    text and the heading index is made here, when the skill is written, and
    not in the block.

    The command is `git grep` with a `:/` pathspec. git resolves `:/` from the
    top of the working tree, so the block reads the same file from the root and
    from any subdirectory, and in a linked worktree it reads the files of that
    worktree. A path relative to the working directory failed when the
    coordinator shell was in a subdirectory. `--untracked` with
    `--no-exclude-standard` also reads a file that is not in the index and a
    file that `.gitignore` names, so the block reads any file on disk, as the
    earlier `sed` block did. `-E` sets the pattern type, because
    `grep.patternType` in a user's git config changes the default.
    """
    pathspec = shlex.quote(f":/{path}")
    lines = len((repo / path).read_text(errors="replace").splitlines())
    if lines <= FULL_PRINT_LINES:
        return (
            f"The block prints this document in full, up to {FULL_PRINT_LINES} lines.",
            f"git grep --untracked --no-exclude-standard -h -E -m {FULL_PRINT_LINES} -e '^' -- {pathspec}",
        )
    return (
        f"This document had {lines} lines when this skill was written, so the block prints its "
        f"heading index. Read one section from the repository root with "
        f"`sed -n '<start>,<end>p' {path}`.",
        f"git grep --untracked --no-exclude-standard -h -n -E -e '^#{{1,6}} ' -- {pathspec}",
    )


def context_skill(repo: Path, name: str, domain: str, references: list[str], command: str) -> str:
    """The `<name>-context` skill, one live-read section per reference."""
    section = _template("context-skill-section")
    blocks = []
    for r in references:
        note, read = live_read(repo, reference_path(r))
        blocks.append(section.substitute(heading=_heading(r), note=note, read=read).strip("\n"))
    sections = "\n\n".join(blocks)
    text = _template("context-skill").substitute(
        frontmatter=_frontmatter(
            {"name": f"{name}-context", "description": skill_description(name, domain, references)}
        ),
        command=command,
        title=name,
        domain=domain,
        sections=sections,
    )
    return text.rstrip("\n") + "\n"


def _toml_string(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_key(name: str) -> str:
    """A bare key where TOML allows one, a quoted key otherwise.

    `[agents.acme.api]` is two nested tables, not one agent called `acme.api`.
    The quoted form keeps the name whole, and the validator then reports
    `NAME_INVALID` for a name no harness accepts.
    """
    return name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else _toml_string(name)


def _toml_multiline(s: str) -> str:
    body = s.strip("\n").replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    if body.endswith('"'):
        body = body[:-1] + '\\"'
    return '"""\n' + body + '\n"""'


def declaration_toml(
    name: str,
    domain: str,
    tier: str,
    skills: list[str],
    references: list[str],
    prompt: str,
    max_turns: int | None,
    command: str,
    gate_commands: Sequence[str] = (),
) -> str:
    """The `[agents.<name>]` declaration, in the key order the skill documents."""
    lines = [
        "# Generated by `agent-definitions bootstrap`. Do not change this file by",
        "# hand; the next run replaces it. Re-run from the repository root:",
        "#",
        f"#   {command}",
        "",
        f"[agents.{_toml_key(name)}]",
        f"description = {_toml_string(domain)}",
        f"tier = {_toml_string(tier)}",
        "skills = [" + ", ".join(_toml_string(s) for s in skills) + "]",
    ]
    if max_turns is not None:
        lines.append(f"maxTurns = {max_turns}")
    lines.append("references = [")
    lines += [f"  {_toml_string(r)}," for r in references]
    lines.append("]")
    if gate_commands:
        lines.append("gateCommands = [" + ", ".join(_toml_string(c) for c in gate_commands) + "]")
    if prompt.strip():
        lines.append("prompt = " + _toml_multiline(prompt))
    return "\n".join(lines) + "\n"


def rerun_command(
    name: str,
    domain: str,
    tier: str,
    skills: list[str],
    references: list[str],
    prompt_file: str | None,
    max_turns: int | None,
    skills_root: str,
    gate_commands: Sequence[str] = (),
) -> str:
    """The command that reproduces this run, with `--repo .` for portability.

    The header of every generated file carries it. An absolute `--repo` would
    put one machine's path into a committed file, so the command names the
    repository root as `.` and expects the reader to run it from there.
    """
    parts = ["agent-definitions", "bootstrap", "--repo", ".", "--name", name, "--domain", domain, "--tier", tier]
    for s in skills:
        parts += ["--skill", s]
    for r in references:
        parts += ["--reference", r]
    if prompt_file:
        parts += ["--prompt-file", prompt_file]
    if max_turns is not None:
        parts += ["--max-turns", str(max_turns)]
    for c in gate_commands:
        parts += ["--gate-command", c]
    parts += ["--skills-root", skills_root]
    return " ".join(shlex.quote(p) for p in parts)


def _write(files: dict[Path, str]) -> None:
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def _validate_rendered(
    rendered: dict[str, dict[str, str]],
    tiers: Tiers,
    skills_root: Path,
    user_skills_dir: Path | None = None,
) -> list:
    """Render to a temporary directory and validate it, never the repository.

    A project agents directory can hold hand-written files that fail the schema
    for reasons outside this bootstrap. Only the new pair is under test.
    """
    tmp = Path(tempfile.mkdtemp(prefix="agent-definitions-bootstrap-"))
    try:
        for harness, files in rendered.items():
            d = tmp / harness
            d.mkdir(parents=True, exist_ok=True)
            for fname, content in files.items():
                (d / fname).write_text(content)
        return validate_set(tiers, tmp / "claude-code", tmp / "opencode", skills_root, user_skills_dir=user_skills_dir)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class BootstrapError(Exception):
    """An argument the command cannot act on. The message names what is wrong."""


def plan(
    repo: Path,
    name: str,
    domain: str,
    tier: str,
    skills: list[str],
    references: list[str],
    prompt_file: str | None,
    max_turns: int | None,
    skills_root: str,
    tiers: Tiers,
    gate_commands: Sequence[str] = (),
) -> tuple[dict[Path, str], dict[str, dict[str, str]]]:
    """The skill and the declaration to write, and the pair to render from them.

    It reads the arguments and the prompt file, and it writes nothing.
    """
    if not repo.is_dir():
        raise BootstrapError(f"repository not found: {repo}")
    missing = [reference_path(r) for r in references if not (repo / reference_path(r)).exists()]
    if missing:
        raise BootstrapError("\n".join(f"reference not found in {repo}: {m}" for m in missing))
    # A load-time block reads one file. A directory is not a document.
    not_files = [reference_path(r) for r in references if not (repo / reference_path(r)).is_file()]
    if not_files:
        raise BootstrapError("\n".join(f"reference is not a file in {repo}: {m}" for m in not_files))
    prompt = ""
    if prompt_file:
        p = Path(prompt_file)
        if not p.is_file():
            raise BootstrapError(f"prompt file not found: {p}")
        prompt = p.read_text()

    command = rerun_command(
        name, domain, tier, skills, references, prompt_file, max_turns, skills_root, gate_commands
    )
    declared_skills = [f"{name}-context", *skills]
    skill_dir = repo / skills_root / f"{name}-context"
    sources = {
        skill_dir / "SKILL.md": context_skill(repo, name, domain, references, command),
        skill_dir / "agents.toml": declaration_toml(
            name, domain, tier, declared_skills, references, prompt, max_turns, command, gate_commands
        ),
    }

    decl_text = sources[skill_dir / "agents.toml"]
    try:
        agents = parse_declaration(tomllib.loads(decl_text))
        rendered = render(agents, tiers)
    except (ValueError, KeyError) as e:  # an unknown tier, or a name render cannot use
        raise BootstrapError(str(e)) from e

    return sources, rendered


def run(
    repo: Path,
    name: str,
    domain: str,
    tier: str,
    skills: list[str],
    references: list[str],
    prompt_file: str | None,
    max_turns: int | None,
    skills_root: str,
    dry_run: bool,
    tiers: Tiers,
    user_skills_dir: Path | None = None,
    gate_commands: Sequence[str] = (),
) -> int:
    try:
        sources, rendered = plan(
            repo, name, domain, tier, skills, references, prompt_file, max_turns, skills_root, tiers,
            gate_commands,
        )
    except BootstrapError as e:
        print(e, file=sys.stderr)
        return 1

    pair = {
        repo / AGENT_DIRS[harness] / fname: content
        for harness, files in rendered.items()
        for fname, content in files.items()
    }

    if dry_run:
        for path, content in {**sources, **pair}.items():
            print(f"--- {path} ---")
            print(content if content.endswith("\n") else content + "\n", end="")
        return 0

    # The skill and the declaration go in first: the validator reports
    # MISSING_SKILL for the context skill until it is on disk.
    _write(sources)
    findings = _validate_rendered(rendered, tiers, repo / skills_root, user_skills_dir=user_skills_dir)
    if findings:
        for f in findings:
            print(f)
        print(f"{len(findings)} finding(s); no agent file was written", file=sys.stderr)
        return 1

    _write(pair)
    for path in [*sources, *pair]:
        print(path)
    return 0


def run_from_args(args) -> int:
    try:
        tiers = load_tiers(args.tiers)
    except (OSError, KeyError) as e:
        print(f"tier table not readable: {e}", file=sys.stderr)
        return 1
    raw_usd = getattr(args, "user_skills_dir", None)
    return run(
        repo=Path(args.repo).resolve(),
        name=args.name,
        domain=args.domain,
        tier=args.tier,
        skills=list(args.skill),
        references=list(args.reference),
        prompt_file=args.prompt_file,
        max_turns=args.max_turns,
        skills_root=args.skills_root,
        dry_run=args.dry_run,
        tiers=tiers,
        user_skills_dir=Path(raw_usd) if raw_usd else None,
        gate_commands=list(getattr(args, "gate_command", None) or []),
    )
