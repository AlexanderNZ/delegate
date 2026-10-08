---
name: Bug
about: Observed behaviour disagrees with the spec, a reference page, or the intent of the user
labels: 'bug'
---

## Context

<!-- Say what the bug costs. Name the spec clause, the reference page, or the user intent that it violates. Cite the source. -->

## Reproduction

<!-- Fill in each line. Remove every secret from the command line, the journal excerpt, and the workflow file. -->

- Package version or git sha:
- Harness and its version:
- OS:
- Python version:
- Command line:
- Run id and journal excerpt (`runs/<run-id>/journal.jsonl`), if the engine ran:
- Workflow file (secrets removed), if one is used:
- Steps:

## Affected area

- [ ] Protocol skill (`skills/agent-delegation/`)
- [ ] Renderer, validator, and bootstrap (`skills/agent-definitions/`)
- [ ] Brief generator (`verifier-brief`)
- [ ] Engine (`delegate run`, `delegate status`, `delegate watch`)
- [ ] Harness adapters
- [ ] Tier table (`tiers.toml`)
- [ ] Docs
- [ ] CI / packaging

## Observed

<!-- Say what happens. Be specific: exit codes, error messages, and the output of the command. -->

## Expected

<!-- Say what must happen. Give the rule that says so: the spec, a reference page, an ADR, or a ticket decision. -->

## Mechanism (hypothesis)

<!-- Give the best current explanation, with code pointers. An unverified explanation is acceptable. Write "unverified". The fix starts from a diagnosis, not from this section. -->

## Evidence

<!-- Add the logs, the journal events, the rendered files, and the command output that show the bug. -->
