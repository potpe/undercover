# Pilot implementation plan

## Outcome

Build the first usable claims pilot: a FastAPI/SQLite backend seeded from UnderCover's business data, a LangChain agent that works one claim through backend tools, and a React dashboard where handlers can run the agent, inspect its work and sign off large payouts.

The pilot will use only the policy wording for decisions. It will not use the claims manual, sign-in, real payments, customer messages or either evaluation set.

## Design

### Claim workflow

Use these API states, with the corresponding labels in the dashboard:

| API state | Dashboard label | Meaning |
|---|---|---|
| `waiting` | Waiting | The claim has not been run. Seed data's `open` claims start here. |
| `in_progress` | In progress | The agent is working, or an approved payout of at most $1,000 still needs its payment recorded. |
| `waiting_for_sign_off` | Waiting for sign-off | The agent approved a payout over $1,000; payment is blocked. |
| `decided` | Decided | The claim was declined or referred, or an approved payout has been recorded. |

A run starts the agent in a background task and returns immediately. The backend records an activity entry when the run starts and whenever the agent reads information, records a decision or attempts payment. The dashboard polls the selected claim while it is in progress; no websocket or streaming infrastructure is needed for the pilot.

The agent will have backend HTTP tools to read the full claim bundle, read the policy wording, record a decision and record a payment. The full claim bundle contains the claim, policy, plan and add-ons, customer and previous claims. The agent fetches its instructions from the backend before creating the LangChain agent. It never reads business files directly.

A decision contains `approve`, `decline` or `refer`, a required USD payout, a two- or three-sentence reason and the relevant policy section. Declines and referrals use a zero payout. Payment is a separate operation that takes the amount and requires it to equal the approved payout. The backend, not the prompt, enforces the $1,000 sign-off rule.

### Backend API

Keep the health route and add these operations, each with typed inputs and outputs, a unique `operation_id`, a one-line docstring and no optional request inputs:

| Operation | Purpose |
|---|---|
| `GET /api/claims` | Return the 25-claim queue and current states. |
| `GET /api/claims/{claim_id}` | Return the full claim bundle, decision, payment, sign-off and ordered activity. |
| `GET /api/policy-wording` | Return policy wording version 2026.1 as text. |
| `GET /api/handlers` | Return the handlers who can sign off large payouts. |
| `GET /api/agent/instructions` | Return the agent's current instructions as read-only text. |
| `POST /api/claims/{claim_id}/run` | Start one agent run with a required `thread_id`; reject duplicate or completed runs. |
| `POST /api/claims/{claim_id}/decision` | Record a required decision, payout, reason and policy section. |
| `POST /api/claims/{claim_id}/sign-off` | Record a required `handler_id` for an over-limit approval. |
| `POST /api/claims/{claim_id}/payment` | Record a required payment amount after all payment rules pass. |

Use a local, git-ignored SQLite database initialized from `claims.json`, `policies.json`, `customers.json`, `plans.json`, `handlers.json` and `company.json`. Keep nested claim, policy and customer content in JSON columns where that is simpler, but use typed columns for workflow state, decisions, sign-offs and payments. Tests will use isolated temporary databases. Database initialization must not read `data/evals/`, `data/security/` or `claims_manual.md`.

## Phase 1: Persistent records and read APIs

Build the SQLite store, deterministic seed initialization and the read-only claim APIs. Add typed API models and isolated database fixtures while retaining the health endpoint.

### Success criteria

- [x] `GET /api/claims` returns 200 with exactly 25 claims, `CLM-1001` through `CLM-1025`, all initially `waiting`; no held-out or security claim appears.
- [x] `GET /api/claims/CLM-1001` returns 200 with Maya Patel's complete story, all three claimed items totaling $420.00, all four supporting documents, policy `POL-2001`, the Premium plan and the customer's previous-claim history.
- [x] `GET /api/claims/CLM-1003` exposes the information needed to calculate its $3,750.00 payout: the $3,800.00 medical bill, Premium's $50.00 deductible and the applicable policy data. This verifies the data path, not a handler answer key.
- [x] `GET /api/policy-wording` returns version 2026.1, and `GET /api/handlers` returns Dana, Marcus and Priya. Unknown claim ids return 404.
- [x] Backend tests cover seed counts, full claim content, customer history, plan/add-on resolution, activity order, not-found responses and database reinitialization without duplicate records.
- [x] An OpenAPI test confirms that every operation has a unique operation id and docstring, and that all request-body fields are required.
- [x] `npm run check:python` completes with Ruff, Pyright and all Python tests passing, with total Python coverage at least 80%.

## Phase 2: Decisions, sign-off and payment guard

Add the decision, sign-off and payment operations, their state transitions and activity records. Keep payment recording entirely inside the backend so neither the agent nor the dashboard can bypass the approval limit.

### Success criteria

- [x] Recording an approval of at most $1,000 leaves the claim `in_progress`; recording the exact payout then makes it `decided` and stores the paid amount. A decline or referral has a zero payout and immediately becomes `decided`.
- [x] Recording an approval over $1,000 makes the claim `waiting_for_sign_off`, whether or not the agent subsequently attempts payment.
- [x] In an isolated API test, `POST /api/claims/CLM-1003/decision` records an approval for $3,750.00, and `POST /api/claims/CLM-1003/payment` with `{"amount":3750.00}` returns 409 before sign-off and records no payment.
- [x] Signing off `CLM-1003` with `{"handler_id":"HND-01"}` records Dana's sign-off without paying; the subsequent payment call with the same amount returns 200, makes the claim `decided` and records $3,750.00.
- [x] Tests prove that exactly $1,000 needs no sign-off, any amount above $1,000 does, an unknown or ineligible handler cannot sign off, and a payment cannot differ from the approved payout.
- [x] Tests reject decisions on completed claims, payment without an approval, payment for declined or referred claims, negative payouts and duplicate payments. Existing decisions and payment records remain unchanged after rejected calls.
- [x] Every accepted or rejected agent action adds a clear activity entry, including a blocked over-limit payment attempt.
- [x] `npm run check:python` passes all format, lint, type and unit checks with coverage at least 80%.

## Phase 3: The claim-deciding agent and run orchestration

Replace the hello-world tool with the claim tools and a policy-grounded instruction set. Serve those instructions from the backend so both the agent and dashboard use the same read-only source. Add the background run operation and connect `agent.run(claim_id, thread_id)` to it.

The instructions will require the agent to read the complete claim and wording, apply covered-trip dates, required documents, waiting periods, exclusions, add-ons, per-item and benefit limits, third-party compensation and deductibles. It will refer when required evidence is missing or the wording does not settle the claim. It will record exactly one decision and request payment only for an approval of at most $1,000.

### Success criteria

- [x] `GET /api/agent/instructions` returns the complete instructions used by `create_agent`; the instructions mention only the policy wording and provide no access to evaluation answers or the claims manual.
- [x] Agent tools reach claim data, instructions, decisions and payments only through the backend HTTP API. The agent package contains no reads of `data/` and no `check_backend` hello-world tool.
- [x] `run` retains the public two-argument entry point, uses `openai/gpt-6-luna` with low reasoning effort by default, honors `MODEL_NAME`, and passes the supplied claim id and thread id to the agent.
- [x] A stub-model integration test scripts tool calls for `CLM-1001`: read claim, read wording, approve for $420.00 with a short reason and section 3 citation, then record payment. The final API state is `decided`, the payout and paid amount are $420.00, and the activity is in call order. No real model is called.
- [x] A second stub-model test approves `CLM-1003` for $3,750.00 without requesting payment. Its final state is `waiting_for_sign_off`, and a direct over-limit payment attempt remains blocked by the backend.
- [x] `POST /api/claims/CLM-1001/run` with a required thread id returns 202 with `in_progress`; duplicate runs and runs for decided claims return 409. A failed run adds a visible failure activity and makes an undecided claim `waiting` again without erasing an already-recorded decision or sign-off.
- [x] `npm run check:python` passes with all agent tests using a stub model and total Python coverage at least 80%.

## Phase 4: Branded queue and read-only instructions

Build the dashboard shell, typed API client, claims queue and instructions view. Use React state for navigation rather than adding a router. Apply the UnderCover brand: Instrument Sans, JetBrains Mono for identifiers and amounts, the specified light and dark surfaces, rounded panels, accessible status colors and plain US business English.

### Success criteria

- [x] The queue shows all 25 claims with claim id, customer, claim type, summary, claimed amount and current labeled state; money uses USD formatting such as `$1,250.00`.
- [x] The instructions view shows the same text returned by `GET /api/agent/instructions` and has no editing control.
- [x] Loading and failed-request states are visible and understandable, and repeated renders do not issue duplicate requests.
- [x] Vitest component tests cover queue rendering, all four state labels, currency formatting, API failures and instructions rendering with mocked HTTP responses. TypeScript remains strict with no `any`.
- [x] Playwright end-to-end tests open the real dashboard and backend, verify the 25-claim queue and read-only instructions, and make no model calls.
- [x] With the Playwright MCP browser at desktop and mobile sizes, check that the queue and instructions are readable, navigation and focus states work, text has sufficient contrast, and there is no horizontal overflow.
- [x] `npm run check:ts` and `npm run e2e` pass, including Vitest coverage of at least 80%.

## Phase 5: Claim workspace, live activity and handler sign-off

Add the selected-claim workspace and all handler actions. Show the customer's full story, claimed items, documents, policy and plan, previous claims, current decision and payout, reason, policy section and chronological activity. Let a handler start a waiting claim, watch its progress and sign off an eligible large payout.

### Success criteria

- [ ] Selecting `CLM-1001` in the Playwright MCP browser displays its complete story, three items, four documents, policy and customer history without truncated or missing content.
- [ ] The Run agent button calls the run operation once, disables duplicate starts, updates the state to In progress and polls for activity and decision changes until the claim leaves `in_progress`.
- [ ] Decided claims show the decision, payout or zero amount, two- or three-sentence reason and relevant policy section. Referred and declined claims have no payment action.
- [ ] An over-limit approval shows Waiting for sign-off and no way to record payment before sign-off. Choosing Dana and confirming sign-off performs the sign-off and separate payment requests, then shows Decided and the recorded payout.
- [ ] Payment errors keep the claim and its recorded decision visible and show the backend message; the UI never treats a rejected payment as paid.
- [ ] Vitest tests cover full-detail rendering, run/polling behavior, each decision kind, successful sign-off and rejected over-limit payment using mocked APIs.
- [ ] Playwright end-to-end tests prepare deterministic decisions through backend API calls, never a model, then verify claim details, decision rendering, activity and the complete over-limit sign-off flow in the browser.
- [ ] With the Playwright MCP browser, check the claim workspace at desktop and mobile sizes, including long documents and the sign-off controls; content remains readable, controls are keyboard accessible and money and identifiers use the intended typography.
- [ ] `npm run check:ts` and `npm run e2e` pass with all tests green and TypeScript coverage at least 80%.

## Phase 6: Pilot smoke test and handover

Finish the operating documentation, reset to a clean seed database and run the complete quality gate. Perform one real-model smoke test only as a manual check; all automated tests continue to use stubs.

### Success criteria

- [ ] `docs/STATUS.md` describes the finished pilot, database initialization/reset, `.env` keys, `MODEL_NAME`, `uv sync`, `npm install`, `npm run dev`, the dashboard URL and `npm run check`.
- [ ] A manual smoke run of `CLM-1001` with the configured OpenRouter model completes through the dashboard or `agent.run("CLM-1001", "pilot-smoke")`: it approves using policy sections 3 and 10, records $420.00, gives a two- or three-sentence reason and ends Decided. This is not part of the automated test suite.
- [ ] The over-limit smoke scenario remains blocked until a handler signs off, with the backend API and dashboard agreeing on its state and paid amount.
- [ ] A final code and data-path review confirms that the agent has no direct file access, the claims manual and `data/evals/` were not used, no held-out claims entered the queue, and no real payment or customer-message integration exists.
- [ ] From a clean checkout-style setup, `npm run check` runs every Python, TypeScript and Playwright check successfully; Ruff, ESLint, Prettier, Pyright and tsc pass, both coverage reports are at least 80%, and all end-to-end tests pass without model calls.
- [ ] The final browser smoke check shows the queue, full claim, activity, decision and read-only instructions working together with no browser console errors.

## Build checkpoints

After each phase, tick only the criteria actually verified, update `docs/STATUS.md`, and commit with the pre-commit hook enabled. Build the phases in order, do not push, and finish with the full `npm run check` result.
