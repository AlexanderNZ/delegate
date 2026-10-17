# The mode trade-off

`delegate run` has two [**modes**](../glossary.md#mode), [**`assure`**](../glossary.md#assure) and [**`economy`**](../glossary.md#economy). They trade cost against the time it takes to learn that something is wrong. I did not want one fixed choice, because the work I delegate is not one kind of work.

The modes came out of the review loop that played football: reject, fix, reject, pass, and then a full re-check after a two-line fix, with no stop rule and a massive token bill. Both modes cap the [**fix-up**](../glossary.md#fix-up) rounds, and a fix-up check reads only the new commits. What the modes choose between is when the check happens.

## Two kinds of work

Some work is a few [**tickets**](../glossary.md#ticket) where a defect in one ticket would spoil the next. For that work I want to know about a defect before anything builds on it, and I will pay for that.

Other work is a long chain of small tickets, each of which is easy. For that work, a [**verifier**](../glossary.md#verifier) for each ticket is most of the bill, and most of those verifiers find nothing. I will accept a later answer to pay less.

A [**protocol**](../glossary.md#protocol) with one mode forces a bad choice. I either skip the protocol for the cheap work, or I pay for assurance that the work does not need. Two modes let me make the choice on purpose.

## What each mode gives up

`assure` gives up tokens and wall-clock time. After each ticket, the [**engine**](../glossary.md#engine) rebases the branch, runs the [**gates**](../glossary.md#gate) again, and spawns a verifier at once. The [**specialist**](../glossary.md#specialist) runs on the `strong` [**tier**](../glossary.md#tier). The [**run branch**](../glossary.md#run-branch) moves only after the verifier accepts the branch, so a defect is found early, before it joins the verified work.

`economy` gives up early discovery. Each ticket branch starts from the previous ticket. The engine runs no gate for one ticket. At the end of the [**chain**](../glossary.md#chain), for each [**stack**](../glossary.md#stack), it runs the gates once on the chain tip, and then one verifier runs. The verifier gets the output of the green gates and does not run them again. The specialist runs on the `standard` tier. A defect in the first ticket shows only at the end, after the later tickets built on it. That is also true for a defect that the gates find. The fix-up then sits on the chain tip. Red gates and a [**REJECT**](../glossary.md#reject) share two fix-up rounds, and a third problem fails the [**run**](../glossary.md#run). In return, a long chain costs the fewest tokens and the least time.

I did not invent a number for the saving, because it depends on the chain, the gates and the [**gateway**](../glossary.md#gateway). What I can say is what drives the cost. Each verifier is a model run with a full brief. `assure` runs one for each ticket, and `economy` runs one for each stack. The gates run in both modes, and the engine runs them outside the models. `assure` runs them after each ticket and after each rebase. `economy` runs them once for each stack, on the chain tip, and again only after a fix-up. When a full gate run takes longer than the work of a specialist, that difference is most of the time of a run. For the exact settings of each mode, see [the modes table in the workflow reference](../reference/workflow.md#modes) and [the economy chain](../reference/run.md#the-economy-chain).

## What no mode gives up

Both modes keep the seven [**invariants**](../glossary.md#invariant). The verifier is blind. The engine runs the gates outside the specialist. Nothing reaches the run branch without an [**ACCEPT**](../glossary.md#accept). The specialist never pushes. Each [**hotspot**](../glossary.md#hotspot) has one writer. A fix-up is a new commit. And the verifier tier never goes down.

The last rule matters most to me. A cheaper mode saves money on the specialist and on the number of verifier runs. It never means a weaker verifier. If the cheap mode used a cheap verifier, I would be paying less to know less, and the saving would be false. The [agent-delegation skill](https://github.com/AlexanderNZ/delegate/blob/main/skills/agent-delegation/SKILL.md#the-invariants-of-every-mode) holds the full list. For the reasons behind the first two rules, see [why the verifier is blind](why-the-verifier-is-blind.md).

Verification at the end of a chain is legal practice in `economy` mode. It is not an exception to the protocol. The rule to verify each branch at once is the `assure` rule, and `economy` does not break it, because `economy` does not claim it.

## How I choose

I choose `assure` when a defect in one ticket would spoil the tickets after it, or when the tickets touch code that I do not know well. I choose `economy` for a long chain of small tickets, when the fewest tokens matter more than the feedback time. If I am unsure, I start with `assure` and move to `economy` when the first tickets show that the chain is easy.

To run a chain in `economy` mode, see [how to run an economy chain](../how-to/run-an-economy-chain.md).
