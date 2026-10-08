<!-- Title: say what the change does in one sentence. End with (#ticket). -->

## Context

<!-- Write one or two sentences. The reader must have them before the change makes sense: the defect, the decision, or the ticket that demands it. Give the context first, then the point. -->

## The change

<!-- Write the governing rule in the first sentence. Then say how the code now applies it. Say what the software starts doing, stops doing, or does in a different way. -->

## Ticket

<!-- Write "Ticket: #N". Never use a closing keyword (Closes, Fixes, Resolves). The maintainer closes the ticket after the merge and a green CI run. -->

Ticket: #

## Affected area

- [ ] Protocol skill (`skills/agent-delegation/`)
- [ ] Renderer, validator, and bootstrap (`skills/agent-definitions/`)
- [ ] Brief generator (`verifier-brief`)
- [ ] Engine (`delegate run`, `delegate status`, `delegate watch`)
- [ ] Harness adapters
- [ ] Tier table (`tiers.toml`)
- [ ] Docs
- [ ] CI / packaging

## Verification gates

<!-- Run the gates before you open the pull request (`docs/agents/delegation.md`). Check only a gate that ran green. If a gate does not apply, say why. Without Nix, run the suite with pip and pytest: the document gives the steps. -->

- [ ] `cd skills/agent-definitions/validator && nix develop -c python3 -m pytest -rs`: the suite is green
- [ ] `nix flake check`: green
- [ ] The neutrality check passes (if the change touches `skills/`)

## Evidence

<!-- Show what proves that the change works: the test output, the command output, or the rendered files. -->
