"""The ssh backend of the host reader says why a host is unreachable with a kind, and the use case makes the sentence (#333).

The backend names the older version of a `delegate` without `delegate runs` as the kind `OLDER_VERSION` of `Unreachable`; it knows no version and makes no
sentence for it. The `ssh` of a case is a shim on `PATH`. The expected values come from the ticket and from `docs/reference/runs.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from delegate.adapters.ssh_hosts import SshHostReader
from delegate.ports.hosts import OLDER_VERSION, Unreachable


def ssh_that_says(directory: Path, text: str, status: int) -> Path:
    """A shim `ssh` that writes `text` on standard error and exits with `status`. Return its directory."""
    script = directory / "bin" / "ssh"
    script.parent.mkdir(parents=True)
    script.write_text(f"#!/bin/sh\nprintf '%s\\n' \"{text}\" >&2\nexit {status}\n")
    script.chmod(0o755)
    return script.parent


@pytest.mark.parametrize("words", ["delegate: unknown subcommand 'runs'; expected one of: render, validate", "delegate: error: invalid choice: 'runs'"])
def test_a_host_with_no_runs_command_is_unreachable_with_the_kind_older_version(tmp_path, monkeypatch, words):
    monkeypatch.setenv("PATH", f"{ssh_that_says(tmp_path, words, 2)}:/usr/bin:/bin")

    answer = SshHostReader().read_runs("far", 5)

    assert isinstance(answer, Unreachable)
    assert answer.kind == OLDER_VERSION


def test_a_host_that_ssh_cannot_reach_has_no_kind_and_a_reason_from_the_backend(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", f"{ssh_that_says(tmp_path, 'Connection refused', 255)}:/usr/bin:/bin")

    answer = SshHostReader().read_runs("far", 5)

    assert answer == Unreachable("ssh cannot reach far: Connection refused")
