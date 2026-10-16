"""The protocol skill and the watch and resume how-to page tell a coordinator agent how to report a run to the person.

Issue #63 and its comments give the rules. Both documents hold them as one
numbered list, in the same order, so the skill and the page cannot drift. The
skill is outside the package source, as are the docs, so a Nix build that
copies only the package skips these cases with a reason.
"""

import re

from .pages import HOW_TO, outside_the_package
from .test_skill_modes import SKILL, in_the_kit, section

PAGE = HOW_TO / "watch-and-resume-a-run.md"

# Each rule, in its order, and the words that one list item must hold to state it.
RULES: list[tuple[str, tuple[str, ...]]] = [
    ("start the run detached, in a new session", ("detached", "`setsid -f`", "start_new_session=True")),
    ("follow the run with watch as a background task, and go on from a position", ("`delegate watch`", "background task", "`--from")),
    ("show the output of status as it is, and write no script to read the journal or an event stream",
     ("`delegate status`", "as it is", "does not write its own script", "journal", "event stream")),
    ("report a stalled agent at once", ("stalled agent", "`--stall-minutes", "at once")),
    ("resume a stopped run, and break the lock of a process that no longer exists", ("--resume <run-id>", "`--break-lock`", "no longer exists")),
]


def numbered_items(text: str) -> list[str]:
    """The items of the numbered list in `text`, each as one line of text."""
    items = re.split(r"^\d+\. ", text, flags=re.MULTILINE)[1:]
    return [" ".join(item.split()) for item in items]


def skill_rules() -> list[str]:
    engine = section(SKILL.read_text(), "## The workflow engine and its modes")
    return numbered_items(section(engine, "### Report a run to the person"))


def page_rules() -> list[str]:
    return numbered_items(section(PAGE.read_text(), "## Report a run as a coordinator agent"))


def assert_states_the_rules(items: list[str]) -> None:
    assert len(items) == len(RULES), items
    for item, (rule, words) in zip(items, RULES):
        missing = [word for word in words if word not in item]
        assert not missing, f"the item for the rule {rule!r} does not hold {missing}: {item}"


@in_the_kit
def test_the_workflow_engine_section_of_the_skill_states_the_rules_to_report_a_run():
    assert_states_the_rules(skill_rules())


@outside_the_package
def test_the_watch_and_resume_page_states_the_rules_to_report_a_run():
    assert_states_the_rules(page_rules())

