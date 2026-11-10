# The claims platform

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy, policy wording and claims manual. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The agent harness

The UnderCover Agent works the queue the way a handler does. A dispatch covers every waiting claim and every claim left in progress, oldest submitted first, with up to N claim agents at once. Code chooses and tracks that work without a model. Each claim uses its own claim graph and thread, with no earlier claims in its context. A named waiting claim can also be run on its own without a dispatch.

For each claim the graph should:

- read the claim, the customer's policy and claim history, and every supporting document
- find and read the relevant sections of the policy wording and claims manual rather than receiving whole documents
- approve, decline or refer the claim
- use the backend's payout rules when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

The graph records the decision and owns the payment step. An approval that needs no sign-off is paid immediately. An approval over the limit pauses with its work saved until a handler signs off; the agent then resumes the same thread and pays it. Sign-off is saved even if the agent is temporarily unavailable.

A rate limit or timeout waits and carries on from the latest saved step as the same attempt. If its retries run out, the claim stays in progress for the next dispatch. Any other failure returns the claim to the queue for one fresh retry in the same dispatch; a second failure leaves it for a handler. One bad claim does not stop the dispatch, and no claim is decided or paid twice.

If the agent is stopped partway through, its dispatch or evaluation is marked stopped. The next dispatch carries on with the claims left in progress and the waiting queue, preserving each saved thread and attempt. A dispatch skips a claim already resuming after sign-off. Its summary comes from our records, not a model: decision counts, payouts waiting for sign-off, claims left for a handler and claims still in progress.

Evaluations use fresh copies of either labelled set, whole or its five tiny claims, through the same dispatcher and claim graph. Copies never enter the queue or accept sign-off; a held approval counts as finished and its saved progress is cleared. The Scoreboard compares each copy with its handler decision and reports decision accuracy, payout accuracy, cost of mistakes, straight-through rate, model cost and active/whole-run time. An undecided copy counts as a referral.

Our goal is to handle claims straight through, as accurately as our handlers do.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off, decided or left for a handler.
- The current dispatch's N, model, reasoning, claims, attempts, states, record-built summary, model cost, active work time and whole-run time.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step.
- Which sections of the policy wording and claims manual it read.
- Evaluations, newest first, with every quality, mistake, cost and time measure and handler/agent comparisons linked to the copies.
- The claim agent's instructions, read only.

## Rules

- A payout over $1,000 needs a handler's sign-off. The agent can approve such a claim, but the payment waits until a handler signs off. The system must block the payment until then, even if the agent tries to pay.
- Decisions follow the policy wording and the claims manual. The policy wording is the contract and takes precedence.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars.

## Our records

Everything is in `data/`, described in `data/README.md`: the claims queue, the policies, the customers, the plans, the handlers, the policy wording and the claims manual. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

## Not for now

- No sign-in. Everyone who uses the screens sees everything.
- No real payments.
- No messages to customers.
