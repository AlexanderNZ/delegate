"""The shell backend of the gate port in `delegate.ports.gates`.

It runs a gate command with `bash -c`, in the worktree of the ticket, through
`subprocess`. The run context never starts a process: this module does it for
the gates, as `delegate.adapters.git` does it for git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..ports.gates import GateResult


class ShellGateRunner:
    """Runs a gate command through `bash -c`."""

    def run(self, command: str, worktree: Path) -> GateResult:
        r = subprocess.run(["bash", "-c", command], cwd=worktree, capture_output=True, text=True)
        return GateResult(exit_status=r.returncode, output=r.stdout + r.stderr)
