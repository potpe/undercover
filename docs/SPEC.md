# The claims platform

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy, policy wording and claims manual. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The agent harness

The UnderCover Agent works the queue the way a handler does. One queue run can cover every waiting claim or stop at a chosen claim cap; a named waiting claim can also be run on its own. The queue agent chooses the order and keeps a durable to-do list. Each claim is worked by a fresh claim subagent with no earlier claims in its context.

For each claim the subagent should:

- read the claim, the customer's policy and claim history, and every supporting document
- find and read the relevant sections of the policy wording and claims manual rather than receiving whole documents
- approve, decline or refer the claim
- work out the payout when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

A failure before a decision returns the claim to the queue for one fresh retry. After a second failure, the claim is left for a handler. One failed claim does not stop the run, and no claim is decided or paid twice. Before finishing, the queue agent checks every selected claim and gives handlers a short summary.

Our goal is to handle claims straight through, as accurately as our handlers do.

## What the team needs to see

- The queue, with where each claim stands: waiting, in progress, waiting for sign-off, decided or left for a handler.
- The current run's cap, model, ordered to-do list, attempts, results and summary.
- Each claim in full, with the agent's decision and its reasons.
- What the agent is doing on a claim while it works, step by step.
- Which sections of the policy wording and claims manual it read.
- The queue and claim agents' instructions, read only.

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
