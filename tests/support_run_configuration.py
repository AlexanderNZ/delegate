"""The helpers of the tests that record the configuration of a run: the paused run and the change of its workflow (#354)."""

from __future__ import annotations

from delegate.cli import delegate

from .support import HEAD, run_main, state_dir, ticket, ticket_of


RUN_PAUSED = 3


WORKFLOW = HEAD + ticket("a") + ticket("b", ("a",))


def paused_run(repo, capsys, scripted) -> str:
    """Start the run of `workflow.toml`, pause it while the specialist of ticket a works, and return the run id."""

    def request(request):
        if ticket_of(request) == "a" and scripted.on_specialist is not None:
            scripted.on_specialist = None
            (run_dir,) = (state_dir(repo) / "runs").iterdir()
            assert delegate.main(["pause", run_dir.name, "--repo", str(repo)]) == 0

    scripted.on_specialist = request
    code, out, err = run_main(capsys, str(repo / "workflow.toml"), "--repo", str(repo))
    assert code == RUN_PAUSED, err
    (run_dir,) = (state_dir(repo) / "runs").iterdir()
    return run_dir.name


def change_workflow(repo, old: str, new: str) -> None:
    path = repo / "workflow.toml"
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))
