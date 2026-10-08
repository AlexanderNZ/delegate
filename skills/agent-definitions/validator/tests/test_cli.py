import subprocess
import sys

from agent_definitions.cli import main
from tests.conftest import EXAMPLE


def test_render_then_validate_round_trip(tmp_path, capsys):
    assert main(["render", str(EXAMPLE), "-o", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "claude-code/java-spring-specialist.md" in out
    assert main(["validate", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip().endswith("ok")


def test_validate_exits_one_on_findings(tmp_path, capsys):
    main(["render", str(EXAMPLE), "-o", str(tmp_path)])
    (tmp_path / "claude-code" / "java-spring-verifier.md").unlink()
    assert main(["validate", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert "MISSING_TWIN" in captured.out


def test_validate_with_nothing_exits_two(capsys):
    assert main(["validate"]) == 2


def test_module_entry_point_runs():
    r = subprocess.run([sys.executable, "-m", "agent_definitions.cli", "--help"], capture_output=True, text=True)
    assert r.returncode == 0 and "render" in r.stdout
