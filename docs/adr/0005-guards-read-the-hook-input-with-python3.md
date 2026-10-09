# ADR 0005: The Claude Code guards read the hook input with python3

- Status: accepted
- Date: 2026-10-08
- Applies to: the specialist push guard and the verifier guard on Claude Code

## Context

Each rendered Claude Code guard is a `PreToolUse` hook on `Bash`. The harness sends the hook a JSON document on stdin, and the guard needs the string at `.tool_input.command`. Before this decision, both guards read it with `jq`.

A machine with no `jq` got two different failures:

- the verifier guard denied every command, because it fails closed;
- the specialist push guard exited 0 and blocked nothing, because its `jq ... && block; exit 0` chain skipped the block when `jq` was missing.

The kit's own rule is that its behaviour never depends on `jq` (spec, "Pure delegation tool").

## Decision

Both guards read the command with `python3`, which the kit already requires. Only the read step changes. The segment split, the allow lists, the temp-path rules and the push pattern stay as they were.

- The verifier guard prints the command with `python3`. It still exits 2 when the output is empty.
- The push guard reads the command and tests the same regular expression in one `python3` call. The call exits 3 for a push and 0 for any other command. Status 0 exits 0; status 3 exits 2 with the block message; every other status exits 2 with a message that names the cause.

Both guards now fail closed. With no `python3`, or with input that holds no command string, each guard exits 2. The push guard failed open in these cases before.

## Rejected alternatives

- **Parse the JSON with `sed` or `grep`.** A command can hold an escaped quote or a newline, and a text match on JSON reads them wrong. A wrong read of a guard's input is a bypass.
- **Keep `jq` and fall back to `python3`.** Two readers need two sets of tests. The two readers could give different results for the same input.
- **Keep the push guard open when it cannot read the input.** The guard exists to block a push, and an unreadable input is not evidence of no push.
