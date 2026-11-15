"""The harness version is read once for each process, and YAML is parsed with libyaml when it is installed (#345).

The seal asked `claude --version` once for each agent that it sealed: a start of a run took 2 to 6 Node starts. The renderer, the validator
and the seal parsed YAML with the pure-Python loader, 30 times slower than the C loader. Each case drives the real adapter or `main(argv)`.
The shim on `PATH` is the system boundary: it counts its calls in a file.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
import yaml

from delegate import adapters
from delegate.cli.agent_definitions import main
from delegate.definitions.declaration import Agent
from delegate.definitions.render import render
from delegate.ports.harness import AdapterError

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "examples" / "java-spring.toml"
GUARD_FORMS = ROOT / "tests" / "fixtures" / "render-snapshots" / "guard-forms.toml"

COUNTING_SHIM = """\
#!{python}
import sys
if sys.argv[1:] == ["--version"]:
    with open({count!r}, "a") as handle:
        handle.write("x\\n")
    print("{version} (Claude Code)")
    sys.exit(0)
sys.exit(1)
"""

needs_libyaml = pytest.mark.skipif(not yaml.__with_libyaml__, reason="PyYAML has no libyaml here")


def install_counting_shim(tmp_path: Path, monkeypatch, version: str = "2.1.287") -> Path:
    """Put a `claude` command first on PATH that answers `--version` and writes one line to the count file for each call."""
    bin_dir = tmp_path / "counting-bin"
    bin_dir.mkdir()
    count = tmp_path / "version-calls"
    executable = bin_dir / "claude"
    executable.write_text(COUNTING_SHIM.format(python=sys.executable, count=str(count), version=version))
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return count


def calls(count: Path) -> int:
    return len(count.read_text().splitlines()) if count.exists() else 0


def write_three_agents(root: Path, tiers) -> None:
    target = root / ".claude" / "agents"
    target.mkdir(parents=True)
    agent = Agent(key="python", description="The python stack.", gate_commands=["python3 -m pytest"])
    for name, content in render([agent], tiers)["claude-code"].items():
        (target / name).write_text(content)
    (target / "extra.md").write_text("---\nname: extra\ndescription: A third agent.\n---\nBody.\n")


def test_a_seal_of_three_agents_starts_claude_version_once(tmp_path, monkeypatch, home, tiers):
    repo = tmp_path / "repo"
    write_three_agents(repo, tiers)
    count = install_counting_shim(tmp_path, monkeypatch)
    adapter = adapters.get("claude-code")

    for agent in ("python-specialist", "python-verifier", "extra"):
        assert adapter.seal(agent, repo, tmp_path / f"seal-{agent}") is not None

    assert calls(count) == 1


def test_a_second_adapter_of_the_same_process_does_not_start_claude_version_again(tmp_path, monkeypatch, home, tiers):
    repo = tmp_path / "repo"
    write_three_agents(repo, tiers)
    count = install_counting_shim(tmp_path, monkeypatch)

    adapters.get("claude-code").seal("python-specialist", repo, tmp_path / "seal-one")
    adapters.get("claude-code").seal("python-verifier", repo, tmp_path / "seal-two")

    assert calls(count) == 1


def test_an_old_harness_is_refused_at_each_seal_of_the_process_and_claude_version_starts_once(tmp_path, monkeypatch, home, tiers):
    repo = tmp_path / "repo"
    write_three_agents(repo, tiers)
    count = install_counting_shim(tmp_path, monkeypatch, "2.1.286")
    adapter = adapters.get("claude-code")

    for agent in ("python-specialist", "python-verifier"):
        with pytest.raises(AdapterError, match=r"2\.1\.286"):
            adapter.seal(agent, repo, tmp_path / f"seal-{agent}")

    assert calls(count) == 1


def test_a_harness_that_is_replaced_after_a_seal_is_asked_again(tmp_path, monkeypatch, home, tiers):
    repo = tmp_path / "repo"
    write_three_agents(repo, tiers)
    install_counting_shim(tmp_path, monkeypatch, "2.2.0")
    assert adapters.get("claude-code").seal("python-specialist", repo, tmp_path / "seal-one") is not None
    # A new harness at a new path: the answer of the old one does not stand for it.
    new = tmp_path / "second"
    new.mkdir()
    install_counting_shim(new, monkeypatch, "2.1.286")

    with pytest.raises(AdapterError, match=r"2\.1\.286"):
        adapters.get("claude-code").seal("python-verifier", repo, tmp_path / "seal-two")


class CountingLoaders:
    """Replace the C loader and the C dumper of `yaml` with subclasses that count their uses."""

    def __init__(self, monkeypatch) -> None:
        self.loads = 0
        self.dumps = 0
        counter = self

        class Loader(yaml.CSafeLoader):
            def __init__(self, *args, **kwargs):
                counter.loads += 1
                super().__init__(*args, **kwargs)

        class Dumper(yaml.CSafeDumper):
            def __init__(self, *args, **kwargs):
                counter.dumps += 1
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(yaml, "CSafeLoader", Loader)
        monkeypatch.setattr(yaml, "CSafeDumper", Dumper)


def render_tree(declaration: Path, out: Path) -> dict[str, bytes]:
    assert main(["render", str(declaration), "-o", str(out)]) == 0
    return {str(path.relative_to(out)): path.read_bytes() for path in sorted(out.rglob("*")) if path.is_file()}


@needs_libyaml
@pytest.mark.parametrize("declaration", [EXAMPLE, GUARD_FORMS], ids=["java-spring", "guard-forms"])
def test_the_renderer_writes_the_same_bytes_with_the_c_dumper_and_with_the_python_dumper(tmp_path, monkeypatch, declaration):
    counting = CountingLoaders(monkeypatch)
    with_c = render_tree(declaration, tmp_path / "c")
    assert counting.dumps > 0, "the renderer used the C dumper"

    monkeypatch.delattr(yaml, "CSafeDumper")
    monkeypatch.delattr(yaml, "CSafeLoader")
    with_python = render_tree(declaration, tmp_path / "python")

    assert with_c.keys() == with_python.keys()
    assert with_c == with_python


@needs_libyaml
def test_the_validator_parses_the_frontmatter_with_the_c_loader_and_finds_the_same_as_with_the_python_loader(tmp_path, monkeypatch, capsys):
    render_tree(EXAMPLE, tmp_path / "out")
    capsys.readouterr()  # the lines that the render printed
    counting = CountingLoaders(monkeypatch)
    code_c = main(["validate", str(tmp_path / "out")])
    out_c = capsys.readouterr().out
    assert counting.loads > 0, "the validator used the C loader"

    monkeypatch.delattr(yaml, "CSafeLoader")
    code_python = main(["validate", str(tmp_path / "out")])
    out_python = capsys.readouterr().out

    assert (code_c, out_c) == (code_python, out_python)


def test_the_renderer_and_the_validator_work_when_yaml_has_no_libyaml(tmp_path, monkeypatch, capsys):
    monkeypatch.delattr(yaml, "CSafeDumper", raising=False)
    monkeypatch.delattr(yaml, "CSafeLoader", raising=False)

    render_tree(EXAMPLE, tmp_path / "out")

    assert main(["validate", str(tmp_path / "out")]) == 0
