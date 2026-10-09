# ADR 0003: The caller can override the OpenCode model column

- Status: accepted
- Date: 2026-09-19
- Applies to: the OpenCode column of `tiers.toml`, and the `render` and `validate` commands

## Context

`src/delegate/tiers.toml` maps each tier to a model for each harness. On Claude Code the spawn call sets the model, so a rendered file has no model. On OpenCode a rendered file names the model of its tier, because OpenCode sets nothing at spawn. OpenCode resolves a model through a provider. The default OpenCode column names `anthropic/` models.

On 2026-09-19, `opencode models` on the machine of the first OpenCode rollout listed no `anthropic/` provider. Its only Claude models came through a company LLM gateway. A part of the machine configuration that belongs to one job set up that gateway as an OpenCode provider. Each rendered OpenCode agent file named a model that OpenCode could not resolve, so each OpenCode spawn of a pair failed. The gateway also served no model of one default family, so one tier needed an older model on that gateway.

The tier table is global. A provider can belong to one job or one machine.

## Decision

- The OpenCode column of `tiers.toml` stays the source of the defaults. The Claude Code column never moves.
- `render` and `validate` take two optional flags. Each flag can occur more than one time.
  - `--opencode-model TIER=MODEL`: TIER uses MODEL in the OpenCode column. A tier that is not named keeps its default.
  - `--opencode-allow MODEL`: MODEL joins the OpenCode allowed set. The defaults stay allowed.
- The caller is the configuration that sets up the provider, for example a work profile. When that configuration is off, the caller gives no flag, and the defaults come back.
- `bootstrap` takes neither flag. It writes a committed render, and a committed render carries the defaults.

## Rejected alternatives

- **Change the OpenCode column of `tiers.toml` to the gateway names.** The table is global, and the job configuration that sets up the gateway is removed when the job changes.
- **A second copy of the tier table for the job.** The decision did not accept a second copy: `tiers.toml` stays the single source of the defaults and of the Claude Code column. The decision recorded no further reason.
- **Add a direct provider with a personal API key, and keep the table.** The decision selected the caller override and recorded no reason against this option.
- **Do nothing.** The OpenCode agents stay present but unusable until a provider exists.
- **One flag that sets the model and also extends the allowed set.** That flag accepts each model that it names. An override onto a model that no provider serves then stays green. With two flags, the validator reports `BAD_MODEL` for a model that is not in the allowed set.

## Consequences

- A consumer keeps its provider identifiers in the configuration that sets up the provider, not in the kit.
- Without the flags, each command gives the same output as before the change.
- A committed project pair names the default OpenCode models. A repository that needs the gateway models re-renders the pair with `render` and the flags.

## Evidence

- 2026-09-19: `opencode models` listed no `anthropic/` provider and six Claude models through the gateway. The gateway listed no model of the Sonnet 5 family, so the standard tier used a Sonnet 4.6 model on the gateway.
- 2026-09-19: the decision. The first proposal was a change to `tiers.toml`. Review stopped it before verification, because the table is global.
- 2026-09-19: a blind verifier checked the change. With the override, each of the eleven rendered OpenCode files named a gateway model. Without the override, each one named the default. An override onto an unknown model gave `BAD_MODEL` and exit 1. The output of `render` and `validate` with no flag was byte-identical to the output before the change. `bootstrap` refused the flags with exit 2.
