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
#: The tools of a Claude Code verifier: the read tools, the guarded Bash, and
#: the Write tool, which a guard hook limits to a temp directory.
VERIFIER_TOOLS = [*READ_ONLY_TOOLS, "Write"]
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
    "git grep",
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

#: The options of `env` that the guard removes with `env` (#158). Each one
#: changes only the environment of the command, or the output of a bare `env`.
#: "-u" and "--unset" take the next word, and "-u<name>" and "--unset=<name>"
#: are the same options. Each other option is a deny: -S runs the words of a
#: string, -C changes the directory, -P changes the search path, and -a changes
#: the name of the command.
VERIFIER_ENV_OPTIONS: tuple[str, ...] = ("-i", "-0", "-", "--ignore-environment", "--null")

#: The read commands that the guard does not permit behind `env` (#158). The
#: guard cannot examine the command that they run. The gates need them as the
#: first word of a segment, not behind `env`.
VERIFIER_ENV_HIDDEN_READS: tuple[str, ...] = ("bash -c", "python3")

#: The options of `find` that run a command or write a file (#158). find (and
#: bfs, with -rm) reads each of them as a separate word, with no abbreviation.
VERIFIER_FIND_DENIED_OPTIONS: tuple[str, ...] = (
    "-exec",
    "-execdir",
    "-ok",
    "-okdir",
    "-delete",
    "-rm",
    "-fprint",
    "-fprint0",
    "-fprintf",
    "-fls",
)

#: The options of `rg` that run a command (#158): --pre runs a command on each
#: file that it searches, and --hostname-bin runs a command for the hostname of
#: a hyperlink. rg accepts no abbreviation of a long option.
VERIFIER_RG_DENIED_OPTIONS: tuple[str, ...] = ("--pre", "--hostname-bin")


def _exact_or_prefix(commands: tuple[str, ...]) -> str:
    """A bash case pattern. It matches each command alone or with arguments."""
    return "|".join(f'"{c}"|"{c} "*' for c in commands)


def _temp_path_pattern() -> str:
    """A bash case pattern for a path in a temp directory."""
    return "|".join(f"'{p}'*" if "$" in p else f"{p}*" for p in VERIFIER_TEMP_PATH_PREFIXES)


#: The most directories that a guard reads below one path when it looks for a
#: `.git` entry in that path (#125). A walk stops at this count. A write target
#: that holds such a directory is then a deny, because the guard cannot tell
#: that the directory holds no git tree. A walk of 2000 directories took about
#: 0.4 s on a macOS host (measured 2026-10-20).
TREE_SEARCH_LIMIT = 2000

#: The python3 text that defines the tree search of the guards. It reads the
#: hook input `j`. Each search reads the file system in the python3 process
#: that the guard starts anyway, so it starts no git process (#106, #125). A
#: git tree is a directory that holds a `.git` entry: a directory, or the
#: pointer file of a worktree. Each name that it gives has no `/private` prefix.
#:
#: - `u` removes a `/private` prefix.
#: - `up(P)` gives the git tree that holds the path P: P or the nearest
#:   directory above it with a `.git` entry. It is empty when no such
#:   directory exists.
#: - `cw()` gives the working directory of the hook: the absolute `cwd` of the
#:   input, or else the working directory of the process, with no `..`
#:   component. Claude Code documents `cwd` as a common field of the hook
#:   input: "Current working directory when the hook is invoked"
#:   (code.claude.com/docs/en/hooks.md, read 2026-10-19).
#: - `top()` gives the git toplevel of the working directory of the hook
#:   (#106): `up` of `cw()`.
#: - `dn(h,b)` walks the directory b, and gives the first git tree below it
#:   whose name with a "/" starts with the text h. It goes only into a
#:   directory whose name with a "/" starts with h, or starts h. It does not go
#:   into a git tree that it found, and it does not follow a symbolic link. When
#:   it reads TREE_SEARCH_LIMIT directories and finds no such tree, it gives
#:   "+" and b. It is empty when it finds no tree.
#:
#: The text holds no single quote, so a single-quoted shell word holds it as a
#: literal.
TOPLEVEL_FINDER = (
    'u=lambda s: s[8:] if s.startswith("/private/") else s; '
    'up=lambda P: next((u(str(x)) for x in (P,*P.parents) if (x/".git").exists()),""); '
    'w=j.get("cwd"); '
    'cw=lambda: os.path.normpath(w if isinstance(w,str) and w.startswith("/") else os.getcwd()); '
    "top=lambda: up(pathlib.Path(cw())); "
    "dn=lambda h,b: next((r for k,(D,ds,fs) in enumerate(os.walk(b)) for r in ("
    f'"+"+b if k>={TREE_SEARCH_LIMIT} else '
    '(u(D) if (u(D)+"/").startswith(h) else ds.clear()) if ".git" in ds+fs else '
    'ds.__setitem__(slice(None),[x for x in ds if (lambda e: e.startswith(h) or h.startswith(e))(u(os.path.join(D,x))+"/")]),'
    ') if r),"")'
)

#: The python3 text that finds the git trees of the write targets of a
#: command `c` (#125). The Bash guard reads the targets later, in the shell, so
#: this text takes each word that can be a temp write target, and more. It
#: removes each quote and backslash, as the shell check does, and divides the
#: text at each space. It also divides each word at each separator and
#: redirection sign. In each word and each part, each text from a temp prefix
#: (`/tmp/`, `/var/folders/` or `$TMPDIR`) to the end is a path. A path gets the
#: value of $TMPDIR (`t`) in place of a leading `$TMPDIR`, loses a `/private`
#: prefix, and each `//` and `/./` in it becomes one `/`, as in
#: IN_TOPLEVEL_CHECK. A path with a pattern or an expansion character (`*`,
#: `?`, `[`, `{` or `$`) counts only up to the first such character. The set
#: `V` holds, for each path, the git tree that holds it (`up`), and the first
#: git tree that it holds (`dn`). An extra path is no risk: each name in `V` is
#: a real git tree, or a directory that the walk could not search.
TARGET_TREE_FINDER = (
    's=(c if isinstance(c,str) else "").replace(chr(34),"").replace(chr(39),"").replace(chr(92),""); '
    'K=("/tmp/","/var/folders/","$TMPDIR"); '
    'X={x for z in s.split() for p in (z,*re.split("[;&|<>()`]",z)) for i in range(len(p)) if p.startswith(K,i) '
    'for x in (re.sub("/(?:[.]/|/)+","/",u(t+p[i+7:] if p.startswith("$TMPDIR",i) else p[i:])),) if x.startswith("/")}; '
    'G=lambda x: min([i for i in map(x.find,"*?[{$") if i>=0] or [-1]); '
    'A=lambda x,g: (pathlib.Path(x[:g]),x[:g],x[:x[:g].rfind("/")] or "/") if g>=0 '
    'else (lambda P: (P,str(P).rstrip("/")+"/",str(P)))(pathlib.Path(x)); '
    "V={v for x in X for P,h,b in (A(x,G(x)),) for v in (up(P),dn(h,b)) if v}"
)

#: The shell text that reads a hook input from stdin for the verifier guard. It
#: prints these lines: the git toplevel of the working directory (`top()`),
#: the value of $TMPDIR without a trailing "/", the working directory
#: (`cw()`) without a "/private" prefix, one line for each git tree of
#: TARGET_TREE_FINDER and of the toplevel, one line with "+" for each directory
#: that a walk could not search, and ":" with the command. The guard then fails
#: closed when the command is empty: no command string, or a key that is
#: absent, or a body that is not JSON, ends python3 with a non-zero status and
#: no output. A line with a newline in it ends python3 in the same way.
HOOK_COMMAND_READER = (
    "python3 -c 'import json,os,pathlib,re,sys; j=json.load(sys.stdin); c=j[\"tool_input\"][\"command\"]; "
    f"{TOPLEVEL_FINDER}; r=top(); "
    "t=os.environ.get(\"TMPDIR\",\"\").rstrip(\"/\"); "
    f"{TARGET_TREE_FINDER}; "
    "L=[r,t,u(cw()),*sorted(V|{r}-{\"\"})]; "
    "(\"\\n\" in \"\".join(L)) and sys.exit(5); "
    "sys.stdout.write(\"\\n\".join(L)+\"\\n:\"+(c if isinstance(c, str) else \"\"))'"
)


def _prefixes(option: str, shortest: int) -> list[str]:
    return [option[:n] for n in range(shortest, len(option) + 1)]


#: The long output option of a GET-only command, and each of its prefixes
#: with at least three characters after "--" (#141). A command built on
#: argparse accepts a unique prefix of a long option, so "--out <path>" is
#: the same option as "--output <path>".
GET_ONLY_OUTPUT_OPTIONS = _prefixes("--output", 5)

#: The shell text that examines the arguments of a GET-only command. It reads
#: "$@", strips quote and backslash characters from each word, and denies a
#: method flag, a body flag and an output flag, in each spelling. A hyphen-led
#: word that is not a double-hyphen option is a short flag or a cluster of
#: short flags (-sX, -XPOST, -dBODY), and it is denied when it holds X, d, f,
#: F or o. An output flag is the one exception: its path must be in a temp
#: directory, by the same rule as the temp-write commands. The output flag is
#: -o, --output, or a prefix of --output in GET_ONLY_OUTPUT_OPTIONS.
GET_ONLY_ARGUMENT_CHECK = (
    "k=0; "
    'for a in "$@"; do '
    'if [ "$k" = 1 ]; then k=0; istmp "$a" || deny "$seg" "An output path is outside a temp directory."; continue; fi; '
    r'''a=${a//\"/}; a=${a//\'/}; a=${a//\\/}; '''
    'case "$a" in '
    f'{"|".join(f"{o}=*" for o in GET_ONLY_OUTPUT_OPTIONS)}) '
    'istmp "${a#*=}" || deny "$seg" "An output path is outside a temp directory." ;; '
    f'{"|".join(GET_ONLY_OUTPUT_OPTIONS)}|-o) k=1 ;; '
    '-o?*) istmp "${a#-o}" || deny "$seg" "An output path is outside a temp directory." ;; '
    "--method|--method=*|--request|--request=*|--data*|--input|--input=*"
    '|--field|--field=*|--raw-field|--raw-field=*) deny "$seg" "This command reads only; $a makes it a write." ;; '
    "--*) ;; "
    '-*[XdfFo]*) deny "$seg" "This command reads only; $a makes it a write or opens an output file." ;; '
    "esac; "
    "done; "
    '[ "$k" = 0 ] || deny "$seg" "An output flag needs a path argument."'
)


#: The long option of `git diff`, `git log` and `git show` that writes a file
#: (#140). Some git versions accept a unique prefix of a long option, so each
#: prefix down to "--ou" is the same option. "--o" is with the run options
#: below: it is also a prefix of --open-files-in-pager.
GIT_OUTPUT_OPTION = "--output"

#: The long option of `git grep` that runs a command on the files that match
#: (#140). "-O" is its short form. Each prefix down to "--o" is the same
#: option: git 2.54 runs the command for "--op=cat".
GIT_PAGER_OPTION = "--open-files-in-pager"

#: The short options of `git grep` that take an argument. In a cluster of short
#: options, git reads the rest of the word after one of them as its argument,
#: so an "O" after it is no option: "-eFOO" is the pattern FOO. An "O" before
#: it is -O: "-iOcat" runs cat. A letter that is not in this text makes the
#: guard read further, so a letter that is missing gives a deny, never a hole.
GIT_GREP_ARGUMENT_SHORT_OPTIONS = "efmABC"


def _git_read_argument_check(grep: bool) -> str:
    """The shell text that examines the arguments of a git read command (#140).

    It reads "$@", and it strips quote and backslash characters from each
    word, as GET_ONLY_ARGUMENT_CHECK does. It denies --output in each form and
    each prefix, unless its path is in a temp directory (`istmp`), by the rule
    of a GET-only output flag. With `grep`, it also denies -O,
    --open-files-in-pager in each form and each prefix, and a cluster of short
    options that holds an "O" before an option that takes an argument. The
    message names the option. A "--" does not end the test: a pathspec that
    looks like an option gives a deny, never a hole.
    """
    output = "|".join(_prefixes(GIT_OUTPUT_OPTION, 4))
    run = "The option $a runs a command on the files that match, so it is not permitted."
    pager = (
        f'{"|".join(["--o", *_prefixes(GIT_PAGER_OPTION, 4)])}) deny "$seg" "{run}" ;; '
        "--*) ;; "
        f'-[!-]*) gw=${{a#-}}; gw=${{gw%%[{GIT_GREP_ARGUMENT_SHORT_OPTIONS}]*}}; '
        f'case "$gw" in *O*) deny "$seg" "{run}" ;; esac ;; '
        if grep
        else ""
    )
    return (
        "k=0; "
        'for a in "$@"; do '
        'if [ "$k" = 1 ]; then k=0; istmp "$a" || deny "$seg" '
        '"The option $go writes a file, so its path must be in a temp directory."; continue; fi; '
        r"""a=${a//\"/}; a=${a//\'/}; a=${a//\\/}; """
        'case "${a%%=*}" in '
        f'{output}) case "$a" in *=*) istmp "${{a#*=}}" || deny "$seg" '
        '"The option $a writes a file, so its path must be in a temp directory." ;; '
        "*) k=1; go=$a ;; esac ;; "
        f"{pager}"
        "esac; "
        "done; "
        '[ "$k" = 0 ] || deny "$seg" "The option $go needs a path argument."'
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


#: The shell function `inwt`. It returns 0 when its argument, a temp path as
#: text, can name a git tree of the array $gt, a path in it, or a directory that
#: holds it (#106, #125). A remove or a move of such a directory changes the
#: tree too. It returns 3 when the path can name a directory of the array $gb,
#: or a directory that holds it: a walk could not search that directory for a
#: git tree. It sets $wr to the tree or the directory. It returns 1 otherwise.
#: It reads the path as the shell will: it removes each quote and backslash,
#: puts the value of $TMPDIR ($td) in place of a leading "$TMPDIR", and removes
#: a "/private" prefix. It then makes each "//" and "/./" one "/". A path that
#: still holds a pattern or an expansion character (*, ?, [, { or $) can name
#: more than one path, so only the text before the first such character
#: counts: the path matches when that text starts the tree or starts with it.
#: Otherwise it removes a trailing "/" or "/.", and compares the full path. It
#: starts no process.
IN_TOPLEVEL_CHECK = " ".join(
    [
        r"""inwt() { wp=${1//\"/}; wp=${wp//\'/}; wp=${wp//\\/};""",
        r"""case "$wp" in '$TMPDIR'*) wp=$td${wp#'$TMPDIR'} ;; esac;""",
        r"""case "$wp" in /private/*) wp=${wp#/private} ;; esac;""",
        r"""while :; do case "$wp" in *//*) wp=${wp%%//*}/${wp#*//} ;; */./*) wp=${wp%%/./*}/${wp#*/./} ;; *) break ;; esac; done;""",
        r"""case "$wp" in *'*'*|*'?'*|*'['*|*'{'*|*'$'*)""",
        r"""wh=${wp%%'*'*}; wh=${wh%%'?'*}; wh=${wh%%'['*}; wh=${wh%%'{'*}; wh=${wh%%'$'*};""",
        r"""for wr in "${gt[@]}"; do case "$wr/" in "$wh"*) return 0 ;; esac; case "$wh" in "$wr"/*) return 0 ;; esac; done;""",
        r"""for wr in "${gb[@]}"; do case "$wr/" in "$wh"*) return 3 ;; esac; case "$wh" in "$wr"/*) return 3 ;; esac; done;""",
        r"""return 1 ;; esac;""",
        r"""while :; do case "$wp" in */.) wp=${wp%/.} ;; */) wp=${wp%/} ;; *) break ;; esac; done;""",
        r"""for wr in "${gt[@]}"; do case "$wp" in "$wr"|"$wr"/*) return 0 ;; esac; case "$wr" in "$wp"/*) return 0 ;; esac; done;""",
        r"""for wr in "${gb[@]}"; do case "$wr" in "$wp"|"$wp"/*) return 3 ;; esac; done; return 1; }""",
    ]
)


#: The shell function `incd`. It returns 0 when its argument, the path of a
#: `cd`, names the git toplevel $wt of the working directory or a directory in
#: it (#121), or is a relative path from the working directory $wd to a temp
#: path (#131). A `cd` writes nothing, so it may go there. It reads the path
#: as the shell will: it removes each quote and backslash. A path that holds a
#: pattern or an expansion character (*, ?, [, { or $), or that starts with
#: "~" or "=", can name a path outside the tree, so it returns 1. A ".."
#: component returns 1. A relative path with no ".." component stays below
#: the working directory. When $wt is not empty, the working directory is in
#: the toplevel, so the path returns 0. When $wt is empty, the path is $wd, a
#: "/" and the path, and it returns 0 only when that is a temp path (`istp`),
#: as the same path in the absolute form does. An absolute path returns 1 when
#: $wt is empty. Otherwise it loses a "/private" prefix, each "//" and "/./"
#: becomes one "/", a trailing "/" or "/." goes, and the path must then be the
#: toplevel or start with the toplevel and a "/". It reads text: it does not
#: resolve a symbolic link. It starts no process.
IN_TOPLEVEL_CD_CHECK = " ".join(
    [
        r"""incd() {""",
        r"""wp=${1//\"/}; wp=${wp//\'/}; wp=${wp//\\/};""",
        r"""case "$wp" in ''|'~'*|'='*|*'$'*|*'*'*|*'?'*|*'['*|*'{'*) return 1 ;; esac;""",
        r"""case "$wp" in ..|../*|*/..|*/../*) return 1 ;; esac;""",
        r"""case "$wp" in /*) ;; *) [ -z "$wt" ] || return 0; [ -n "$wd" ] || return 1; istp "${wd%/}/$wp"; return ;; esac;""",
        r"""[ -n "$wt" ] || return 1;""",
        r"""case "$wp" in /private/*) wp=${wp#/private} ;; esac;""",
        r"""while :; do case "$wp" in *//*) wp=${wp%%//*}/${wp#*//} ;; */./*) wp=${wp%%/./*}/${wp#*/./} ;; *) break ;; esac; done;""",
        r"""while :; do case "$wp" in */.) wp=${wp%/.} ;; */) wp=${wp%/} ;; *) break ;; esac; done;""",
        r"""case "$wp" in "$wt"|"$wt"/*) return 0 ;; esac; return 1; }""",
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

    A segment that starts with "docker compose" loses each "-f <file>" and
    "--file <file>" that comes directly after it, so "docker compose -f
    compose.yml config" is tested as "docker compose config" (#121). The
    subcommand after the options is still tested, so "docker compose -f x.yml
    up" stays a deny.

    A segment that starts with "nix develop -c" or "nix develop <flake-ref> -c"
    (or --command) loses that prefix in the same way, so a gate that runs in a
    flake dev shell is tested as the gate command itself. Only a flake
    reference may come before -c: a flag there can write (--profile writes a
    symlink, --build runs build phases), so a hyphen-led word denies. Without
    -c, nix develop starts an interactive shell, and it stays a deny.

    A segment that starts with "direnv exec <dir>" loses that prefix too, so a
    gate that runs with a repository's .envrc is tested as the gate command.
    "direnv allow" writes direnv's allow list, and it stays a deny.

    A segment that starts with "env" loses "env" and its options
    (VERIFIER_ENV_OPTIONS, and "-u <name>"), and the rest is tested as its own
    command, with every rule (#158). A bare "env", or "env" with only options
    and NAME=value assignments, stays a read. An assignment before a command
    denies, as an assignment prefix without env does: GIT_EXTERNAL_DIFF,
    GIT_CONFIG_* and PATH make a read command run a program. The guard divides
    these words at each space, so a word with a quote, a backslash, an
    expansion or a pattern character denies: it can move the start of the
    command. Another option denies. Behind "env", the commands of
    VERIFIER_ENV_HIDDEN_READS deny.

    After "find", the options of VERIFIER_FIND_DENIED_OPTIONS deny, and after
    "rg", the options of VERIFIER_RG_DENIED_OPTIONS deny: they run a command or write a file
    (#158). "sed -n" is a read only as a line range,
    "sed -n '<start>,<end>p' <path>...": a sed script can run a command (the
    GNU "e" command) or write a file ("w"), and an option can edit a file in
    place ("-i"). The guard does not parse a sed script, so it denies each
    other form.

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

    A "cd" takes one path argument: a temp path, or a directory in the git
    toplevel of the working directory (IN_TOPLEVEL_CD_CHECK, #121). A relative
    path counts from the working directory, so in a copy with no .git entry in
    a temp directory, "cd app" goes to a temp path and is permitted (#131). A
    "cd" writes nothing, and each later segment is tested on its own, so a
    relative write target after the "cd" still fails the temp test. When the
    working directory is in no git tree, the deny message states that, and
    names no toplevel.

    A temp path is a text prefix and a path with no ".." component. The guard
    rejects "..", it does not resolve it, because it reads the command as text
    and a resolution needs the file system.

    The guard fails closed. No python3, no command text, or a shell without the
    bash string operators all end at exit 2, which blocks the call. Under zsh
    the guard first turns on sh emulation, so that zsh divides a segment into
    words as bash does (#113).
    """
    quote = '\'"\'*|"\'"*'  # a leading double quote or a leading single quote
    redirect = "A redirection writes outside a temp directory."
    in_toplevel = (
        "A write target is in the git toplevel [$wr], or holds it. A verifier is read-only on each git tree: "
        "copy the tree to another temp directory and remove the .git entry of the copy in the same command, "
        "then change the copy."
    )
    # The message names the limit of the walk and what to do (#132). A copy of a repository with node_modules holds
    # more directories than the walk reads, so a verifier meets this deny when it removes its own temp copy.
    in_unsearched = (
        f"The walk in the directory [$wr] read {TREE_SEARCH_LIMIT} directories and did not reach the end, "
        "so the guard cannot tell that the write target holds no git tree. The limit keeps each guard call short. "
        "Leave a large temp copy in place, because the system removes temp directories, or first remove the parts "
        f"that hold no git tree, one directory at a time, each with fewer than {TREE_SEARCH_LIMIT} directories."
    )
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
    cd_one_path = "A cd takes one path argument."
    cd_outside = (
        "The path is outside a temp directory and outside the git toplevel $wt of the working directory, "
        "or it holds .., ~ or an expansion character."
    )
    # With no toplevel, the message names no empty toplevel (#131).
    cd_outside_no_tree = (
        "The path is outside a temp directory, or it holds .., ~ or an expansion character. "
        "The working directory [$wd] is in no git tree, so a cd can go only to a temp path."
    )
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
    env_word = (
        "The word $ew holds a quote, a backslash, an expansion or a pattern character. The guard reads the words "
        "before the command of env as text, so it cannot tell where the command starts."
    )
    env_option = (
        "The env option $ew is not permitted. Only -i, -0, -u <name>, - and -- are permitted: another option can run "
        "a string as a command, or change the directory, the search path or the name of the command."
    )
    env_hidden = "After env, bash -c and python3 are not permitted, because the guard cannot examine the command that they run."
    # An assignment can make a read run a program: GIT_EXTERNAL_DIFF=touch makes "git diff" write files, and
    # GIT_CONFIG_* and PATH do the same. The guard denies an assignment prefix without env too.
    env_assignment = (
        "The assignment $ea comes before a command. An assignment can make a read command run a program, "
        "for example GIT_EXTERNAL_DIFF, GIT_CONFIG_* or PATH, so it is not permitted before a command, "
        "with env or without it."
    )
    env_options = "|".join(f"0{o}" for o in VERIFIER_ENV_OPTIONS)
    # env reads its options until the first word that is not an option, or
    # until "--" ($ed). After the options, each word with "=" is an assignment.
    # The first other word starts the command. A loop removes each env, so
    # "env env touch f" is tested as "touch f". $eh is 1 when an env went. $ea
    # holds the first assignment: with no command after it, env only prints.
    env_strip = (
        "eh=0; "
        'while :; do case "$g" in "env "*) eh=1 ;; *) break ;; esac; et=${g#env}; ev=0; ed=0; ea=; '
        'while :; do et=${et#"${et%%[! ]*}"}; ew=${et%% *}; et=${et#"$ew"}; '
        r"""case "$ew" in *\'*|*\"*|*\\*|*\$*|*\**|*\?*|*\[*|*\{*|*\~*) """
        f'deny "$seg" "{env_word}" ;; esac; '
        'if [ "$ev" = 1 ]; then ev=0; continue; fi; '
        'case "$ed$ew" in '
        "0--) ed=1 ;; "
        "?) g=env; break ;; "
        f"{env_options}|0--unset=*|0-u?*) ;; "
        "0-u|0--unset) ev=1 ;; "
        f'0-*) deny "$seg" "{env_option}" ;; '
        '?*=*) [ -n "$ea" ] || ea=$ew ;; '
        f'*) [ -z "$ea" ] || deny "$seg" "{env_assignment}"; g=$ew$et; break ;; '
        "esac; done; done; "
        f'[ "$eh" = 0 ] || case "$g" in {_exact_or_prefix(VERIFIER_ENV_HIDDEN_READS)}) deny "$seg" "{env_hidden}" ;; esac; '
    )
    # A find option and the rg option that run a command or write a file (#158). Each word loses its quotes and
    # backslashes first, as the shell removes them.
    unquote = r"""a=${a//\"/}; a=${a//\'/}; a=${a//\\/}; """
    find_rg_branches = (
        f'"find"|"find "*) for a in "$@"; do {unquote}'
        f'case "$a" in {"|".join(VERIFIER_FIND_DENIED_OPTIONS)}) '
        'deny "$seg" "The find option $a runs a command or writes a file, so it is not permitted." ;; esac; done ;; '
        f'"rg"|"rg "*) for a in "$@"; do {unquote}'
        f'case "$a" in {"|".join(f"{o}|{o}=*" for o in VERIFIER_RG_DENIED_OPTIONS)}) '
        'deny "$seg" "The rg option $a runs a command, so it is not permitted." ;; '
        "esac; done ;; "
    )
    # "sed -n" is a read only as a line range (#158). A bare "sed -n" has no script, so it stays a read of the
    # table. The script is the word after -n: digits, at most one comma,
    # and "p", with "$" as the last address only in single quotes. Each later word is a path: no option, and no
    # quote, backslash, expansion or pattern character, so the shell gives sed the same words as the guard reads.
    sed_only_range = (
        "A sed -n command is permitted only as a line range: sed -n '<start>,<end>p' <path>. "
        "A sed script can run a command or write a file, and a sed option can edit a file in place. "
        "To search for a pattern, use grep."
    )
    sed_branch = (
        '"sed -n "*) shift 2; sw=${1-}; [ "$#" = 0 ] || shift; sq=0; sv=$sw; '
        """case "$sw" in "'"?*"'") sq=1; sv=${sw#?}; sv=${sv%?} ;; '"'?*'"') sv=${sw#?}; sv=${sv%?} ;; esac; """
        f'case "$sv" in *p) ;; *) deny "$seg" "{sed_only_range}" ;; esac; sb=${{sv%p}}; '
        r"""case "$sb" in ''|*[!0-9,\$]*|*,*,*|,*|*,) """
        f'deny "$seg" "{sed_only_range}" ;; esac; '
        r"""case "$sb" in *\$*) [ "$sq" = 1 ] || """
        f'deny "$seg" "{sed_only_range}"; '
        r"""case "$sb" in \$|*,\$) ;; *) """
        f'deny "$seg" "{sed_only_range}" ;; esac ;; esac; '
        'for a in "$@"; do '
        r"""case "$a" in -*|*\'*|*\"*|*\\*|*\$*|*\**|*\?*|*\[*|*\{*|*\~*) """
        f'deny "$seg" "{sed_only_range}" ;; esac; done ;; '
    )
    get_only_branch = f"{_exact_or_prefix(get_only)}) {GET_ONLY_ARGUMENT_CHECK} ;; " if get_only else ""
    git_reads = tuple(c for c in VERIFIER_READ_COMMANDS if c.startswith("git ") and c != "git grep")
    other_reads = tuple(c for c in VERIFIER_READ_COMMANDS if not c.startswith("git "))
    segment = (
        '[ -n "$seg" ] || continue; '
        "set -- $seg; "
        "r=0; "
        'for a in "$@"; do '
        # After a lone sign, a word that starts with a sign is no target.
        f'if [ "$r" = 1 ]; then r=0; case "$a" in \'>\'*) deny "$seg" "{redirect}" ;; esac; '
        f'outs "$a" || deny "$seg" "{redirect}"; continue; fi; '
        f'if [ "$r" = 2 ]; then r=0; isfd "$a" || deny "$seg" "{to_file}"; continue; fi; '
        'case "$a" in '
        # A word with ">&": the text before the first ">&" holds no ">" (so
        # no ">>&"), and the text after it is a descriptor. When the word
        # ends at ">&", the next word is the descriptor.
        "*'>&'*) t=${a%%'>&'*}; v=${a#*'>&'}; "
        f'case "$t" in *\'>\'*) deny "$seg" "{to_file}" ;; esac; '
        f'if [ -z "$v" ]; then r=2; else isfd "$v" || deny "$seg" "{to_file}"; fi ;; '
        "'>'|'>>'|'1>'|'1>>'|'2>'|'2>>'|'&>'|'&>>') r=1 ;; "
        # Each target of the word counts, not only the last: in `>x>/dev/null` the shell writes x too. zsh does not
        # divide the segment into words, so there the word is the whole segment.
        f'*\'>\'*) outs "${{a#*>}}" || deny "$seg" "{redirect}" ;; '
        "esac; "
        "done; "
        f'[ "$r" = 0 ] || deny "$seg" "{no_target}"; '
        'g=$seg; case "$seg" in "git -C "*) t=${seg#git -C }; g="git ${t#* }" ;; esac; '
        'case "$g" in "direnv exec "*) t=${g#direnv exec }; '
        'case "$t" in *" "*) g=${t#* } ;; *) g="direnv exec" ;; esac ;; esac; '
        'case "$g" in "nix develop "*) t=${g#nix develop }; '
        'case "$t" in -*) ;; *) t=${t#* } ;; esac; '
        'case "$t" in "-c "*|"--command "*) g=${t#* } ;; *) g="nix develop" ;; esac ;; esac; '
        f"{env_strip}"
        # A compose file is a read. Remove each "-f <file>" and "--file <file>" after "docker compose", as "-C <path>"
        # after "git". "docker compose -f x.yml" with nothing after it becomes "docker compose", which is no gate.
        'case "$g" in "docker compose "*) t=${g#docker compose }; '
        'while :; do case "$t" in "-f "*|"--file "*) t=${t#* }; case "$t" in *" "*) t=${t#* } ;; *) t= ;; esac ;; '
        '*) break ;; esac; done; g="docker compose${t:+ $t}" ;; esac; '
        # The write tests below read the arguments from $@: take them from the
        # command after the prefix, not from the segment.
        "set -- $g; "
        'case "$g" in '
        f"{get_only_branch}"
        # A git read command with an option that writes a file or runs a command is no read (#140).
        f'"git grep"|"git grep "*) {_git_read_argument_check(grep=True)} ;; '
        f"{_exact_or_prefix(git_reads)}) {_git_read_argument_check(grep=False)} ;; "
        f"{find_rg_branches}"
        f"{sed_branch}"
        f"{_exact_or_prefix(other_reads + prefix_gates)}) ;; "
        f"{_exact_or_prefix(VERIFIER_TEMP_DEST_COMMANDS)}) shift; d=; m=0; q=0; k=0; "
        'for a in "$@"; do '
        'if [ "$k" = 1 ]; then k=0; continue; fi; '
        'case "$a" in '
        f'--t*|--n*|-*t*|-*T*) deny "$seg" "{target_flag}" ;; '
        "-*) continue ;; "
        "*'>&'|'>'|'>>'|'1>'|'1>>'|'2>'|'2>>'|'&>'|'&>>'|'<'|'<<'|'0<') q=1; k=1; continue ;; "
        "*'>'*|*'<'*) q=1; continue ;; "
        "esac; "
        f'[ "$q" = 0 ] || deny "$seg" "{after_redirect}"; '
        "d=$a; m=$((m+1)); "
        "done; "
        '[ -n "$d" ] || deny "$seg" "This command needs a temp path argument."; '
        # A cd writes nothing, so it may go to a temp path or to a directory in the git toplevel of the working
        # directory (#121). bash 3.2 goes to the first of two paths, and zsh reads two paths as a substitution in the
        # working directory, so a cd takes one path only.
        f'case "$g" in cd|"cd "*) [ "$m" = 1 ] || deny "$seg" "{cd_one_path}"; '
        f'istp "$d" || incd "$d" || if [ -n "$wt" ]; then deny "$seg" "{cd_outside}"; '
        f'else deny "$seg" "{cd_outside_no_tree}"; fi ;; '
        '*) istmp "$d" || deny "$seg" "The last path argument is outside a temp directory." ;; esac ;; '
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
            # zsh does not divide an unquoted expansion into words, so without this line it denies each temp write.
            # In sh emulation zsh divides the words as bash and /bin/sh do, and the guard gives their verdicts.
            '[ -z "${ZSH_VERSION-}" ] || emulate sh',
            'deny() { echo "verifier guard: the command segment [$1] is not permitted. $2" >&2; exit 2; }',
            f'istp() {{ p=$1; case "$p" in {quote}) p=${{p#?}};; esac; '
            'case "$p" in ..|../*|*/..|*/../*) return 1;; esac; '
            f'case "$p" in {_temp_path_pattern()}) return 0;; esac; return 1; }}',
            IN_TOPLEVEL_CHECK,
            IN_TOPLEVEL_CD_CHECK,
            # A write target: a temp path that is not in a git tree and holds none (#106, #125). A temp path in a
            # git tree, or that holds one, denies the command at once, with its own reason.
            f'istmp() {{ istp "$1" || return 1; inwt "$1"; case $? in 0) deny "$seg" "{in_toplevel}" ;; '
            f'3) deny "$seg" "{in_unsearched}" ;; esac; return 0; }}',
            # The target of a redirection: a temp path, or exactly /dev/null, which discards the output and writes no
            # file. Only a redirection accepts /dev/null; a write command keeps the temp rule.
            'isout() { [ "$1" = /dev/null ] || istmp "$1"; }',
            # The text after a redirection sign: each piece between two signs is a target. An empty piece between two
            # signs is `>>`; an empty last piece is a sign with no target. The word after a lone sign is checked the
            # same way, so in `> /tmp/y>x` the target x counts too.
            # It uses its own variable names: x holds the command, and the exact-gate test reads it.
            "outs() { ow=$1; while :; do ox=${ow%%>*}; if [ -n \"$ox\" ]; then isout \"$ox\" || return 1; fi; "
            "case \"$ow\" in *'>'*) ow=${ow#*>} ;; *) [ -n \"$ox\" ] || return 1; return 0 ;; esac; done; }",
            # A file descriptor number, or "-", which closes a descriptor.
            "isfd() { case \"$1\" in -) return 0;; ''|*[!0-9]*) return 1;; esac; return 0; }",
            # The reader gives the git toplevel of the working directory ($wt), the value of $TMPDIR ($td), the
            # working directory ($wd), the git trees of the targets ($gt), the directories that a walk could not search
            # ($gb) and the command, in that order. A line goes in an array only when a newline comes after it, so the
            # loop always ends.
            f"hi=$({HOOK_COMMAND_READER} 2>/dev/null)",
            "wt=${hi%%$'\\n'*}; hi=${hi#*$'\\n'}; td=${hi%%$'\\n'*}; hi=${hi#*$'\\n'}; "
            "wd=${hi%%$'\\n'*}; hi=${hi#*$'\\n'}; gt=(); gb=()",
            "while :; do case \"$hi\" in /*$'\\n'*) gt+=(\"${hi%%$'\\n'*}\") ;; "
            "+/*$'\\n'*) wr=${hi%%$'\\n'*}; gb+=(\"${wr#+}\") ;; *) break ;; esac; hi=${hi#*$'\\n'}; done; cmd=$hi",
            'case "$cmd" in :*) cmd=${cmd#:} ;; *) cmd= ;; esac',
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


# --- The verifier's guarded Write -------------------------------------------
#
# The verifier writes its verdict to a file in a temp directory. A Bash write of
# a verdict often fails: the Bash guard refuses a heredoc, and each backtick and
# each $( of a command, also inside quotes, and a verdict names code. So the
# verifier also has the Write tool, and this guard limits it to a temp
# directory (#98).

#: The marker that makes the Write guard identifiable in a rendered file. It is
#: not a substring of VERIFIER_GUARD_MARKER, and the reverse is also true.
VERIFIER_WRITE_GUARD_MARKER = "# agent-definitions:verifier-write-guard"

#: The fixed temp prefixes of the Write guard: the prefixes of the Bash guard
#: without the variable. The Write tool does not expand a variable, so the guard
#: reads $TMPDIR from its own environment instead.
VERIFIER_WRITE_PATH_PREFIXES: tuple[str, ...] = tuple(p for p in VERIFIER_TEMP_PATH_PREFIXES if "$" not in p)


def _verifier_write_hook() -> str:
    """Build the verifier's Write guard as one shell line.

    The hook reads `tool_input.file_path` with python3. It permits the Write
    only when the path is absolute, has no ".." component, and starts with a
    fixed temp prefix or with the value of $TMPDIR in the environment of the
    hook. A $TMPDIR that is not absolute, or that is "/", adds no prefix. The
    guard reads the path as text, as the Bash guard does: it does not resolve a
    symbolic link.

    python3 prints the reason of a deny and exits 3. Any other status that is
    not 0 (no python3, a body that is not JSON, no file path, a file path that
    is not text) ends at exit 2 with the fail-closed message.
    """
    # The script holds no single quote, so a single-quoted shell word holds it
    # as a literal. The text of the path reaches the shell only as the value of
    # $r, inside double quotes, so the shell does not read it as a command.
    prefixes = ",".join(f'"{p}"' for p in VERIFIER_WRITE_PATH_PREFIXES)
    script = (
        "import json,os,pathlib,sys; "
        "j=json.load(sys.stdin); "
        'p=j["tool_input"]["file_path"]; '
        "isinstance(p,str) or sys.exit(4); "
        't=os.environ.get("TMPDIR","").rstrip("/"); '
        f'q=({prefixes})+((t+"/",) if t.startswith("/") else ()); '
        'ok=p.startswith("/") and ".." not in p.split("/") and p.startswith(q); '
        'm="" if ok else "the file path ["+p+"] is not permitted. '
        'A verifier writes only to an absolute path in a temp directory, with no .. component."; '
        # Only a temp path needs the tree search. The Write tool expands no
        # pattern and no variable, so the path is compared as it is. The path
        # must not be in the git toplevel of the working directory (#106) or in
        # the git tree above the path, and it must hold no git tree (#125).
        f"{TOPLEVEL_FINDER}; "
        'n=u(os.path.normpath(p)) if ok else ""; '
        'r=next((x for x in (top(),up(pathlib.Path(n))) if x and (n==x or n.startswith(x+"/") or x.startswith(n+"/"))),"") '
        'if ok else ""; '
        'd=dn(n+"/",n) if ok and not r else ""; '
        '(r or d[:1]=="/") and (m:="the file path ["+p+"] is in the git toplevel ["+(r or d)+"], or holds it. '
        'A verifier is read-only on each git tree: write to another temp directory."); '
        'd[:1]=="+" and (m:="the file path ["+p+"] is not permitted. The walk in ["+d[1:]+"] read '
        f'{TREE_SEARCH_LIMIT} directories and did not reach the end, so the guard cannot tell that the file path '
        'holds no git tree. The limit keeps each guard call short. Write to another temp directory."); '
        "sys.stdout.write(m); "
        "sys.exit(3 if m else 0)"
    )
    return (
        f"r=$(python3 -c '{script}' 2>/dev/null); "
        "case $? in 0) exit 0 ;; "
        '3) echo "verifier write guard: $r" >&2; exit 2 ;; '
        "*) echo 'verifier write guard: no file path, or python3 is absent. The guard fails closed.' >&2; exit 2 ;; "
        f"esac {VERIFIER_WRITE_GUARD_MARKER}"
    )


VERIFIER_WRITE_HOOK = _verifier_write_hook()

#: The OpenCode `edit` map of a verifier. OpenCode matches an `edit` rule
#: against the path relative to the worktree (packages/opencode/src/tool/write.ts
#: @ tag v1.18.31), so a path in the project never starts with "../". The one
#: allow therefore opens only a path outside the project, and the
#: `external_directory` map below limits that path to a temp directory.
VERIFIER_OPENCODE_EDIT: dict[str, str] = {"*": "deny", "../*": "allow"}


def verifier_external_directory_permission() -> dict[str, str]:
    """The OpenCode `external_directory` map of a verifier: deny first, then each fixed temp prefix.

    OpenCode asks this permission for a path outside the project, with the
    absolute parent directory and "/*" as the target
    (packages/opencode/src/tool/external-directory.ts @ tag v1.18.31). An
    OpenCode pattern holds no environment variable, so $TMPDIR is not in the map.
    """
    perm: dict[str, str] = {"*": "deny"}
    for prefix in VERIFIER_WRITE_PATH_PREFIXES:
        perm[f"{prefix}*"] = "allow"
    return perm


#: Read commands that the OpenCode verifier map leaves out. A verifier needs
#: echo and printf only to write, and the OpenCode map cannot deny each
#: redirection: see VERIFIER_OPENCODE_WRITE_DENIES (#123). `git grep` runs a
#: command with -O and --open-files-in-pager (#140). A glob reads the text of
#: the command, and the shell makes the option from other text: "-iOcat" and
#: "-'O'cat" are -O, "-eFOO" is not, and "--op" is the long option. A deny glob
#: wide enough for each form also denies most searches, so the map has no
#: `git grep` rule. The OpenCode verifier has its grep tool. `env` runs the
#: command after its options and assignments (#158), and a rule "env *" also
#: matches "env touch <file>". A glob cannot examine the words after env, so
#: the map has no `env` rule.
VERIFIER_OPENCODE_LEFT_OUT: tuple[str, ...] = ("git grep", "echo", "printf", "env")

#: The deny rules that close the OpenCode verifier map (#123). They come after
#: the read allows, because OpenCode keeps the last matching rule
#: (`evaluate` in packages/core/src/permission.ts @ tag v1.15.10, a `findLast`,
#: read 2026-10-20). OpenCode checks each simple command of the line on its
#: own, with the text of a redirection that follows that command
#: (`source` in packages/opencode/src/tool/shell.ts @ tag v1.15.10). So "*>*"
#: denies "echo x > /abs/x", "cat a >> /abs/b", "ls &> f", and also
#: "2>/dev/null" and "2>&1": a glob cannot tell a redirection to a file from
#: one to a file descriptor. The two "tee" rules stop a pipe to tee where the
#: whole line is one pattern. OpenCode gives the glob no ">" of a redirection
#: after a group, a subshell, a loop or the last command of a pipeline, so no
#: rule of the map can deny those forms.
VERIFIER_OPENCODE_WRITE_DENIES: tuple[str, ...] = ("*>*", "*| tee *", "*|tee *")


def verifier_bash_permission(gates: tuple[str, ...] = ()) -> dict[str, str]:
    """The OpenCode permission.bash map for a verifier.

    The catch-all deny is the first key, because OpenCode keeps the last
    matching rule. Each read command becomes "<command> *": OpenCode treats a
    trailing " *" as optional, so one pattern covers the bare command and the
    command with arguments, and it does not match a longer command name. The
    write denies come last, so they win over the read allows.

    This map is smaller than the Claude Code allow list on purpose. An OpenCode
    pattern cannot examine a path argument or an option, so the temp-write
    commands and the GET-only commands stay out, and so do the commands of
    VERIFIER_OPENCODE_LEFT_OUT.
    """
    perm: dict[str, str] = {"*": "deny"}
    # An exact gate stays out: the map is a glob, and a glob cannot hold a
    # quote or a substitution as text that only matches itself.
    plain_gates = tuple(g for g in gates if GATE_COMMAND.fullmatch(g))
    reads = tuple(c for c in VERIFIER_READ_COMMANDS if c not in VERIFIER_OPENCODE_LEFT_OUT)
    for command in reads + plain_gates:
        perm[f"{command} *"] = "allow"
    for pattern in VERIFIER_OPENCODE_WRITE_DENIES:
        perm[pattern] = "deny"
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
            fm["tools"] = ", ".join(VERIFIER_TOOLS)
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
        pre_tool_use = [{"matcher": "Bash", "hooks": [{"type": "command", "command": guard}]}]
        if read_only:
            # The verifier writes its verdict with Write, and this guard keeps
            # every Write in a temp directory.
            pre_tool_use.append({"matcher": "Write", "hooks": [{"type": "command", "command": VERIFIER_WRITE_HOOK}]})
        fm["hooks"] = {"PreToolUse": pre_tool_use}
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
            # The write tool for the verdict: a path outside the project, and
            # that path only in a temp directory. Both maps open with their
            # own catch-all deny.
            perm["edit"] = dict(VERIFIER_OPENCODE_EDIT)
            perm["external_directory"] = verifier_external_directory_permission()
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
