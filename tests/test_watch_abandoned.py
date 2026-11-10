"""The live view of `delegate watch` of an abandoned run shows the state `abandoned` and the reason (#201)."""

import pytest

from .support import bare_repo, frames
from .test_watch_live import Run, local_zone, watch  # noqa: F401 - local_zone is a fixture

# The commands of each case use the fake clock of the case (#297): its helpers set it.
pytestmark = pytest.mark.usefixtures("clock")

REASON = "the owner cancelled the work"


def test_the_live_view_of_an_abandoned_run_shows_the_state_in_the_run_line_and_the_reason_in_the_feed(tmp_path, monkeypatch, local_zone):
    local_zone("NZT-13")
    repo = bare_repo(tmp_path)
    run = Run(repo, "a")
    run.add(1, "run-end", result="abandoned", built=[], failed=[], skipped=[], reason=REASON)

    code, out = watch(repo, monkeypatch)

    assert code == 1
    (last,) = frames(out)
    assert last[0].startswith("run r1  abandoned ")
    assert last[-1] == f"16:00  run abandoned · 1s · reason: {REASON} · tokens 0 new, 0 cache"
