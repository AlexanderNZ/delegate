"""Helpers for the tests of the how-to pages: read the fenced blocks of a page, write its files, follow its commands.

A page tells a reader to write some files and to run some commands. These
helpers let a test do the same, word for word, in a temporary git repository.
The `delegate` commands run in this process through `delegate.main`, and any
other command runs in bash.

A block that holds a file has the path as its title, as in
`` ```toml title="workflow.toml" ``. The site renders this form. A block with
no title is a command or an output sample.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shlex
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from delegate.cli import delegate

def repository_root(test_file: Path) -> Path:
    """The directory above the tests directory. A Nix build copies only the
    package, its tests and its example to /build/source, so there the root holds
    no README. A path with no directory above the tests directory gives the top
    of the path, which holds no README either."""
    parents = test_file.resolve().parents
    return parents[1] if len(parents) > 1 else parents[-1]


ROOT = repository_root(Path(__file__))
README = ROOT / "README.md"  # its presence marks a source tree with the docs
HOW_TO = ROOT / "docs" / "how-to"

outside_the_package = pytest.mark.skipif(
    not README.is_file(), reason="the docs are outside the package source, as in a Nix build"
)

# A fence has three backticks or more. A block that holds a fenced block uses a longer outer fence.
FENCE = re.compile(r'^(`{3,})(\w+)(?: title="([^"]+)")?\n(.*?)^\1$', flags=re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Block:
    """One fenced block of a page: its language, the path of the file it holds (or None), and its text."""

    lang: str
    path: str | None
    body: str


@dataclass(frozen=True)
class Step:
    """One command that a test ran: the command, its exit code, and what it wrote."""

    command: str
    code: int
    out: str
    err: str


def blocks(page: Path) -> list[Block]:
    return [Block(lang, path or None, body) for _fence, lang, path, body in FENCE.findall(page.read_text())]


def file_block(page: Path, path: str) -> Block:
    """The block of the page that holds the file `path`. Exactly one must exist."""
    found = [block for block in blocks(page) if block.path == path]
    assert len(found) == 1, f"{page.name} must hold exactly one block for {path}, found {len(found)}"
    return found[0]


def write_files(page: Path, root: Path) -> list[str]:
    """Write every file block of the page under `root`. Return the paths."""
    written = []
    for block in blocks(page):
        if block.path:
            target = root / block.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(block.body)
            written.append(block.path)
    return written


def commands(block: Block) -> list[str]:
    """The commands of a bash block, in order. A trailing backslash joins two lines."""
    lines = block.body.replace("\\\n", " ").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def new_repo(path: Path) -> Path:
    """An empty git repository on `main`, with an identity for the commits that a page makes."""
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    return path


def follow(
    page: Path, cwd: Path, monkeypatch, *, replace: Mapping[str, str] = {}, skip: tuple[str, ...] = (),
    only: Callable[[Block], bool] = lambda block: True,
) -> list[Step]:
    """Run every command of every bash block of the page, in order, and return the steps.

    `only` selects the bash blocks to run, for a page whose blocks belong to
    different moments (a run, then an interruption, then a resume).

    `replace` maps a placeholder of the page (for example `<run-id>`) to the text
    that a real run gives. A command that starts with one of `skip` is not run.
    A command that is not `delegate` must exit 0.
    """
    steps: list[Step] = []
    for block in blocks(page):
        if block.lang != "bash" or not only(block):
            continue
        for command in commands(block):
            for placeholder, value in replace.items():
                command = command.replace(placeholder, value)
            if skip and command.startswith(skip):
                continue
            if command.startswith("cd "):
                cwd = (cwd / command.removeprefix("cd ")).resolve()
            elif command.startswith("delegate "):
                monkeypatch.chdir(cwd)
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    try:
                        code = delegate.main(shlex.split(command)[1:])
                    except SystemExit as stop:  # argparse exits for `--help` and for a usage error
                        code = stop.code if isinstance(stop.code, int) else 0
                steps.append(Step(command, code, out.getvalue(), err.getvalue()))
            else:
                done = subprocess.run(["bash", "-ec", command], cwd=cwd, capture_output=True, text=True, env=os.environ.copy())
                assert done.returncode == 0, (command, done.stdout, done.stderr)
                steps.append(Step(command, 0, done.stdout, done.stderr))
    return steps
