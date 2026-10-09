# Why each specialist has its own verifier

Every [**specialist**](../glossary.md#specialist) in this kit has its own [**verifier**](../glossary.md#verifier). For a [**stack**](../glossary.md#stack) named `python`, the [**agent pair**](../glossary.md#agent-pair) is `python-specialist` and `python-verifier`. I do not use one generic verifier for all the work. This page gives my reasons.

## A verifier needs the skills of the specialist

A verifier that does not know the discipline of the stack reads the code and misses the discipline. A generic reviewer looks at a diff and asks if it is correct. It does not ask if the tests drive the public entry point, if the error names its input, or if the new module follows the layering of the repository. Those questions come from the [**skills**](../glossary.md#skill) of the stack. The specialist has the skills, so it knows them. A reviewer without them can only agree with what it sees.

So the verifier holds the same skills as the specialist. It also holds the same domain references. The verifier then reviews the diff against the same standard that the specialist built to. A defect that breaks the standard is a defect that the verifier can name.

## The verifier differs where trust matters

The two halves of a pair are not copies. They differ in three ways, and each way is about trust.

- The verifier has a read-only tool set. It may read the code and run the [**gates**](../glossary.md#gate). It may write only in a temporary location. The specialist writes the code, and the verifier does not.
- The verifier runs on the `verifier` [**tier**](../glossary.md#tier). The [**engine**](../glossary.md#engine) never puts it lower, in any [**mode**](../glossary.md#mode). See [the mode trade-off](the-mode-trade-off.md).
- The verifier gets no [**report**](../glossary.md#report) from the specialist. See [why the verifier is blind](why-the-verifier-is-blind.md).

The specialist and the verifier must not share a context or a bias. They share knowledge, which is the skills. They do not share work.

## One declaration, so the pair cannot drift

I do not write the two files by hand. One [**declaration**](../glossary.md#declaration) renders both halves, for each [**harness**](../glossary.md#harness). If the specialist gains a skill, the verifier gains it in the same render, because both read the same list. If I wrote the files separately, one would fall behind, and the verifier would lose the skill that it needs most.

The [**validator**](../glossary.md#validator) checks the result. It reports a specialist with no verifier, and a verifier with no specialist, as `MISSING_TWIN`. See [the finding codes](../reference/commands.md#the-finding-codes).

## When a generic verifier is right

A generic verifier is correct for one kind of work: docs and configuration. No domain pair applies there, because no domain discipline applies. For any other work, I use the verifier of the stack.

## A monorepo has a pair for each stack

A repository with more than one stack gets one pair for each stack. A Python backend and a web front end do not share skills, so they do not share a pair. Each specialist carries every skill of its own stack, and each verifier may run only the gates of its own stack. See [how to bootstrap a monorepo](../how-to/bootstrap-a-monorepo.md).
