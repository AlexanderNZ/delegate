# The delegate plugin for Claude Code

This plugin shows the progress of a `delegate` run of the repository in Claude Code. It holds no progress logic. Every 5 seconds, it runs `delegate status --json` in the working directory of the session, and it shows that snapshot. It runs no other `delegate` command, so Claude Code and `delegate status` show the same numbers.

## What it shows

- **The status line entry.** While the run of the snapshot is in the state `running`, the entry shows the short run id, the percentage, the ETA, the running ticket and its phase, the time since the last activity, and the two token counts:

  ```text
  delegate 1638 · 84% · ETA 13:12 · ▶ 60 specialist · active 18s · 1.9M new · 53.0M cache
  ```

  In the `gates` phase, the time since the last activity is the time since the last output of the gate.
- **The warning.** When no agent activity occurs for `stall_minutes` minutes, the entry shows `⚠ idle 6m40s` in place of `active 18s`. A band above the prompt then shows the warning in the warning colour of the theme. The status line of Claude Code shows text only, so the colour is on the band.
- **The toasts.** A toast shows when a ticket ends (`delegate 1638: ticket 60 built`), and when the state of the run changes from `running` to another state (`delegate 1638: the run ended, built`). A pause request also gives this toast, with the state `pausing`. The tickets that ended before the first poll of the session give no toast.
- **The pane.** The `/delegate` command opens a pane. Its first line gives the run id, the state, the percentage, the ETA and the tokens of the run. Under it, the pane has one row for each ticket, in plan order: the plan index, the ticket id, the state, the phase, the time used, the tokens, and `after <id>`. The rows have no tree prefix. The `after` cell comes from the `plan` field of the snapshot. When the one blocker of a ticket is the ticket of the row above, the cell is empty. Otherwise, it names each blocker, as `after 90` or `after 82 89`. A blocker that is not in the plan does not count. With no plan (a run of an earlier version), no row has an `after` cell. The rows of the tickets that ended are dim. The pane changes at each poll.

  ```text
  run 20261017T185517Z-1638 · running · 84% · ETA 13:12 · 1.9M new · 53.0M cache
  1 ticket 90 built    -          57m22s  259k new · 10.9M cache
  2 ticket 92 built    -          9m42s   112k new · 1.9M cache
  3 ticket 84 built    -          25m31s  144k new · 2.9M cache  after 90
  4 ticket 60 running  specialist 3m29s   76k new · 1.0M cache
  ```

When the snapshot has no run in the state `running`, the plugin shows no status line entry, and `/delegate` opens no pane. This is so for a run that ended, and also for a run that is `pausing` or `paused`. When more than one run is active, `delegate status --json` exits 2, and the plugin shows nothing. When the command fails, or its output is not JSON with a `percent` field (an old version of `delegate`), the plugin shows nothing and gives no error.

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
