---
name: Task
about: Work with a decided design — a feature, a refactor, or a fix with a known shape
---

## Parent

<!-- Say where this work comes from: "Part of #1", "Graduated from #N", or the review that found it. Link the parent. A ticket with no parent may need a decision first. -->

## Context

<!-- Write one or two sentences. The reader must have them before the request makes sense. Say why: what breaks, misleads, or blocks while this work is not done. -->

## What to build

<!-- Write the rule first, then how it applies. Name the mechanism. Do not describe it with an image. -->

## Affected area

- [ ] Protocol skill (`skills/agent-delegation/`)
- [ ] Renderer, validator, and bootstrap (`skills/agent-definitions/`)
- [ ] Brief generator (`verifier-brief`)
- [ ] Engine (`delegate run`, `delegate status`, `delegate watch`)
- [ ] Harness adapters
- [ ] Tier table (`tiers.toml`)
- [ ] Docs
- [ ] CI / packaging

## Acceptance criteria

<!-- Make each criterion checkable by a reader who did not do the work. -->

- [ ]
- [ ] Verification gates pass (`docs/agents/delegation.md`)
- [ ] The neutrality check passes if the change touches `skills/`

## Blocked by

<!-- List each ticket that must merge first. Give the reason for each: same file, same seam, or decision pending. Delete this section if nothing blocks. -->
