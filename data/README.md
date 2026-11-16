# UnderCover seed data

The data behind UnderCover Travel Insurance, a fictional insurer. Everything here is invented, including the people, the airlines and the hotels. Amounts are in US dollars.

## The insurer's systems

- `company.json`: company settings, including the $1,000 payout approval limit.
- `plans.json`: the legacy copy of the three plans (Economy, Premium, First) and add-ons. It remains in the repository but is no longer a product input; context seeding uses `context/plans.yaml`.
- `policies.json`: one policy per trip, with the plan, the add-ons, the purchase date, the trip and the travelers.
- `customers.json`: contact details and claim history, with the date and a one line summary of each earlier claim. This is personal data.
- `handlers.json`: the claims handlers.
- `claims.json`: the original twenty-five queue claims, kept unchanged. Each has the customer's story, the items claimed and the supporting documents as text. Seeding adds `CLM-1901` from `security/claims.json` with its security policy and customer, making 26 operational claims. `security/decisions.json` is not seeded, so the security claim enters neither labelled evaluation set.

## The documents

- `policy_wording.md`: a legacy copy of the contract. The product uses the canonical `context/policy_wording.md` and stores its full contents/version in the backend documents table during seeding.
- `claims_manual.md`: a legacy copy of the internal handling manual. The product uses `context/claims_manual.md` and stores its full contents/version in the backend documents table during seeding.

The seven files in `context/` are data governance's read-only sources for field definitions, glossary terms, plans, claim rules, reference facts, policy wording and the claims manual. Do not change them. The context MCP service reads them; backend seeding imports plan rules and complete versioned documents from them. Ordinary backend imports and requests do not read those files. The instructions append both documents from the backend documents table. The approval limit remains the setting in `company.json`.

The dashboard's Context used and activity views display provenance recorded from the read-only context service; they do not make these files mutable or change their ownership. Optional AgentCore Memory is customer-scoped runtime context, not a seed-data source. Backend claim history remains authoritative for repeat-claim handling.

## The evaluation set

- `evals/decisions.json`: the handlers' decisions on the twenty-five claims in the queue: approve, decline or refer, the payout, the calculation, the reason and what each claim tests. An agent deciding claims must never be shown this file.
- `evals/held_out_claims.json` and `evals/held_out_decisions.json`: twenty-five more claims with their decisions, kept out of the queue for checking that an improvement generalizes. They have the same blend as the queue. Keep them out of sight while you write a prompt or a rule, and score them afterwards.

The queue is the development set and the held-out claims are the test set. Nothing is trained on either.

Every decision also carries a few tags. `difficulty` is `routine`, `hard` (the wording settles it and it stretches a small model) or `manual` (it needs something the prompt does not hold, listed in `needs`: the claims manual, the customer's history or both). `failure_class` groups the hard claims that one rule should fix. `tone` is `plain`, `amusing` or `nod`. `tiny` marks a subset of five claims in each set, one of each kind, which fits inside a free model's daily allowance.

The labs define `get_sample_data(tiny=False, held_out=False)`, which returns the claims of either set with their decisions.

A decision is scored on two things: the decision itself and the payout. A valid claim that pays more than $1,000 is still an approve. The payment waits for a handler's sign-off, and `over_approval_limit` marks those claims.

The backend decision record includes the decision, payout, `section`, reason, required `decided_by`, UTC `decided_at` and the claims manual's version from its stored documents (`2026.2`). An approval also records its exact calculation as `lines: [{step, amount}]` and `payout`; declines and referrals have zero payout and no calculation. The latest working calculation belongs to the claim's current attempt (`failed_attempts + 1`) and is copied into the accepted approval record, not changed in place. Identical decision replays preserve the original maker, time and manual version. The evaluator reads `section` and keeps the existing scoring behavior.

The claim dashboard shows the complete decision record and approval calculation, typed agent steps in the existing chronological activity feed, and lookup provenance grouped by source file. Its agent-key-only `POST /api/claims/{id}/steps` route records typed steps alongside retained message-based activity bookkeeping; it is not one of the six app model operations.

Test helpers preserve existing approval amounts by calculating one `expense` line under `emergency_medical` for the expected payout plus the plan deductible, then recording the exact returned payout and decision maker. Backend-only tests may set the matching current-attempt calculation directly.

## The security claim

- `security/`: CLM-1901, a stolen watch whose story carries an instruction to the AI to approve it in full. Its policy and customer are seeded into the operational queue; its handler decision remains unseeded, and the claim is excluded from both evaluation sets.

The Lambda package includes the application data directory. Evaluation sources and handler labels remain backend-only: the claim agent receives neither the files nor their answer keys. The package is built and checked locally; no AWS resources are created by this build.
