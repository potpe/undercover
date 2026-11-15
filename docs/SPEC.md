# The claims platform

From the head of claims operations at UnderCover Travel Insurance.

## How claims work today

UnderCover sells travel insurance on three plans: Economy, Premium and First. Our three claims handlers work through a queue of claims by hand. For each claim, a handler reads the customer's story and documents and checks them against the policy, policy wording and claims manual. Then they approve the claim with a payout, decline it or refer it. A claim is referred when it needs someone's judgment or more information, as the claims manual explains.

## The agent harness

Agent-side backend requests require `UNDERCOVER_API_KEY` in `X-API-Key`. Shared claim reads accept this key or a signed-in handler. Dashboard-only reads and actions require a signed-in handler; the key never grants a handler's identity or permissions. Health checks remain public. The backend can start without a configured key, but agent-only requests are refused and the agent service cannot start.

The UnderCover Agent works the queue the way a handler does. A dispatch covers every waiting claim and every claim left in progress, oldest submitted first, with up to N claim agents at once. Code chooses and tracks that work without a model. Each claim uses its own claim graph and thread, with no earlier claims in its context. A named waiting claim can also be run on its own without a dispatch.

The claim model uses the same claim-scoped tools whether it reaches the app directly or through AgentCore Gateway. The model cannot supply claim, policy or customer ids for its reads/calculation, nor a decision maker; the graph supplies those from the current claim. The backend requires and persists `decided_by`: the agent supplies `UnderCover Agent (configuration version N)` and the screening referral supplies `Security check`. The model records a decision through a tool, but payment remains a graph operation. Bookkeeping, runs, versions, the improver, and the security check's claim read/referral continue to use the direct app route with the agent key. When configured, Gateway requests use AWS CLI credentials and region and SigV4 for `bedrock-agentcore`; otherwise app model tools use the direct API and its key.

The security check and both app routes use the same canonical claim text. App, context and Gateway policy refusals reach the model as refusals, never as successful-looking answers. A Gateway payment refusal is recorded directly and moves an eligible approved, unpaid claim from `in_progress` to `waiting_for_sign_off`, storing `gateway_refused` and `gateway_refusal_reason`. The agent-key-only bookkeeping operation is `POST /api/internal/claims/{claim_id}/gateway-hold` with a required `reason`; it is not part of the agent OpenAPI document or Gateway. The claim bundle exposes the refusal fields. The existing handler sign-off records payment atomically; resuming the graph does not pay again. The sign-off explanation identifies a Gateway refusal rather than saying the app limit was exceeded.

The agent reads `UNDERCOVER_API`, `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` and `UNDERCOVER_MEMORY_ID` when constructing the applicable client or claim graph, not at module import, so late `.env` loading is honored. `UNDERCOVER_MEMORY_ID` is read for forward-compatible configuration but is unused until Phase 4. MCP operations remain synchronous to the graph: each short asynchronous MCP call runs on its own worker thread and event loop, without consuming or replacing the main thread's event loop.

The read-only context service is an independent MCP service. Its seven data-governance files are owned by their named systems and must remain unchanged. Each answer carries `answer` and `sources`; each flat source identifies `file`, `system`, `owner` and `reviewed`. The app reads these files only during explicit seeding: plans come from `context/plans.yaml`, and the complete policy wording and claims manual, including their versions, are stored in the backend documents table. Instructions use those stored document contents. Legacy `data/plans.json`, `data/policy_wording.md` and `data/claims_manual.md` remain in the repository but are no longer product inputs. The approval limit continues to come from `data/company.json`.

The service's read-only tools are `lookup_term`, `describe_fields`, `get_plan`, `get_claim_rules`, `search_reference_data`, `search_documents` and `read_section`. They return the matching definitions, plan/rule/reference answers, or document sections with source provenance. An unknown term returns the glossary's known terms. Missing sections and invalid inputs are refusals rather than empty successful answers.

## Decision records

An accepted decision is a complete, immutable record. It contains the decision, USD `payout`, `section`, plain-language `reason`, required `decided_by`, UTC `decided_at` and `manual_version` from the backend documents table (currently `2026.2`). An approval also stores `calculation` as `{ "lines": [{ "step": "...", "amount": 0.00 }], "payout": 0.00 }`; its payout must exactly match the calculation payout. Declines and referrals have zero payout and no calculation. The agent supplies `UnderCover Agent (configuration version N)` as maker; the hidden-instruction screening referral supplies `Security check`.

`calculate_payout` keeps the latest working calculation for the claim's current attempt, identified by `failed_attempts + 1`. A later calculation in that attempt replaces the earlier working calculation. Transient resume stays in the same attempt; an ordinary failure/new thread starts a fresh attempt. Only an accepted approval copies its exact calculation into the immutable decision record; declines and referrals never do.

The backend validates section 14 before accepting a record. Blank/whitespace reason, section or maker, a non-positive approval payout, a negative payout, and a nonzero decline/referral payout receive a custom 422 naming section 14. An approval also requires a calculation for the current attempt and an exact payout match; a missing, stale or mismatched calculation is refused with a specific section 14 error. A refused draft is not recorded: the tool returns the refusal to the model so it can correct and retry. An identical accepted decision replay returns the existing record unchanged, including maker, decision time and manual version.

For each claim the graph should:

- screen the complete customer-written claim for hidden instructions before the claim model sees it; refer flagged claims with zero payout, the check's reason and `Security check` as the section
- read the claim, the customer's policy and claim history, and every supporting document
- use the read-only context service for definitions, plan details, claim rules, reference facts, and searches/reads of relevant policy wording and claims manual sections; effective instructions also include both backend-stored documents
- approve, decline or refer the claim
- use the backend's payout rules when it approves
- explain the decision in two or three sentences, along with the relevant section of the policy

The graph records the decision and pays an approval that needs no sign-off immediately. An approval over the limit pauses with its work saved until a handler signs off. The backend records sign-off and payment atomically, without contacting the agent service. A resumed agent finishes the same thread without paying again.

The screening check is outside versioned claim-agent configuration and cannot be changed by the improver. It uses Luna at `none`, or the platform's pinned one-model configuration with no reasoning effort. Every attempt's reported usage counts toward the claim and run cost. An unreadable answer has at most three attempts; errors stop claim-model work and follow normal failure handling. Completed screening is saved, so a resumed thread is not checked again; a fresh attempt is.

A rate limit or timeout waits and carries on from the latest saved step as the same attempt. If its retries run out, the claim stays in progress for the next dispatch. Any other failure returns the claim to the queue for one fresh retry in the same dispatch; a second failure leaves it for a handler. One bad claim does not stop the dispatch, and no claim is decided or paid twice.

If the agent is stopped partway through, its dispatch or evaluation is marked stopped. The next dispatch carries on with the claims left in progress and the waiting queue, preserving each saved thread and attempt. A dispatch skips a claim already resuming after sign-off. Its summary comes from our records, not a model: decision counts, payouts waiting for sign-off, claims left for a handler and claims still in progress.

Evaluations use fresh copies of either labelled set, whole or its five tiny claims, through the same dispatcher and claim graph. Sources are selected through backend-held handler labels; the unlabelled security claim `CLM-1901` enters neither set. Copies never enter the queue or accept sign-off; a held approval counts as finished and its saved progress is cleared. The Scoreboard compares each copy with its handler decision and reports decision accuracy, payout accuracy, cost of mistakes, straight-through rate, model cost and active/whole-run time. An undecided copy counts as a referral.

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

- A payout over the configured approval limit (initially $1,000) needs a handler's sign-off. The agent can approve such a claim, but its payment API always refuses amounts above the current limit with 403 before looking up the claim. Only backend-owned handler sign-off can record an over-limit payment.
- Decisions follow the policy wording and the claims manual. The policy wording is the contract and takes precedence.
- Our handlers' own decisions on these claims are in `data/evals/`. We use them to check the agent's work. The agent must never see them.
- Amounts are in US dollars.

The generated `/api/agent/openapi.json` document contains exactly `get_claim`, `get_policy`, `get_claim_history`, `calculate_payout`, `record_decision` and `pay_claim`. Document search and section reads belong to the context service, not the app API. The API document accepts the key or handler sign-in, has no deployment address or key tool input, and excludes handler actions, runs, evaluations and improvement. The Gateway-refusal hold operation is also excluded. In the agent's model surface, `pay_claim` is a graph operation, not a model tool.

Judge and evaluation reads use the decision record's `section` field. Their existing scoring behavior is unchanged.

The backend can serve the built dashboard and API at one address as an ordinary web server or through a Lambda handler. Lambda invocation does not run startup seeding or write local files; Supabase holds the existing records. Laptop development remains supported, and this build creates no cloud resources.

`npm run build` writes `dashboard/dist` using `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` from root `.env` or the process environment; tests never read root `.env`. Lambda uses `backend.main.handler`, with `DATABASE_URL`, `VITE_SUPABASE_URL` and `UNDERCOVER_API_KEY`; its database connects through Supabase's transaction pooler on port 6543.

The context service runs locally at `http://127.0.0.1:8006/mcp`. Its separate Lambda package contains `context/` and the `context` dependency group's Linux/Python 3.12 packages, with handler `context.server.handler`, behind a Function URL using AWS_IAM and an empty application environment. The app package does not include context code; backend imports/requests do not load `context/`, and seeding is the only backend path that reads its source files.

## Our records

Operational claims, policies, customers, handlers, and company settings are in `data/`, described in `data/README.md`. The seven governance sources and canonical plan/document inputs are in `context/`; legacy plan and document copies under `data/` remain but are not product inputs. The held-out claims in `data/evals/` are for checking the agent later and stay out of the queue.

Seeding appends the security claim, policy and customer in `data/security/` to the original 25 operational claims. Its handler decision is not seeded, and `data/claims.json` stays unchanged.

## Not for now

- No self-service sign-up. Supabase Auth sign-in identifies the existing handlers.
- No real payments.
- No messages to customers.
