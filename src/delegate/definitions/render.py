"""Render one declaration into complete agent files per harness.

No harness composes agent files, so every file is complete. A pair emits a
specialist and a verifier; the verifier shares the domain, the skills, and the
references, and differs in a read-only posture and the verifier tier.
"""

from __future__ import annotations

from importlib import resources
from string import Template

import yaml

from .declaration import GATE_COMMAND, Agent
from ..shared.tiers import Tiers

READ_ONLY_TOOLS = ["Read", "Grep", "Glob", "Bash"]
OPENCODE_READ_TOOLS = ["read", "grep", "glob", "list"]

# --- The verifier's guarded Bash --------------------------------------------
#
# A verifier with no Bash cannot run the gates, so the coordinator runs them
# instead. The verifier keeps Bash and loses every command that writes. The
# tables below are the single source for three artifacts: the Claude Code hook,
# the OpenCode permission map, and the table in SKILL.md.

#: The marker that makes the guard identifiable in a rendered file. The hook
#: text changes with the tables; the marker does not.
VERIFIER_GUARD_MARKER = "# agent-definitions:verifier-guard"

#: Commands the verifier may run. Each entry matches the command alone or the
#: command with arguments, and never a longer command name: "ls" does not match
#: "lsof". The list holds only what every verifier needs. A build tool or a
#: tracker CLI is one repository's gate: its declaration names it in
#: gateCommands or getOnlyCommands.
VERIFIER_READ_COMMANDS: tuple[str, ...] = (
    "git diff",
    "git log",
    "git show",
    "git status",
    "git ls-files",
    "git ls-tree",
    "git rev-parse",
    "git merge-base",
    "git worktree list",
    "git branch --show-current",
    "ls",
    "cat",
    "head",
    "tail",
    "sed -n",
    "grep",
    "rg",
    "find",
    "wc",
    "diff",
    "jq",
    "shasum",
    "stat",
    "readlink",
    "file",
    "which",
    "env",
    "pwd",
    "echo",
    "printf",
    "date",
    "mktemp",
    "python3",
    "bash -c",
)

#: Commands that write. The guard permits one only when every path argument is
#: in a temp directory.
VERIFIER_TEMP_WRITE_COMMANDS: tuple[str, ...] = ("mkdir", "mv", "rm", "tee", "touch")

#: Commands the guard permits when the LAST path argument is in a temp
#: directory. The earlier path arguments are sources, and a read of any path is
#: a read. The verifier method needs this: it copies the worktree into a temp
#: directory to prove a check red. For "cp" the last path is the destination;
#: for "cd" it is the new working directory. "mv" stays out of this group: it
#: removes its source.
VERIFIER_TEMP_DEST_COMMANDS: tuple[str, ...] = ("cp", "cd")

VERIFIER_TEMP_PATH_PREFIXES: tuple[str, ...] = ("/tmp/", "/private/tmp/", "$TMPDIR", "/var/folders/")


def _exact_or_prefix(commands: tuple[str, ...]) -> str:
    """A bash case pattern. It matches each command alone or with arguments."""
    return "|".join(f'"{c}"|"{c} "*' for c in commands)


def _temp_path_pattern() -> str:
    """A bash case pattern for a path in a temp directory."""
    return "|".join(f"'{p}'*" if "$" in p else f"{p}*" for p in VERIFIER_TEMP_PATH_PREFIXES)


#: The shell text that prints the command of a hook input from stdin. It prints
#: nothing when the input holds no command string, and the verifier guard then
#: fails closed. A key that is absent, or a body that is not JSON, ends python3
#: with a non-zero status and no output, which has the same result.
HOOK_COMMAND_READER = (
    "python3 -c 'import json,sys; c=json.load(sys.stdin)[\"tool_input\"][\"command\"]; "
    "sys.stdout.write(c if isinstance(c, str) else \"\")'"
)


#: The shell text that examines the arguments of a GET-only command. It reads
#: "$@", strips quote and backslash characters from each word, and denies a
#: method flag, a body flag and an output flag, in each spelling. A hyphen-led
#: word that is not a double-hyphen option is a short flag or a cluster of
#: short flags (-sX, -XPOST, -dBODY), and it is denied when it holds X, d, f,
#: F or o. An output flag is the one exception: its path must be in a temp
#: directory, by the same rule as the temp-write commands.
GET_ONLY_ARGUMENT_CHECK = (
    "k=0; "
    'for a in "$@"; do '
    'if [ "$k" = 1 ]; then k=0; istmp "$a" || deny "$seg" "An output path is outside a temp directory."; continue; fi; '
    r'''a=${a//\"/}; a=${a//\'/}; a=${a//\\/}; '''
    'case "$a" in '
    '--output=*) istmp "${a#--output=}" || deny "$seg" "An output path is outside a temp directory." ;; '
    "--output|-o) k=1 ;; "
    '-o?*) istmp "${a#-o}" || deny "$seg" "An output path is outside a temp directory." ;; '
    "--method|--method=*|--request|--request=*|--data*|--input|--input=*"
    '|--field|--field=*|--raw-field|--raw-field=*) deny "$seg" "This command reads only; $a makes it a write." ;; '
    "--*) ;; "
    '-*[XdfFo]*) deny "$seg" "This command reads only; $a makes it a write or opens an output file." ;; '
    "esac; "
    "done; "
    '[ "$k" = 0 ] || deny "$seg" "An output flag needs a path argument."'
)


#: The shell text that divides the command text "$s" into the array "segs". It
#: reads "$s" one character at a time, and it divides at &&, ||, ;, |, & and a
#: newline only outside quotes. An "&" next to a redirection sign (2>&1, >&2,
#: <&3, &>file, &>>file) is part of the redirection, and it does not divide.
#: A redirection sign that a backslash escapes is a literal, so the "&" after
#: it divides (echo \>& rm). $l is 1 when the last character in the segment
#: is an escaped one, and $b holds that value for the character before $c.
#: It reads quotes as bash and zsh do: a single quote ends at the next single
#: quote; a double quote ends at the next double quote that no backslash
#: escapes; outside quotes, a backslash makes the next
#: character a literal. An unquoted "#" starts a comment, and a quote or a
#: backslash in a comment does nothing until the next newline.
#:
#: Four forms read quotes in their own way: a command substitution ($( and the
#: backtick), a parameter expansion with braces, an ANSI-C quote ($') and a
#: here-document (<<). The divider does not parse them. When the text holds one
#: of them, it divides at each separator, also inside quotes, as the guard did
#: before. Each of these differences gives more segments, never fewer, so a
#: separator that the shell sees always divides the command. The test for the
#: four forms reads "$u", the command text without each backslash-newline,
#: because the shell removes a backslash-newline before it reads a form
#: ($\<newline>( is a command substitution).
#:
#: At the end, $q holds "s" or "d" when a quote is not closed. The guard then
#: cannot read the command, and it fails closed.
COMMAND_DIVIDER = " ".join(
    [
        r"""q=; case "$u" in *'`'*|*'$('*|*'${'*|*"\$'"*|*'<<'*) q=L ;; esac;""",
        r"""segs=(); o=; e=0; l=; i=0; n=${#s};""",
        r"""while [ "$i" -lt "$n" ]; do c=${s:$i:1}; i=$((i+1));""",
        r"""if [ "$e" = 1 ]; then e=0; l=1; o+=$c; continue; fi; b=$l; l=;""",
        r"""case "$q" in""",
        r"""s) [ "$c" = "'" ] && q=; o+=$c; continue ;;""",
        r"""d) case "$c" in '\') e=1 ;; '"') q= ;; esac; o+=$c; continue ;;""",
        r"""esac;""",
        r"""case "$c" in""",
        r"""$'\n'|';'|'|') segs+=("$o"); o=; [ "$c$q" = $'\n'c ] && q=; continue ;;""",
        r"""'&') case "${b:-${o#"${o%?}"}}${s:$i:1}" in '>'*|'<'*|*'>') ;;""",
        r"""*) [ "${s:$i:1}" = '&' ] && i=$((i+1)); segs+=("$o"); o=; continue ;; esac ;;""",
        r"""esac;""",
        r"""[ -n "$q" ] || case "$c" in "'") q=s ;; '"') q=d ;; '\') e=1 ;; '#') q=c ;; esac;""",
        r"""o+=$c;""",
        r"""done;""",
        r"""segs+=("$o")""",
    ]
)


def _verifier_bash_hook(gates: tuple[str, ...] = (), get_only: tuple[str, ...] = ()) -> str:
    """Build the verifier's Bash guard as one bash line from the tables above.

    The guard divides the command at &&, ||, ;, |, & and a newline outside
    quotes (COMMAND_DIVIDER), and it examines each segment. One segment that is
    not on the allow list denies the full command, and the message names that
    segment. A quote that is not closed denies the full command.

    A substitution runs a command that the guard cannot examine: a command
    substitution ($(...) and the backtick) and a process substitution (<(...),
    >(...), and the zsh =(...)). When all segments pass and the command still
    holds one of these forms, the guard denies the full command. It reads the
    command as text for this test, so the form also denies inside single
    quotes. The text has no backslash-newline for this test: the shell removes
    each one first, so "$\\<newline>(" is a command substitution too. The one
    exception is an exact gate of the declaration (a gate with a substitution,
    see declaration.EXACT_GATE_COMMAND): the guard permits it
    when the full command is that gate, character for character, with only
    spaces before and after it.

    A segment that starts with "git -C <path>" loses that prefix before the
    test, so a read of a repository by path is the same read as a read of the
    current repository. A verifier with no worktree of its own reads a worktree
    by its path. The subcommand after the path is still tested, so
    "git -C <path> push" stays a deny.

    A segment that starts with "nix develop -c" or "nix develop <flake-ref> -c"
    (or --command) loses that prefix in the same way, so a gate that runs in a
    flake dev shell is tested as the gate command itself. Only a flake
    reference may come before -c: a flag there can write (--profile writes a
    symlink, --build runs build phases), so a hyphen-led word denies. Without
    -c, nix develop starts an interactive shell, and it stays a deny.

    A segment that starts with "direnv exec <dir>" loses that prefix too, so a
    gate that runs with a repository's .envrc is tested as the gate command.
    "direnv allow" writes direnv's allow list, and it stays a deny.

    `gates` are the repository's gate commands from its declaration. The
    gates differ in each repository, so the global list holds none of them
    (ADR 0004). Each plain gate is permitted like a read command, in that
    verifier only. Each exact gate is permitted only as the full command.

    `get_only` are the declaration's commands that read only while they stay a
    GET, such as a tracker API client. Each one is permitted alone or with
    arguments, in that verifier only. The guard examines each argument and
    denies a method flag, a body flag, and an output flag whose path is not in
    a temp directory (GET_ONLY_ARGUMENT_CHECK). With no such command the
    branch is absent.

    A temp path is a text prefix and a path with no ".." component. The guard
    rejects "..", it does not resolve it, because it reads the command as text
    and a resolution needs the file system.

    The guard fails closed. No python3, no command text, or a shell without the
    bash string operators all end at exit 2, which blocks the call.
    """
    quote = '\'"\'*|"\'"*'  # a leading double quote or a leading single quote
    redirect = "A redirection writes outside a temp directory."
    # The shell removes a redirection before it runs the command, so a path
    # that comes after one is still a path argument. A scan that stopped at the
    # redirection read the source as the destination: "cp /tmp/a >/tmp/log
    # /Users/x" writes to /Users/x.
    after_redirect = "A path argument comes after a redirection."
    # A redirection with "&" duplicates a file descriptor only when the word
    # after the "&" is a descriptor number or "-". With another word, bash,
    # /bin/sh and zsh send the output to the file of that name (">&out",
    # ">& out", "1>&out"), and zsh does so also after "2>". In zsh, ">>&"
    # always appends to a file, also with a number (">>&1" writes the file
    # "1"). A word that holds ">&" therefore needs a descriptor after the
    # first ">&" and no ">" before it.
    to_file = (
        "A redirection with & writes to a file when the word after the & is not a file descriptor number or -, "
        "and >>& always writes to a file in zsh. To search for the text, write a . in place of the >. "
        "When 2>&1 is the last word inside quotes, put a space before the closing quote."
    )
    # The divider cuts at each "|", but in ">|" and ">&|" the "|" is part of
    # the redirection, and the word after it is the file. So a segment that
    # ends at a redirection sign gives its target to the next segment, where
    # the guard reads it as a command ("ls >|cat" writes the file "cat").
    no_target = "A redirection sign has no target in this segment, so the next word can be a file that the shell writes."
    # GNU cp takes its destination from -t or --target-directory, and a word
    # that starts with a hyphen is otherwise dropped as a flag. Two literal
    # prefixes do not hold the class: GNU short options cluster, so "cp -Rt
    # /Users/y /tmp/a" still takes the next word as the destination, and a GNU
    # long option accepts an unambiguous abbreviation such as "--targ". The
    # guard therefore denies every hyphen-led word that holds "t" or "T", and
    # every long option that starts with "--t" or "--n". For "cp" and "cd" the
    # only short option with a lower-case "t" is -t itself, the only long
    # options that start with "--t" are the target-directory pair, and -T with
    # --no-target-directory changes what the last path means.
    target_flag = "A flag that holds t or T can move the destination, so it is not permitted. Give the destination as the last path."
    prefix_gates = tuple(g for g in gates if GATE_COMMAND.fullmatch(g))
    # An exact gate holds a double quote and a $( but never a single quote, so
    # a single-quoted case pattern holds it as a literal.
    exact_gates = tuple(g for g in gates if not GATE_COMMAND.fullmatch(g))
    exact_gate_test = (
        ["case \"$x\" in " + "|".join(f"'{g}'" for g in exact_gates) + ") exit 0 ;; esac"] if exact_gates else []
    )
    substitution = (
        "A substitution runs a command that the guard cannot examine. Only an exact gate of the declaration, "
        "given as the full command, may hold one. To search for the text, put the dollar sign in brackets."
    )
    get_only_branch = f"{_exact_or_prefix(get_only)}) {GET_ONLY_ARGUMENT_CHECK} ;; " if get_only else ""
    segment = (
        '[ -n "$seg" ] || continue; '
        "set -- $seg; "
        "r=0; "
        'for a in "$@"; do '
        f'if [ "$r" = 1 ]; then r=0; istmp "$a" || deny "$seg" "{redirect}"; continue; fi; '
        f'if [ "$r" = 2 ]; then r=0; isfd "$a" || deny "$seg" "{to_file}"; continue; fi; '
        'case "$a" in '
        # A word with ">&": the text before the first ">&" holds no ">" (so
        # no ">>&"), and the text after it is a descriptor. When the word
        # ends at ">&", the next word is the descriptor.
        "*'>&'*) t=${a%%'>&'*}; v=${a#*'>&'}; "
        f'case "$t" in *\'>\'*) deny "$seg" "{to_file}" ;; esac; '
        f'if [ -z "$v" ]; then r=2; else isfd "$v" || deny "$seg" "{to_file}"; fi ;; '
        "'>'|'>>'|'1>'|'1>>'|'2>'|'2>>'|'&>'|'&>>') r=1 ;; "
        f'*\'>\'*) istmp "${{a##*>}}" || deny "$seg" "{redirect}" ;; '
        "esac; "
        "done; "
        f'[ "$r" = 0 ] || deny "$seg" "{no_target}"; '
        'g=$seg; case "$seg" in "git -C "*) t=${seg#git -C }; g="git ${t#* }" ;; esac; '
        'case "$g" in "direnv exec "*) t=${g#direnv exec }; '
        'case "$t" in *" "*) g=${t#* } ;; *) g="direnv exec" ;; esac ;; esac; '
        'case "$g" in "nix develop "*) t=${g#nix develop }; '
        'case "$t" in -*) ;; *) t=${t#* } ;; esac; '
        'case "$t" in "-c "*|"--command "*) g=${t#* } ;; *) g="nix develop" ;; esac ;; esac; '
        # The write tests below read the arguments from $@: take them from the
        # command after the prefix, not from the segment.
        "set -- $g; "
        'case "$g" in '
        f"{get_only_branch}"
        f"{_exact_or_prefix(VERIFIER_READ_COMMANDS + prefix_gates)}) ;; "
        f"{_exact_or_prefix(VERIFIER_TEMP_DEST_COMMANDS)}) shift; d=; q=0; k=0; "
        'for a in "$@"; do '
        'if [ "$k" = 1 ]; then k=0; continue; fi; '
        'case "$a" in '
        f'--t*|--n*|-*t*|-*T*) deny "$seg" "{target_flag}" ;; '
        "-*) continue ;; "
        "*'>&'|'>'|'>>'|'1>'|'1>>'|'2>'|'2>>'|'&>'|'&>>'|'<'|'<<'|'0<') q=1; k=1; continue ;; "
        "*'>'*|*'<'*) q=1; continue ;; "
        "esac; "
        f'[ "$q" = 0 ] || deny "$seg" "{after_redirect}"; '
        "d=$a; "
        "done; "
        '[ -n "$d" ] || deny "$seg" "This command needs a temp path argument."; '
        'istmp "$d" || deny "$seg" "The last path argument is outside a temp directory." ;; '
        f"{_exact_or_prefix(VERIFIER_TEMP_WRITE_COMMANDS)}) shift; n=0; "
        'for a in "$@"; do case "$a" in -*) continue ;; esac; '
        'if istmp "$a"; then n=$((n+1)); else deny "$seg" "A path argument is outside a temp directory."; fi; '
        "done; "
        '[ "$n" -gt 0 ] || deny "$seg" "A write command needs a temp path argument." ;; '
        '*) deny "$seg" "Only read commands and temp-directory writes are permitted." ;; '
        "esac;"
    )
    return "; ".join(
        [
            'deny() { echo "verifier guard: the command segment [$1] is not permitted. $2" >&2; exit 2; }',
            f'istmp() {{ p=$1; case "$p" in {quote}) p=${{p#?}};; esac; '
            'case "$p" in ..|../*|*/..|*/../*) return 1;; esac; '
            f'case "$p" in {_temp_path_pattern()}) return 0;; esac; return 1; }}',
            # A file descriptor number, or "-", which closes a descriptor.
            "isfd() { case \"$1\" in -) return 0;; ''|*[!0-9]*) return 1;; esac; return 0; }",
            f"cmd=$({HOOK_COMMAND_READER} 2>/dev/null)",
            '[ -n "$cmd" ] || { echo "verifier guard: no command text, or python3 is absent. '
            'The guard fails closed." >&2; exit 2; }',
            # A tab becomes a space first: the prefix strips cut at a space,
            # and a tab between words must not hide a flag or a write.
            "s=${cmd//$'\\t'/ }",
            # The text without each backslash-newline, for the tests that look
            # for a substitution and the other forms that the divider does not
            # parse. Only these tests read it: in single quotes and after an
            # escaped backslash the shell keeps the newline, so the divider
            # reads "$s".
            "u=${s//\\\\$'\\n'/}",
            # The full command without the spaces at each end, for the exact
            # gate test and for the substitution message.
            'x=${s#"${s%%[! ]*}"}; x=${x%"${x##*[! ]}"}',
            *exact_gate_test,
            COMMAND_DIVIDER,
            'case "$q" in s|d) echo "verifier guard: a quote is not closed, so the guard cannot read the command. '
            'The guard fails closed." >&2; exit 2 ;; esac',
            "set -f",
            # The divider keeps the spaces at each end of a segment. The prefix
            # tests match from the first word, so remove them.
            'for seg in "${segs[@]}"; do seg=${seg#"${seg%%[! ]*}"}; seg=${seg%"${seg##*[! ]}"}; '
            f"{segment} done",
            "case \"$u\" in *'`'*|*'$('*|*'<('*|*'>('*|*'=('*) "
            f'deny "$x" "{substitution}" ;; esac',
            f"exit 0 {VERIFIER_GUARD_MARKER}",
        ]
    )


VERIFIER_BASH_HOOK = _verifier_bash_hook()


def verifier_bash_permission(gates: tuple[str, ...] = ()) -> dict[str, str]:
    """The OpenCode permission.bash map for a verifier.

    The catch-all deny is the first key, because OpenCode keeps the last
    matching rule. Each read command becomes "<command> *": OpenCode treats a
    trailing " *" as optional, so one pattern covers the bare command and the
    command with arguments, and it does not match a longer command name.

    This map is smaller than the Claude Code allow list on purpose. An OpenCode
    pattern cannot examine a path argument, so the temp-write commands and the
    GET-only commands stay out.
    """
    perm: dict[str, str] = {"*": "deny"}
    # An exact gate stays out: the map is a glob, and a glob cannot hold a
    # quote or a substitution as text that only matches itself.
    plain_gates = tuple(g for g in gates if GATE_COMMAND.fullmatch(g))
    for command in VERIFIER_READ_COMMANDS + plain_gates:
        perm[f"{command} *"] = "allow"
    return perm

# A PreToolUse hook on Bash. Exit 2 blocks the call and returns stderr to the
# agent. A disallowedTools entry with a specifier would remove Bash entirely,
# so the block is a hook. The hook reads the command with python3, which the
# kit already requires, so it needs no jq. The reader exits 3 for a push and 0
# for any other command. Any other status ends at exit 2: the input holds no
# command text, or python3 is absent (status 127). The hook fails closed.
GIT_PUSH_HOOK = (
    r'''python3 -c 'import json,re,sys; c=json.load(sys.stdin)["tool_input"]["command"]; '''
    r'''sys.exit(3 if re.search(r"\bgit(\s+-C\s+\S+)?\s+push\b", c) else 0)' 2>/dev/null; '''
    "case $? in 0) exit 0 ;; "
    "3) echo 'git push is blocked for specialist agents. Report the branch to the coordinator.' >&2; exit 2 ;; "
    "*) echo 'specialist push guard: no command text, or python3 is absent. The guard fails closed.' >&2; exit 2 ;; esac"
)


#: The language rule that opens the Report section, by outputLanguage. The
#: default "none" adds no rule, so the section opens with its content.
OUTPUT_LANGUAGE_RULES: dict[str, str] = {
    "none": "",
    "ste": "Write in ASD-STE100 Simplified Technical English. ",
}

#: The reason that follows the staging rule in the Claude Code specialist when
#: trackedFileBuild is true. The staging rule itself is in every specialist.
TRACKED_FILE_REASON = " An unstaged file is invisible to a flake."


def _template(name: str) -> Template:
    return Template(resources.files(__package__).joinpath("templates", f"{name}.md").read_text())


def _frontmatter(fm: dict) -> str:
    # The width is large because the verifier guard is one long line. A fold
    # inside a quoted scalar puts a line break where the hook has a space, and
    # a reader then cannot copy the hook out of the file.
    return "---\n" + yaml.safe_dump(fm, sort_keys=False, default_flow_style=False, allow_unicode=True, width=100000) + "---\n"


def _bullets(items: list[str], empty: str) -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def _body(template: str, agent: Agent, name: str, twin: str) -> str:
    return _template(template).substitute(
        name=name,
        twin=twin,
        domain=agent.key,
        description=agent.description,
        skills=", ".join(agent.skills) if agent.skills else "none declared",
        references=_bullets(agent.references, "none declared beyond CLAUDE.md and the delegation doc"),
        prompt=agent.prompt or "(no domain notes declared)",
        output_language=OUTPUT_LANGUAGE_RULES[agent.output_language],
        tracked_file_reason=TRACKED_FILE_REASON if agent.tracked_file_build else "",
    )


def _roles(agent: Agent) -> list[tuple[str, bool, str]]:
    """(name, read_only, role) for each file the agent produces."""
    if agent.pair:
        return [
            (f"{agent.key}-specialist", False, "specialist"),
            (f"{agent.key}-verifier", True, "verifier"),
        ]
    return [(agent.key, agent.read_only, "verifier" if agent.read_only else "specialist")]


def render_claude_code(agent: Agent, tiers: Tiers) -> dict[str, str]:
    files: dict[str, str] = {}
    for name, read_only, role in _roles(agent):
        twin = f"{agent.key}-verifier" if role == "specialist" else f"{agent.key}-specialist"
        fm: dict = {"name": name}
        if read_only:
            fm["description"] = (
                f"{agent.key} verifier. {agent.description} Use it to review a diff in this domain. "
                "It is read-only and blind. It returns ACCEPT or REJECT with evidence."
            )
            fm["tools"] = ", ".join(READ_ONLY_TOOLS)
        else:
            fm["description"] = (
                f"{agent.key} specialist. {agent.description} Use it for one implementation task in this domain. "
                "It works in an isolated worktree and never pushes."
            )
        # model is omitted on purpose: the spawn call sets it, and effort
        # follows the model through modelSettings (ADR 0001).
        if agent.effort:
            fm["effort"] = agent.effort
        if agent.skills:
            fm["skills"] = list(agent.skills)
        if agent.max_turns:
            fm["maxTurns"] = agent.max_turns
        # Each half gets a PreToolUse hook on Bash, and the two hooks differ.
        # The specialist keeps a full shell and loses "git push". The verifier
        # keeps only the read commands and the gates: with no shell it cannot
        # run the gates, and with a full shell it can write.
        guard = (
            _verifier_bash_hook(tuple(agent.gate_commands), tuple(agent.get_only_commands))
            if read_only
            else GIT_PUSH_HOOK
        )
        if not read_only:
            fm["isolation"] = "worktree"
        fm["hooks"] = {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": guard}]}]
        }
        files[f"{name}.md"] = _frontmatter(fm) + _body(f"claude-code-{role}", agent, name, twin if agent.pair else "")
    return files


def render_opencode(agent: Agent, tiers: Tiers) -> dict[str, str]:
    files: dict[str, str] = {}
    for name, read_only, role in _roles(agent):
        twin = f"{agent.key}-verifier" if role == "specialist" else f"{agent.key}-specialist"
        tier = "verifier" if read_only else agent.tier
        fm: dict = {
            "description": (
                f"{agent.key} verifier. {agent.description} Read-only, blind review. Returns ACCEPT or REJECT with evidence."
                if read_only
                else f"{agent.key} specialist. {agent.description} One implementation task. Never pushes."
            ),
            "mode": "subagent",
            "model": tiers.model_for(tier, "opencode"),
        }
        if agent.effort and agent.effort in tiers.opencode_variants:
            fm["variant"] = agent.effort
        if agent.max_turns:
            fm["steps"] = agent.max_turns
        if read_only:
            # "*" must be the first key: rules match in order and the last
            # matching rule wins, so the catch-all deny goes first.
            perm: dict = {"*": "deny"}
            for t in OPENCODE_READ_TOOLS:
                perm[t] = "allow"
            # The bash map is a second catch-all deny with the read commands
            # after it. It does not replace the outer deny: without that outer
            # rule every other tool becomes available again.
            perm["bash"] = verifier_bash_permission(tuple(agent.gate_commands))
            fm["permission"] = perm
        else:
            # Catch-all first here too: OpenCode keeps the last matching rule,
            # so a deny placed before "*" is shadowed and the agent can push.
            # Two denies, because an OpenCode pattern is an anchored glob:
            # "git push*" becomes "^git push.*$" and does not cover the
            # "git -C <path> push" form that the Claude Code hook stops. The map
            # does not add a wider "*git push*" pattern (ADR 0002).
            fm["permission"] = {
                "bash": {"*": "allow", "git push*": "deny", "git -C * push*": "deny"}
            }
        files[f"{name}.md"] = _frontmatter(fm) + _body(f"opencode-{role}", agent, name, twin if agent.pair else "")
    return files


def render(agents: list[Agent], tiers: Tiers) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {"claude-code": {}, "opencode": {}}
    for agent in agents:
        for fname, content in render_claude_code(agent, tiers).items():
            if fname in out["claude-code"]:
                raise ValueError(f"duplicate agent file {fname}")
            out["claude-code"][fname] = content
        for fname, content in render_opencode(agent, tiers).items():
            out["opencode"][fname] = content
    return out
