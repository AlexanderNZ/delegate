# The delegate plugin for Claude Code

This plugin shows the progress of a `delegate` run of the repository in Claude Code. It holds no progress logic. Every 5 seconds, it runs `delegate status --json` in the working directory of the session, and it shows that snapshot. It runs no other `delegate` command, so Claude Code and `delegate status` show the same numbers.

## What it shows

- **The status line entry.** While a run is active, the entry shows the short run id, the percentage, the ETA, the running ticket and its phase, the time since the last activity, and the two token counts:

  ```text
  delegate 1638 · 84% · ETA 13:12 · ▶ 60 specialist · active 18s · 1.9M new · 53.0M cache
  ```

  In the `gates` phase, the time since the last activity is the time since the last output of the gate.
- **The warning.** When no agent activity occurs for `stall_minutes` minutes, the entry shows `⚠ idle 6m40s` in place of `active 18s`. A band above the prompt then shows the warning in the warning colour of the theme. The status line of Claude Code shows text only, so the colour is on the band.
- **The toasts.** A toast shows when a ticket ends (`delegate 1638: ticket 60 built`), and when the run ends (`delegate 1638: the run ended, built`). The tickets that ended before the first poll of the session give no toast.
- **The pane.** The `/delegate` command opens a pane with one row for each ticket: the state, the phase, the time used, and the tokens. The pane changes at each poll.

With no active run, the plugin shows nothing. When the command fails, or its output is not JSON with a `percent` field (an old version of `delegate`), the plugin shows nothing and gives no error.

## Options

| Option | Default | What it does |
| --- | --- | --- |
| `command` | `delegate` | The `delegate` command. The plugin runs `<command> status --json`. Set a full path when the `delegate` on the `PATH` is an old version. |
| `stall_minutes` | `5` | The minutes with no agent activity before the warning shows. |

Set the options in `/config`, or in `pluginConfigs.delegate.options` of the settings.

## Enable it

The root of this repository is a plugin marketplace: `.claude-plugin/marketplace.json` lists this plugin as `delegate`. In the root of a clone, run:

```bash
claude plugin marketplace add ./
claude plugin install delegate@delegate
```

To try a working copy for one session, run `claude --plugin-dir plugins/claude-code`.

## Test it

```bash
claude plugin validate plugins/claude-code
claude plugin test plugins/claude-code
```

The tests in `tests/` give the plugin a `delegate status --json` snapshot that was recorded during a real run (`tests/snapshot.ts`), and assert the status line entry, the toasts, the band, and the pane.
