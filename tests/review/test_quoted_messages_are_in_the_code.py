"""The messages that a reference page quotes are in the code (#368).

A reference page quotes many messages of the code in a span of code. No other test checks them, so a renamed message made the page wrong with
no failure. The test takes each span of code of the pages in `docs/reference/` that reads as a message: it has a word such as `cannot`, `not`,
`must` or `exits`. The text of a message has places for a value, as in `<host>` or `...`, so the test splits the span at each place, takes the
longest literal part, and looks for it in a text constant of `src/delegate/`.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import delegate

from ..pages import ROOT, outside_the_package

SOURCE = Path(delegate.__file__).parent
PAGES = sorted((ROOT / "docs" / "reference").glob("*.md"))

# A span of code of one line. A fenced block is an example of a file or an output, and a span in it is not a quoted message.
SPAN = re.compile(r"(?<!`)`([^`\n]+)`(?!`)")
# A word that makes a span a message.
VERB = re.compile(r"\b(cannot|can't|not|must|exits?|fails?|refuses|is missing|missing|unknown|invalid)\b")
# The places for a value in a quoted message: a name in angle brackets, a mark of omission, a count or a time as a letter.
# An example value is a place in the same way: a text in quotes, as in `'far'`, a number, or a dotted name. A colon or a semicolon and a space ends a clause.
PLACE = re.compile(r"<[^<>]*>|\.\.\.|…|\bN\b|\{[^{}]*\}|'[^']*'|\"[^\"]*\"|\b\d+\b|[\w-]+(?:\.[\w-]+)+|[:;] ")
# The shortest literal part that proves anything. A shorter part is a common word.
MINIMUM = 12

# A span that reads as a message and is not a message of `src/delegate/`. The key is the span, the value is the reason.
NOT_A_MESSAGE_OF_THE_CODE = {
    "! agent \"<name>\" is a subagent, not a primary agent. Falling back to default agent": "a message of the harness OpenCode",
    "agent \"<name>\" not found. Falling back to default agent": "a message of the harness OpenCode",
    "Agent not found": "a message of the harness OpenCode",
    "config <path>: the file is not valid TOML: Expected '=' after a key in a key/value pair (at line 1, column 6)": "an example: the reason after the colon comes from the TOML parser",
    "the harness command 'claude' cannot start: No such file or directory": "an example: the reason after the colon comes from the operating system",
    "note: halted at the specialist of ticket b: claude: not logged in": "an example of a note with the message of the harness Claude Code",
    "halted · the specialist of ticket d · claude: not logged in · go on with delegate run --resume ab12": "an example of a line with the message of the harness Claude Code",
    "the specialist ended <state> with exit status <n>": "the code makes the role word at run time, as in `{who} ended {state}`",
    "the verifier ended <state> with exit status <n>": "the code makes the role word at run time, as in `{who} ended {state}`",
    "the fix-up specialist ended <state> with exit status <n>": "the code makes the role word at run time, as in `{who} ended {state}`",
    "16:36  \u2717 #a the specialist returned no valid result: report missing: a.specialist.json": "an example line: the code makes the role word at run time",
    "ticket 'a': tier: 'opus' is a model identifier, not a tier name": "the code makes the words `is` and `a model identifier` from the value at run time",
}


def _constants() -> list[str]:
    """Each text constant of `src/delegate/`: the text of a plain string, and each literal part of an f-string."""
    found: list[str] = []
    for path in sorted(SOURCE.rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.append(node.value)
    return found


def _longest_literal(span: str) -> str:
    """The longest part of `span` between two places for a value, with no space at its ends."""
    return max((part.strip(" '\"()") for part in PLACE.split(span)), key=len)


def _messages(pages: list[Path] = PAGES) -> dict[str, str]:
    """Span -> the page that quotes it, for each span of a page that reads as a message."""
    found: dict[str, str] = {}
    for page in pages:
        for span in SPAN.findall(page.read_text()):
            if VERB.search(span) and len(_longest_literal(span)) >= MINIMUM:
                found.setdefault(span, page.name)
    return found


def _missing(messages: dict[str, str]) -> list[str]:
    constants = _constants()
    return [
        f"{page}: {span!r} (looked for {_longest_literal(span)!r})"
        for span, page in messages.items()
        if span not in NOT_A_MESSAGE_OF_THE_CODE and not any(_longest_literal(span) in text for text in constants)
    ]


@outside_the_package
def test_each_message_that_a_reference_page_quotes_is_in_the_code():
    messages = _messages()
    assert len(messages) > 40  # the pages quote about 55 messages: a scan that finds few has lost its pattern
    assert _missing(messages) == []


@outside_the_package
def test_a_changed_quoted_message_of_a_page_fails_the_check(tmp_path):
    page = tmp_path / "runs.md"
    text = (ROOT / "docs" / "reference" / "runs.md").read_text()
    assert "`ssh cannot reach <host>: <line>`" in text
    page.write_text(text.replace("`ssh cannot reach <host>: <line>`", "`ssh could not reach <host>: <line>`"))
    assert _missing(_messages([page])) == ["runs.md: 'ssh could not reach <host>: <line>' (looked for 'ssh could not reach')"]


@outside_the_package
def test_each_entry_of_the_allow_list_is_a_message_that_a_page_quotes_and_the_code_does_not_hold():
    messages = _messages()
    constants = _constants()
    stale = [
        span
        for span in NOT_A_MESSAGE_OF_THE_CODE
        if span not in messages or any(_longest_literal(span) in text for text in constants)
    ]
    assert stale == []
