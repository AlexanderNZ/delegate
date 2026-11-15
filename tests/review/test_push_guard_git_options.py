"""The push guard of a rendered specialist refuses a push after the global options of git, and a git command that
turns off the `pre-push` hook (#359).

A `-c` on the command line wins over each configuration file, so `git -c core.hooksPath=/dev/null push` runs no
`pre-push` hook. The text guard of each harness is the layer that sees the command. Each case renders the example
declaration with `agent-definitions render`, and runs the guard of the rendered file: the Claude Code hook as a bash
process, and the OpenCode bash map through the model of the OpenCode rules in the validator.

A review of the first guard of #359 found that a substitution or a variable before the subcommand hid the push from
the reader of words (`git -C $(pwd) -c core.hooksPath=/dev/null push`), and that the new guard allowed forms that the
guard before #359 refused. The guard now refuses a git command with a substitution, a variable or a backtick before its
subcommand when its text names a push, and it keeps the pattern of the guard before #359 as a floor.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from delegate.adapters.git import GitVersionControl
from delegate.cli.agent_definitions import main
from delegate.definitions.validate import opencode_bash_action

from ..support import git, make_repo

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "java-spring.toml"

#: A push after a run of global options of git. Each form passed the guard of each harness before #359.
PUSH_AFTER_OPTIONS = (
    "git -c x=y push",
    "git -c x=y push origin main",
    "git --no-pager push",
    "git -C 'a b' push",
    'git -C "a b" push origin main',
    "git -C a -c x=y push",
    "git -C /repo/worktree --no-pager -c user.name=x push --force",
)

#: A git command that sets `core.hooksPath` on its command line or with `--config-env`, so git runs no `pre-push` hook.
HOOKS_OFF = (
    "git -c core.hooksPath=/dev/null push origin feature",
    "git -c core.hookspath=/dev/null push origin feature",
    "git -C /repo/worktree -c core.hooksPath=/dev/null push",
    "git -c 'core.hooksPath=/dev/null' status",
    "git --config-env=core.hooksPath=HOOKS push",
    "git --config-env=core.hookspath=HOOKS status",
)

#: Only the Claude Code hook reads the command as words. An OpenCode pattern is a glob that is case-sensitive.
CLAUDE_CODE_ONLY = (
    "git --git-dir /repo/.git push",
    "git --git-dir=/repo/.git push",
    "git 'push' origin main",
    "git -c CORE.HOOKSPATH=/dev/null status",
    "git --config-env core.hooksPath=HOOKS status",
    "git status && git -c x=y push",
    "git -C a \\\n  push",
)

#: Read commands that name a push as an argument. The OpenCode glob `git -* push*` denies them; the hook reads the words.
CLAUDE_CODE_PASS = ("git --no-pager log --grep push", "git -C /repo/worktree log --grep push")

#: The read commands of git, and commands that only name a push or the hooks setting.
STILL_PASS = (
    "git -C /repo/worktree log",
    "git -C /repo/worktree log --oneline -5",
    "git -c color.ui=never diff",
    "git -c color.ui=never diff HEAD~1 -- src",
    "git status",
    "git config --get core.hooksPath",
    'git commit -m "Refuse a push after the options of git"',
    "grep -rn hooksPath src",
)

#: A substitution, a variable or a backtick before the subcommand. The reader of words did not see the options or the
#: push after it, and each form pushed past the `pre-push` hook to a bare remote in the review of #359.
DYNAMIC_BEFORE_THE_SUBCOMMAND = (
    "git -C $(pwd) -c core.hooksPath=/dev/null push origin HEAD:refs/heads/stolen",
    "git -c x=$(echo y) -c core.hooksPath=/dev/null push origin HEAD:refs/heads/stolen",
    "git -C x$(true) -c core.hooksPath=/dev/null push",
    "git -C <(true) -c core.hooksPath=/dev/null push",
    "git -C $(pwd) push",
    "git -C x$(true) push",
    "git -C <(true) push",
    "git -C `pwd` push",
    "git $(echo) push",
    "git push$NOPE origin HEAD",
    'git push"$NOPE" origin HEAD',
    "git push${NOPE} origin",
    "echo `git push`",
    "echo `git -C x push`",
)

#: The other git commands that push: they run no `pre-push` hook.
OTHER_PUSH_COMMANDS = (
    "git send-pack origin main",
    "git -C /repo/worktree send-pack origin HEAD",
    "git http-push https://example.org/repo.git main",
    "git -c x=y http-push https://example.org/repo.git main",
)

#: A substitution or a variable before the subcommand of a read: the text names no push, so the read passes.
DYNAMIC_READS_PASS = (
    'git -C "$REPO" log --oneline -5',
    'git -C "$REPO" log',
    "git -C $(pwd) status",
    "git log --format=%H $(cat ref.txt)",
    "git status",
    "git diff --stat",
    "git log --grep push",
    "git -c color.ui=never diff",
    "git status 2>&1",
    "git log --oneline >log.txt",
    "git 2>/dev/null status",
    "git show HEAD:docs/push.md",
)

#: The forms of the second review of #359 (B1 to B4). Each passed the reader of words and pushed past the `pre-push` hook.
#: B1: a redirection between git and its subcommand. The reader steps over each redirection token.
REDIRECTIONS = (
    "git 2>&1 push --no-verify origin HEAD:refs/heads/x",
    "git >/dev/null push --no-verify origin HEAD:refs/heads/x",
    "git -C . 2>/dev/null push --no-verify origin HEAD:refs/heads/x",
    "git 2>/dev/null -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "git >/dev/null -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "git </dev/null -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "git 2> /dev/null push origin HEAD:refs/heads/x",
    "git &>/dev/null push origin HEAD:refs/heads/x",
)

#: B2: a git word in quotes, with a backslash, or followed by a variable, and a command word that is a substitution.
GIT_WORDS = (
    '"git" -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x',
    "'git' -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "gi''t -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "\\git -c core.hooksPath=/dev/null push origin HEAD",
    '"git" push origin HEAD',
    "git${IFS}push origin HEAD",
    "git${IFS}-c${IFS}core.hooksPath=/dev/null${IFS}push${IFS}origin${IFS}HEAD:refs/heads/x",
    "git$IFS-c${IFS}core.hooksPath=/dev/null push origin HEAD",
    "$(which git) -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "G=git; $G -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
)

#: B3: ANSI-C quotes, locale quotes and brace expansion, which the shell turns into a push.
QUOTING = (
    "git -c core.hooks$'P'ath=/dev/null pu$'s'h origin HEAD:refs/heads/x",
    "git -c $'core.hooks\\x50ath=/dev/null' $'\\x70ush' origin HEAD:refs/heads/x",
    "git -c {core.hooksPath=/dev/null,} {push,} origin HEAD:refs/heads/x",
    "git $'push' origin HEAD",
    "git $'\\x70ush' origin HEAD",
    "git pu$'s'h origin HEAD",
    'git pu$"s"h origin HEAD',
    "git {push,} origin HEAD",
    "eval $'git -c core.hooks\\x50ath=/dev/null \\x70ush origin HEAD:refs/heads/x'",
)

#: The git word in another case: on a file system that ignores case (macOS), `GIT` runs git, and `git-PUSH` runs git-push.
OTHER_CASE = (
    "GIT push --no-verify origin HEAD:refs/heads/x",
    "GIT -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "Git push origin HEAD",
    "git PUSH origin HEAD",
    "/usr/libexec/git-core/GIT-PUSH origin HEAD",
    "git-Send-Pack ../remote.git HEAD",
)

#: B4: the dashed binary of a command that pushes, with any path before it.
DASHED = (
    "$(git --exec-path)/git-push --no-verify origin HEAD:refs/heads/x",
    "/usr/lib/git-core/git-push origin HEAD",
    "git-send-pack ../remote.git HEAD",
    "$(git --exec-path)/git-http-push http://x/ HEAD",
)

#: The push guard of the specialist before #359 (commit 9a973b2), as text. The guard of today never allows a command
#: that this guard refuses.
GUARD_BEFORE_359 = (
    r'''python3 -c 'import json,re,sys; c=json.load(sys.stdin)["tool_input"]["command"]; '''
    r'''sys.exit(3 if re.search(r"\bgit(\s+-C\s+\S+)?\s+push\b", c) else 0)' 2>/dev/null; '''
    "case $? in 0) exit 0 ;; "
    "3) echo 'git push is blocked for specialist agents. Report the branch to the coordinator.' >&2; exit 2 ;; "
    "*) echo 'specialist push guard: no command text, or python3 is absent. The guard fails closed.' >&2; exit 2 ;; esac"
)

#: The commands of the never-weaker check: each probe of the review of #359, and each form of the cases of this file.
PROBES = (
    'echo `git push`',
    'x=`git push origin HEAD`',
    'echo `git -C x push`',
    'git -C $(pwd) push',
    'git -C $(pwd) push origin HEAD:refs/heads/x',
    'git -C "$(pwd)" push',
    'git -C `pwd` push',
    'git -c x=$(echo y) push',
    'git -c core.hooksPath=$(echo /dev/null) push',
    'git -C x$(true) push',
    'git push$NOPE origin HEAD',
    'git push"$NOPE" origin HEAD',
    'git push${NOPE} origin',
    'git -C <(true) push',
    'git push',
    'git -C x push',
    'my-git push',
    'git $(echo) push',
    'git -C $(pwd) -c core.hooksPath=/dev/null push origin HEAD:refs/heads/stolen',
    'git -c x=$(echo y) -c core.hooksPath=/dev/null push origin HEAD:refs/heads/stolen',
    'echo `git -c core.hooksPath=/dev/null push origin HEAD:refs/heads/stolen`',
    'git -C x$(true) -c core.hooksPath=/dev/null push',
    'git -C <(true) -c core.hooksPath=/dev/null push',
    'git -c core.hooksPath=/dev/null push$NOPE origin HEAD:refs/heads/stolen',
    'git -c core.hooksPath=/dev/null push""',
    'git -C . -c core.hooksPath=/dev/null push',
    'git -c core.hooksPath=/dev/null push',
    "git -C 'a b' push",
    'git --no-pager -C x push',
    'git -c x=y -c core.hookspath=z push',
    'git -c x=y -c core.hooksPath=z push',
    'env git push',
    'command git push',
    '/usr/bin/git push',
    'ls; git push',
    'ls && git push',
    'ls | git push',
    'ls\ngit push',
    'echo $(git push)',
    'GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=core.hooksPath GIT_CONFIG_VALUE_0=/dev/null git push',
    'git \\\npush',
    'git -C x \\\n push origin main',
    'git push --no-verify',
    'git -ccore.hooksPath=/dev/null push',
    'git -c core.hooksPath= push',
    'git -c "core.hooksPath=/dev/null" push',
    'git -c core."hooksPath"=/dev/null push',
    'git -c c"ore.hooksPath"=/dev/null status',
    'git --config-env=core.hooksPath=H push',
    "git -c x=y -C 'a b' --no-pager push origin main",
    'git\tpush',
    'git -C\tx\tpush',
    'git -C x\tpush',
    '(git push)',
    '{ git push; }',
    'x=1 git push',
    'sudo git push',
    'time git push',
    'nohup git push',
    'exec git push',
    'git p\\ush',
    "git pu''sh",
    'git "push"',
    'git -c alias.p=push p',
    'git -c core.hooksPath=/dev/null -C x status',
    'git -C x -c core.hooksPath=/dev/null log',
    'git -p push',
    'git --paginate push',
    'git --exec-path=/x push',
    'git --exec-path /x push',
    'git --git-dir x push',
    'git --work-tree x push',
    'git --namespace x push',
    'git -C x -C y push',
    'git --bare push',
    'git --literal-pathspecs push',
    'git --no-replace-objects push',
    'git -C x push-something',
    'git send-pack origin main',
    'git -C x send-pack origin HEAD',
    'git -C ~/repo push',
    'git -C $HOME push',
    'git -C "$X" push',
    'git -C ${X} push',
    'git -C a\\ b push',
    'if true; then git push; fi',
    "git -c 'a b=c' push",
    'git -c x="y z" push',
    'git --config-env core.hooksPath=H push',
    'xgit push',
    'git.exe push',
    'git-push',
    'git -C x status; git -c x=y push',
    'GIT_DIR=/x git push',
    'git  push',
    'git push2',
    'git -c x=y push2',
    'git -C /repo/worktree log',
    'git -C /repo/worktree log --oneline -5',
    'git -c color.ui=never diff',
    'git log --grep push',
    'git -C x log --grep push',
    'git -c color.ui=never diff HEAD -- push.txt',
    'git status',
    'git config --get core.hooksPath',
    'git commit -m "git push later"',
    "git commit -m 'do not git -c x=y push'",
    "grep -rn 'git push' docs",
    'echo git push',
    'git show HEAD:docs/push.md',
    'git -C x commit -m push',
    'git -C x diff -- push',
)


@pytest.fixture(scope="module")
def rendered(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("rendered")
    assert main(["render", str(EXAMPLE), "-o", str(out)]) == 0
    return out


def frontmatter(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---\n")[1])


@pytest.fixture(scope="module")
def claude_code_hook(rendered) -> str:
    fm = frontmatter(rendered / "claude-code" / "java-spring-specialist.md")
    [entry] = [e for e in fm["hooks"]["PreToolUse"] if e["matcher"] == "Bash"]
    return entry["hooks"][0]["command"]


@pytest.fixture(scope="module")
def opencode_permission(rendered) -> dict:
    return frontmatter(rendered / "opencode" / "java-spring-specialist.md")["permission"]


def run_hook(hook: str, command: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run(["bash", "-c", hook], input=payload, capture_output=True, text=True, cwd=cwd)


@pytest.mark.parametrize("command", PUSH_AFTER_OPTIONS)
def test_the_claude_code_guard_refuses_a_push_after_global_options(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "git push is blocked for specialist agents" in r.stderr


@pytest.mark.parametrize("command", PUSH_AFTER_OPTIONS)
def test_the_opencode_guard_refuses_a_push_after_global_options(opencode_permission, command):
    assert opencode_bash_action(opencode_permission, command) == "deny"


@pytest.mark.parametrize("command", HOOKS_OFF)
def test_the_claude_code_guard_refuses_a_git_command_that_turns_off_the_hooks(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "core.hooksPath" in r.stderr and "--config-env" in r.stderr


@pytest.mark.parametrize("command", HOOKS_OFF)
def test_the_opencode_guard_refuses_a_git_command_that_turns_off_the_hooks(opencode_permission, command):
    assert opencode_bash_action(opencode_permission, command) == "deny"


@pytest.mark.parametrize("command", CLAUDE_CODE_ONLY)
def test_the_claude_code_guard_reads_the_words_of_the_command(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "blocked for specialist agents" in r.stderr


@pytest.mark.parametrize("command", STILL_PASS)
def test_the_read_commands_of_git_still_pass_the_guard_of_each_harness(claude_code_hook, opencode_permission, command):
    r = run_hook(claude_code_hook, command)

    assert (r.returncode, r.stderr) == (0, "")
    assert opencode_bash_action(opencode_permission, command) == "allow"


@pytest.mark.parametrize("command", CLAUDE_CODE_PASS)
def test_the_claude_code_guard_passes_a_read_command_that_names_a_push(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert (r.returncode, r.stderr) == (0, "")


@pytest.mark.parametrize(
    "push",
    [
        "git -c core.hooksPath=/dev/null push origin HEAD:refs/heads/{ref}",
        "git -C $(pwd) -c core.hooksPath=/dev/null push origin HEAD:refs/heads/{ref}",
        "git 2>/dev/null -c core.hooksPath=/dev/null push origin HEAD:refs/heads/{ref}",
        '"git" -c core.hooksPath=/dev/null push origin HEAD:refs/heads/{ref}',
        "git -c core.hooks$'P'ath=/dev/null pu$'s'h origin HEAD:refs/heads/{ref}",
        "$(git --exec-path)/git-push --no-verify origin HEAD:refs/heads/{ref}",
    ],
    ids=["hooks-off", "F1-substitution", "B1-redirection", "B2-quoted-git", "B3-ansi-c-quotes", "B4-dashed-binary"],
)
def test_a_push_with_the_hooks_off_from_a_real_worktree_is_refused_by_the_rendered_guard(
    tmp_path, git_identity, claude_code_hook, push
):
    """The pushes that the reviews made: without the text guard they reach the bare remote; with the guard they do not."""
    vcs = GitVersionControl()
    repo = make_repo(tmp_path)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    git(repo, "remote", "add", "origin", str(remote))
    worktree = tmp_path / "wt"
    vcs.ensure_worktree(repo, worktree, "feature", "main")
    hooks = tmp_path / "guard-hooks"
    hooks.mkdir()
    (hooks / "pre-push").write_text("#!/bin/sh\necho 'delegate: push refused' >&2\nexit 1\n")
    (hooks / "pre-push").chmod(0o755)
    vcs.use_hooks_directory(repo, worktree, hooks)
    environment = {**os.environ, **vcs.worktree_environment(worktree)}

    def shell(command: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", "-c", command], cwd=worktree, capture_output=True, text=True, env=environment)

    def remote_branches() -> list[str]:
        return git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").split()

    # The control: the pre-push hook refuses a plain push, and the form under test goes past it.
    assert "delegate: push refused" in shell("git push origin HEAD:refs/heads/plain").stderr
    assert shell(push.format(ref="control")).returncode == 0
    assert remote_branches() == ["control"]

    command = push.format(ref="stolen")
    guard = run_hook(claude_code_hook, command, cwd=worktree)
    if guard.returncode == 0:
        shell(command)

    assert guard.returncode == 2, (guard.stdout, guard.stderr)
    assert "blocked for specialist agents" in guard.stderr
    assert remote_branches() == ["control"]


def refuses(hook: str, command: str) -> bool:
    return run_hook(hook, command).returncode == 2


def test_the_claude_code_guard_never_allows_a_command_that_the_guard_before_359_refused(claude_code_hook):
    commands = (
        *PROBES, *PUSH_AFTER_OPTIONS, *HOOKS_OFF, *CLAUDE_CODE_ONLY, *DYNAMIC_BEFORE_THE_SUBCOMMAND, *OTHER_PUSH_COMMANDS,
        *REDIRECTIONS, *GIT_WORDS, *QUOTING, *DASHED, *OTHER_CASE, *NOT_SEEN,
    )
    refused_before = [command for command in commands if refuses(GUARD_BEFORE_359, command)]

    assert "git -C $(pwd) push" in refused_before and "echo `git push`" in refused_before  # the check compares something
    assert [command for command in refused_before if not refuses(claude_code_hook, command)] == []


@pytest.mark.parametrize("command", DYNAMIC_BEFORE_THE_SUBCOMMAND)
def test_the_claude_code_guard_refuses_a_push_after_a_substitution_or_a_variable(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "blocked for specialist agents" in r.stderr


@pytest.mark.parametrize("command", OTHER_PUSH_COMMANDS)
def test_the_guard_of_each_harness_refuses_send_pack_and_http_push(claude_code_hook, opencode_permission, command):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "git push is blocked for specialist agents" in r.stderr
    assert opencode_bash_action(opencode_permission, command) == "deny"


@pytest.mark.parametrize("command", [c for c in DYNAMIC_BEFORE_THE_SUBCOMMAND if c.startswith(("git -", "git push"))])
def test_the_opencode_guard_refuses_a_push_after_a_substitution_or_a_variable_in_the_options(opencode_permission, command):
    assert opencode_bash_action(opencode_permission, command) == "deny"


@pytest.mark.parametrize("command", ["echo `git push`", "echo `git -C x push`", "gi''t push origin HEAD", "/usr/lib/git-core/git-push origin HEAD"])
def test_the_opencode_guard_allows_a_push_that_no_pattern_anchored_at_the_command_word_matches(
    claude_code_hook, opencode_permission, command
):
    # A recorded difference, as for a compound command (ADR 0002): each OpenCode pattern is anchored at "git", and the
    # map adds no wider pattern. The limits page names these forms. The Claude Code hook refuses them.
    assert refuses(claude_code_hook, command)
    assert opencode_bash_action(opencode_permission, command) == "allow"


@pytest.mark.parametrize("command", DYNAMIC_READS_PASS)
def test_the_claude_code_guard_passes_a_read_with_a_substitution_or_a_variable_that_names_no_push(claude_code_hook, command):
    r = run_hook(claude_code_hook, command)

    assert (r.returncode, r.stderr) == (0, "")


@pytest.mark.parametrize("command", [*REDIRECTIONS, *GIT_WORDS, *QUOTING, *DASHED, *OTHER_CASE])
def test_the_claude_code_guard_refuses_a_push_hidden_by_a_redirection_a_quoted_git_word_shell_quoting_or_a_dashed_binary(
    claude_code_hook, command
):
    r = run_hook(claude_code_hook, command)

    assert r.returncode == 2, (r.stdout, r.stderr)
    assert "blocked for specialist agents" in r.stderr


#: The forms of B1 to B4 that an anchored glob of the OpenCode map can deny (ADR 0002: each pattern starts at the command word).
OPENCODE_DENIES = (
    "git 2>&1 push --no-verify origin HEAD:refs/heads/x",
    "git >/dev/null push --no-verify origin HEAD:refs/heads/x",
    "git -C . 2>/dev/null push --no-verify origin HEAD:refs/heads/x",
    "git 2>/dev/null -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "git </dev/null -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    '"git" -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x',
    "'git' -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "git${IFS}push origin HEAD",
    "git$IFS-c${IFS}core.hooksPath=/dev/null push origin HEAD",
    "git $(echo) push",
    "git -c core.hooks$'P'ath=/dev/null pu$'s'h origin HEAD:refs/heads/x",
    "git $'\\x70ush' origin HEAD",
    "git {push,} origin HEAD",
    "git -c {core.hooksPath=/dev/null,} {push,} origin HEAD:refs/heads/x",
    "$(git --exec-path)/git-push --no-verify origin HEAD:refs/heads/x",
    "git-push origin HEAD",
    "git-send-pack ../remote.git HEAD",
    "GIT push --no-verify origin HEAD:refs/heads/x",
    "GIT -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "git `echo push` origin HEAD",
    "git -c alias.p=push p origin HEAD",
    "\\git -c core.hooksPath=/dev/null push origin HEAD",
)


@pytest.mark.parametrize("command", OPENCODE_DENIES)
def test_the_opencode_guard_denies_each_form_that_an_anchored_glob_can_match(opencode_permission, command):
    assert opencode_bash_action(opencode_permission, command) == "deny"


#: Forms that the hook does not see, as the limits page says: a variable or a glob inside the git word. The guard is a first line,
#: and the pre-push hook of the engine is the guard that holds. If the hook comes to refuse one of them, move it to a refusal list.
NOT_SEEN = (
    "gi${NOPE}t -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
    "/usr/bin/gi? -c core.hooksPath=/dev/null push origin HEAD:refs/heads/x",
)


@pytest.mark.parametrize("command", NOT_SEEN)
def test_the_claude_code_guard_does_not_see_a_variable_or_a_glob_inside_the_git_word(claude_code_hook, command):
    # A recorded limit: the docs name these forms, so a doc that claims more than the hook does fails review, not this test.
    r = run_hook(claude_code_hook, command)

    assert (r.returncode, r.stderr) == (0, "")
