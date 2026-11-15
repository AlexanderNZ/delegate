"""A constant name has one value in the run context, and the journal values have one home (#361).

The scan reads every module of `src/delegate/run` with the `ast` module. It looks at the constants at the top level of a module: a name in
capital letters with a literal value. The other packages (`cli`, `adapters`, `definitions`) keep their own local constants, such as an exit
code, and a name there can differ from a name in `run` on purpose.
"""

from __future__ import annotations

import ast
from pathlib import Path

import delegate
from delegate.run import domain, events

SOURCE = Path(delegate.__file__).parent
RUN = SOURCE / "run"


def _constants() -> dict[str, dict[str, set[str]]]:
    """Name -> repr of its value -> the files that give it that value, for each literal constant at the top level of a module of `run`."""
    found: dict[str, dict[str, set[str]]] = {}
    for path in sorted(RUN.rglob("*.py")):
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
            elif isinstance(node, ast.Assign):
                targets = node.targets
            else:
                continue
            for target in targets:
                if not (isinstance(target, ast.Name) and target.id.isupper()):
                    continue
                try:
                    value = ast.literal_eval(node.value)
                except ValueError:
                    continue
                found.setdefault(target.id, {}).setdefault(repr(value), set()).add(path.name)
    return found


def _files_with(text: str) -> list[str]:
    """The files of `src/delegate` that hold `text`."""
    return [
        str(path.relative_to(SOURCE)) for path in sorted(SOURCE.rglob("*.py")) if text in path.read_text()
    ]


def test_no_two_constants_of_the_run_context_have_the_same_name_and_different_values():
    clashes = {name: values for name, values in _constants().items() if len(values) > 1}

    assert clashes == {}


def test_the_phase_of_a_gate_of_a_fix_up_is_not_the_phase_that_a_view_shows():
    assert domain.PHASE_FIXUP == "fix-up"
    assert events.GATE_PHASE_FIXUP == "fixup"
    assert events.RETRY_PHASE_FIXUP == "fixup"
    assert events.ROLE_FIXUP == "fix-up"


def test_the_names_of_the_directories_of_a_verifier_copy_are_written_in_one_file():
    assert _files_with('"delegate-verify-"') == ["run/state_location.py"]


def test_the_name_of_the_journal_file_is_written_in_one_file():
    assert _files_with('"journal.jsonl"') == ["run/state_location.py"]
