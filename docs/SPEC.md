# The claims platform

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy, policy wording and claims manual. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The agent harness

The UnderCover Agent works the queue the way a handler does. One queue run can cover every waiting claim or stop at a chosen claim cap; a named waiting claim can also be run on its own. The queue agent chooses the order and keeps a durable to-do list. Each claim attempt uses its own claim graph and thread, with no earlier claims in its context.

For each claim the graph should:

- read the claim, the customer's policy and claim history, and every supporting document
- find and read the relevant sections of the policy wording and claims manual rather than receiving whole documents
- approve, decline or refer the claim
- use the backend's payout rules when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

The graph records the decision and owns the payment step. An approval that needs no sign-off is paid immediately. An approval over the limit pauses with its work saved until a handler signs off; the agent then resumes the same thread and pays it. Sign-off is saved even if the agent is temporarily unavailable.

If the agent is stopped partway through, the next run carries on from the latest saved step with the same thread and no new attempt. A claim that fails with an error instead returns to the queue for one fresh retry. After a second failure, the claim is left for a handler. One failed claim does not stop the run, a stopped queue run is finished by the next run, and no claim is decided or paid twice. Before finishing, the queue agent checks every selected claim and gives handlers a short summary.

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
