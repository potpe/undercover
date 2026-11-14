# Security and Lambda readiness implementation plan

## Outcome

Prepare the existing claims platform for a public deployment without deploying it. Add agent authentication, screen every freshly started claim for hidden instructions before the claim model can see it, expose exactly the eight agent operations needed by the future AgentCore Gateway, make handler sign-off pay atomically, and package the backend and built dashboard for AWS Lambda while preserving laptop development and ordinary web-server operation.

The four build phases correspond to the brief's four steps and must be completed in order. Automated tests continue to use isolated SQLite, local stand-ins and stub models. Nothing in this build creates or changes AWS resources, runs the deployment notebook, moves the agent onto Gateway tools, or changes the claim agent's versioned instructions.

## Course alignment and scope

> SIDENOTE: For consistency with the course, please read `labs/week3day2_lab1.ipynb` and build the check in step 2 in the same style as the lab, and read `labs/week3day2_lab2.ipynb`, which deploys the app, without running it. Where the lab and this document differ, follow this document. Include this sidenote in your plan too.

Both notebooks have been read in full and neither was run. Reuse Lab 1's `SCREEN_INSTRUCTIONS`, its policy-record-plus-claim JSON prompt, its `Screening` schema with `reason` before `instructions_found`, and Structured Outputs. Keep the product requirements where they extend the notebook: the check is a durable graph node, records real usage and activity, refers injected claims, and has explicit retry/failure behavior.

Follow Lab 2's deployment contract without executing it: `npm run build` writes `dashboard/dist`, the Lambda entry point is `backend.main.handler`, an ordinary server runs `backend.main:app`, the package contains `backend/`, `data/`, `dashboard/dist/` and exactly the `server` dependency group's Linux Python 3.12 packages, and the deployed environment contains only `DATABASE_URL`, `VITE_SUPABASE_URL` and `UNDERCOVER_API_KEY`. The app supplies the agent OpenAPI document without `servers`; the later deployment adds its address.

Read `labs/week3day2_product_SECURE.md` as the controlling brief. Leave earlier plans unchanged and tick only criteria in this file that have evidence. Do all live checks together at the end, after one reseed on the default model.

## Current-build facts to preserve or adapt

- Authentication is in `backend/auth.py`, not `backend/dependencies.py`. Claim backend calls and the graph are in `agent/main.py`; the dispatcher and service share its client factory, while `agent/improver_api.py` currently creates its own clients. Put request-time key handling at these actual boundaries rather than assuming the reference branch's `agent/connection.py` and `agent/api.py` files.
- The CLI currently calls the dashboard's handler-protected `/api/claims/{claim_id}/run` operation. Do not weaken that operation to let a key impersonate a handler. Give the CLI a key-protected internal named-work start operation and then invoke the existing claim graph directly, printing its result. The dashboard operation remains handler-only.
- `dashboard/src/api.ts` currently attaches the bearer token only when `RequestInit` is supplied. Make every dashboard request, including parameterless GETs, carry the current token; add a regression test before protecting all dashboard reads.
- The existing bundle route supplies claim, policy, plan and customer history together. Preserve that dashboard behavior while adding typed policy and customer-history read views needed for the required eight-operation agent document. Align the existing operation ids to `search_documents`, `read_section` and `pay_claim` rather than maintaining a second, hand-written API definition.
- Evaluation creation currently selects all operational claims, not only labelled ones. Filter evaluation sources through the backend-held handler decisions so unlabelled `CLM-1901` cannot enter either whole or tiny evaluation sets. Do not open or change files in `data/evals/`.
- Playwright calls agent/internal routes directly from the specs and `e2e/start-agent-stub.ts`; there is no reference `e2e/api.ts` yet. Add a small fixed-key helper for those service-side calls and update the actual sign-off spec in `e2e/claims.spec.ts`. Browser traffic still authenticates as the signed-in handler.
- The current non-Lambda Postgres engine allows two connections (`pool_size=1`, `max_overflow=1`). Preserve that behavior and the separate four-connection synchronous agent checkpoint pool. Only the Lambda backend switches to `NullPool`.
- The `server` dependency group already exists. Keep it aligned with imports reached from `backend.main`; do not replace it with the project's full notebook/agent dependency set or run a destructive `uv sync --only-group server` in the project environment.

## Design

### 1. Agent key and authorization boundaries

Add three small backend authentication boundaries in `backend/auth.py`:

- **Agent only:** read `X-API-Key` directly from the Starlette `Request`, compare it with `UNDERCOVER_API_KEY` using `secrets.compare_digest`, and return 401 for a missing, wrong or unconfigured key. Reading the header from `Request`, rather than declaring a FastAPI `Header` parameter, keeps it out of OpenAPI tool inputs.
- **Agent or handler:** accept a valid key or a valid signed-in handler and return 401 when neither is present. A handler can still use a shared read when the backend has no agent key configured. A key by itself never produces a handler identity or handler permissions.
- **Handler only:** retain `current_handler`/`senior_handler` for every dashboard action and dashboard-only read. Senior permission checks remain separate from authentication.

Apply an explicit route inventory, with tests so a new route cannot silently fall into the wrong category:

| Category | Routes |
|---|---|
| Open | `GET /api/health` only |
| Agent key | All `/api/internal/**` operations; current/versioned agent configuration and current improver recipe; payout, decision, payment, failure, activity, model-call and work-period writes; document search/section operations; the CLI's internal named-work start |
| Key or signed-in handler | Queue and claim-bundle reads, the new policy/history reads, and `GET /api/agent/openapi.json` |
| Signed-in handler | Every other dashboard route, including configuration/recipe/settings/round/me/handler reads, dispatch/evaluation reads and starts, named dashboard runs, sign-off, proposal decisions and settings changes |

For the agent side, construct the key header when each backend client is created for a request, after late `load_dotenv` calls have run. Reuse that boundary from `agent/improver_api.py` so claim tools, dispatcher selection, service recovery/orchestration, the improver and the CLI all send the same header. Never log or print the key. The backend has no mandatory-key startup check; the agent service's lifespan fails immediately with a clear `UNDERCOVER_API_KEY is missing` error before attempting recovery when it is absent.

Set a fixed test key in backend/agent fixtures and Playwright's backend environment. Migrate existing agent-side test calls to send it while retaining independent handler tokens, so tests can prove that the two credentials have different authority.

### 2. Durable hidden-instruction screening

Add the screen as the first node of the existing hand-written claim graph in `agent/main.py`, outside `configuration_versions` and outside the improver's proposal schema. It uses the lab's prompt and response model verbatim in substance:

- Fetch the claim bundle through the backend because the initial message only names the claim. Render the policy record and complete claim as JSON, including the story, every item and every supporting document, in the same form as the lab. No handler decision or evaluation label is included.
- Default to a separate `ChatOpenAI` checker on `openai/gpt-6-luna` with `reasoning_effort="none"`. In the one-model configuration represented by `MODEL_NAME`/`default`, use the pinned configured model and omit reasoning effort. Derive this from the existing configuration path rather than adding another independent `MODEL_NAME` read, so resumes and evaluations retain their pinned model.
- Call `with_structured_output(Screening, include_raw=True)`. Save OpenRouter-reported token usage and `usage.cost` for every raw response, including each parse retry, through the existing claim model-call ledger and current `run_id`. Reuse the existing scoreboard aggregation without estimates.
- Retry an unreadable structured answer up to three attempts in total. Raise after the third unreadable answer. Propagate model errors/timeouts into the existing claim failure and transient-retry policy; never continue to the claim model after a failed screen.
- Record the screening result as the fresh claim's first activity. Move the existing `Using model ...` activity to the first claim-model node so it cannot precede the screen. A positive result records `decision="refer"`, `payout=0`, the screen's reason and `policy_section="Security check"`, then ends the graph without invoking the claim model. A negative result continues along the existing agent/tools/decision path.

The completed screen node is checkpointed before the claim-model node. Resume from saved progress, including after a restart or sign-off, therefore does not screen again. A new thread that starts again from its first message has no completed screen node and is screened again. Existing claim-model call limits count claim-model calls, not a hidden way to bypass the required screen.

Extend seeding to append `data/security/claims.json` beside the unchanged `data/claims.json`, and add its policy and customer from `data/security/`. Do not read or seed `data/security/decisions.json`. The queue becomes 26 waiting claims, with `CLM-1901` included, while the two handler-label sets remain unchanged. Filter evaluation sources by their existing backend labels so `CLM-1901` is absent from evaluation and held-out runs, whole and tiny.

### 3. Agent-only OpenAPI and sign-off payment

Generate `GET /api/agent/openapi.json` from the FastAPI app's own schema, deep-copy it, filter its paths to exactly these operation ids, and remove any root `servers` entry:

`get_claim`, `get_policy`, `get_claim_history`, `search_documents`, `read_section`, `calculate_payout`, `record_decision`, `pay_claim`.

Add typed `GET /api/policies/{policy_id}` and `GET /api/customers/{customer_id}/history` views from the existing records, retaining the dashboard's combined `get_claim` response. Rename the existing document/payment operation ids to the required tool names. Preserve each operation's one-line docstring and required inputs, especially the payment body's `amount`. The key-checking dependencies must add no header/query/body fields to the filtered document. The document endpoint itself uses the shared key-or-handler boundary and is not one of the eight operations.

Split public agent payment from backend-owned sign-off payment:

- `pay_claim` first reads the current approval limit from settings. If `amount` exceeds it, return 403 with a detail naming the formatted limit before looking up the claim, so approved, unapproved and unknown claim ids all receive the same over-limit answer. Amounts within the limit retain the existing decision, amount-match, idempotency and state checks.
- `sign_off_claim` keeps the signed-in/senior handler checks, records sign-off and pays the approved amount in one backend transaction using an internal payment helper that requires the sign-off. It returns the claim as `decided`, with `paid_amount`/`paid_at` and exactly one payment activity. Remove its agent-service background notification; Lambda never needs to call the laptop service for sign-off.
- Keep the graph's `pay` node for ordinary approvals at or below the limit. After the sign-off interrupt, finish with the already-recorded payment and do not call `save_payment`. Existing service resume/recovery may finish an old saved thread, but it must not pay again or call the public over-limit operation.

### 4. Static dashboard, Lambda entry point and database lifecycle

Add root `npm run build` using Vite to write `dashboard/dist`. Configure production and development to read root `.env`, while test mode retains its own explicit environment and never reads root `.env`. Vite must also accept `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY` directly from the process environment on a server with no `.env`. Resolve build warnings rather than suppressing them.

At the end of `backend/main.py`, expose `handler = Mangum(app, lifespan="off")`. When `dashboard/dist` exists, serve its files at their own paths and return `index.html` for `/` and unknown non-`/api/` GET paths so React pages survive refresh. Keep the fallback outside the OpenAPI document, prevent path traversal, and preserve FastAPI's JSON 404 for unknown `/api/` paths. Without the build folder, register no dashboard fallback and leave `npm run dev` unchanged. Lambda performs no startup seed; normal laptop lifespan behavior remains as it is today.

Add a cross-platform `check:server` script along the lines of `uv run --isolated --only-group server python -c "import backend.main"` and include it in `npm run check`. This verifies the exact server group in a temporary environment without stripping `.venv`. The package review must show that `backend.main` reaches no `agent` import, reads business files only relative to the packaged backend/data files, and has no runtime dependency on the local SQLite path, root `.env`, agent service, OpenRouter key or writable filesystem for work that can start on Lambda. Unavailable agent-started work may retain its current defaults and return the existing service-unavailable response.

In `backend/database.py`, select Lambda behavior when `AWS_LAMBDA_FUNCTION_NAME` is present: Postgres engines use `poolclass=NullPool` and psycopg `connect_args` include `prepare_threshold=None` alongside the existing schema search path. Each request's existing `connect()` scope therefore opens and closes its own connection with none retained. Non-Lambda Postgres and SQLite engines remain unchanged.

Update `docs/TECH_STACK.md` with a Deployment section naming the build command/output, package contents, Lambda handler, ordinary ASGI app, exact Lambda environment and transaction-pooler/port-6543 behavior. Update the other product documents as each phase lands.

## Phase 1: An API key for the agent

Implement the authentication dependencies and route inventory, request-time agent headers, CLI separation, dashboard bearer-token fix, service startup guard and test-key plumbing.

### Success criteria

- [x] If `.env` has no `UNDERCOVER_API_KEY`, implementation appends a `secrets.token_urlsafe(32)` value of at least 32 characters without printing it; a presence/length check succeeds and neither the key nor a command containing it appears in logs or the plan.
- [x] Backend tests prove an agent-only route returns 401 with no key, 401 with a wrong key and its normal success response with the right key; `secrets.compare_digest` is used for the comparison.
- [x] Tests prove a shared read succeeds with the key and with Dana's bearer token, returns 401 with neither, and still succeeds for Dana when `UNDERCOVER_API_KEY` is unset.
- [x] A route-inventory test covers every category: all agent/internal writes require the key, all dashboard-only reads/actions require a handler, and `GET /api/health` remains 200 with no credentials.
- [x] Tests prove key-only requests cannot run a dashboard named claim, start a dispatch/evaluation/round, sign off, decide a proposal or change settings. Agent-service internal round creation and proposal-decision records still accept the key and the supplied authorized `handler_id` without treating it as a sign-in.
- [x] With no key configured, the backend starts and handler-only/shared-handler requests work, while agent-only requests are 401. Entering the agent service lifespan without the key raises a clear missing-key error before recovery; with the fixed test key it starts normally.
- [x] A late-`load_dotenv` regression test proves the CLI and each agent backend boundary send `X-API-Key` on every request, including claim tools, dispatcher/service calls and `agent/improver_api.py`; no handler-only route is made key-capable to accommodate the CLI.
- [x] Dashboard unit tests prove parameterless GETs and actions all send the current bearer token. Playwright uses a fixed backend key for every direct agent/internal call and still signs in through the local Auth stand-in for browser calls.
- [x] `npm run check` passes with root `.env` moved away and restored, with no model, hosted Auth or AWS calls and with all existing coverage thresholds intact.
- [x] `docs/SPEC.md`, `docs/PROCESS.md` and `docs/STATUS.md` describe the key, credential boundaries, CLI behavior and test setup. Tick only verified items and commit Phase 1 with the pre-commit hook enabled.

## Phase 2: A check for instructions hidden in a claim

Implement the durable screening node, usage/activity recording, security seed data and label-driven evaluation selection.

### Success criteria

- [x] A model-construction test verifies default Luna/`none` screening and the one-model/default override with no reasoning argument; the checker is not present in versioned instructions, configuration proposals or the improver recipe.
- [x] A stub-screen test supplies a response containing the claim JSON and verifies it includes the complete customer story, items and documents plus the policy record, uses the lab instructions, and declares `reason` before `instructions_found`.
- [x] A flagged stub claim ends as a zero-payout referral with the screen's exact reason and `policy_section="Security check"`; the claim model receives no invocation and no claim content. The dashboard data shows the screening result as its first activity and the referral reason/section on the claim.
- [x] A passing stub claim follows the pre-existing tool/decision/payment path. Tests prove one screen per saved thread across ordinary resume, restart and sign-off, and a new first-message thread screens again.
- [x] Structured-output tests cover one and three unreadable attempts, and an error/timeout: all raw responses with usage are recorded, no failed screen reaches the claim model, three parse failures raise, and existing dispatch transient/ordinary failure behavior remains unchanged.
- [x] Usage tests prove every screen attempt contributes its reported tokens/cost to the claim, run and scoreboard totals, including a flagged claim whose only model call is the checker.
- [x] Seed tests return exactly 26 waiting operational claims including `CLM-1901`, its `POL-2901` policy and Derek Vance customer, while `data/claims.json` and `data/security/decisions.json` are unchanged and no security handler decision is stored.
- [x] Whole and tiny creation tests for both evaluation sets contain no source `CLM-1901`; evaluation-set counts and all five tiny claims remain as before, and seed output/documentation says 26 rather than 25.
- [x] Queue, claim-workspace and Playwright expectations are updated for the 26th claim without weakening existing assertions; no automated test calls a real screening or claim model.
- [x] `npm run check` passes with root `.env` absent and all coverage thresholds intact. Update `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/STATUS.md` and `data/README.md`; tick verified items and commit Phase 2.

## Phase 3: The agent's own API and sign-off that pays

Implement the filtered generated OpenAPI document, required operation names/read views, limit-first payment guard and atomic sign-off payment/resume behavior.

### Success criteria

- [ ] With the key and with a handler token, `GET /api/agent/openapi.json` returns exactly the eight required operation ids and no others; with neither it returns 401. The root document has no `servers` entry and no `X-API-Key` parameter/security-derived tool input.
- [ ] Every included operation retains a one-line description and required inputs; `pay_claim` exposes a required numeric `amount`, and the new policy/history operations return the same records already present in the claim bundle. The full backend OpenAPI rules still pass with unique ids.
- [ ] A payment above the current configured limit returns 403 naming that limit on a claim that was never approved; an unknown claim returns the identical response, proving the guard runs before claim lookup. A test with a changed setting proves the limit is read from settings, and exactly-at-limit payments retain their normal rules.
- [ ] Dana's sign-off of a held operational approval returns `decided` with the approved `paid_amount`, `paid_at`, one sign-off and one payment activity, and no agent-service call. Repeating sign-off cannot create another payment; evaluation-copy sign-off remains forbidden.
- [ ] A graph test resumes a saved sign-off interrupt with `save_payment` instrumented and proves no payment call occurs, the existing paid result is preserved, and a repeated resume is idempotent. Small approvals still use the graph's payment node exactly once.
- [ ] Playwright's held-payout scenario now observes payment immediately after Dana signs off, exactly one recorded payment and no `/claims/{claim_id}/resume` request to the agent stand-in.
- [ ] `npm run check` passes with root `.env` absent and all coverage thresholds intact. Update `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md` and `docs/STATUS.md` for the eight-operation API and sign-off that pays; tick verified items and commit Phase 3.

## Phase 4: Ready for Lambda

Implement the dashboard build, static/SPA serving, Mangum handler, isolated server-group check, Lambda database engine and deployment record. Build and measure the package locally without deploying it.

### Success criteria

- [ ] `npm run build` writes `dashboard/dist` with root `.env` present and again with only process-environment Supabase values and no `.env`; both builds print no warnings. Vitest, Playwright and the complete check continue to use explicit stand-in values and never read root `.env`.
- [ ] A Mangum test invokes `backend.main.handler` with a Function URL event for `/api/health` and receives 200/`{"status":"ok"}` without running lifespan or seeding.
- [ ] With a stand-in dashboard build, Lambda/static tests return `index.html` at `/` and a dashboard deep link, return an asset at its own path, and return JSON 404 for an unknown `/api/` path. With no build folder, current API/404 and development behavior are unchanged.
- [ ] `check:server` imports `backend.main` in an isolated environment containing only the `server` group and leaves the project's `.venv` intact. A package/import review finds no backend-to-agent import, root-`.env` requirement or runtime write for supported Lambda requests.
- [ ] An `AWS_LAMBDA_FUNCTION_NAME` test instruments two sequential requests and finds a newly opened and closed connection for each, `NullPool`, and `prepare_threshold=None`; the existing non-Lambda two-connection engine and SQLite tests still pass.
- [ ] In a temporary folder, export exactly the locked `server` group, install only binary Linux `x86_64` packages for Python 3.12, add `backend/`, `data/` and `dashboard/dist/`, and zip the result. The zip is below 50 MB; record its exact decimal MB size in `docs/STATUS.md` and the final report. No AWS resource is created.
- [ ] `docs/TECH_STACK.md` has the required Deployment section, and `docs/SPEC.md`, `docs/PROCESS.md`, `docs/STATUS.md` and `data/README.md` match the product. `npm run check`, including `check:server`, passes with `.env` moved away and safely restored, with all tests and thresholds green.

## Final one-seed live verification and handover

Run these checks only after Phases 1–4 and the offline gate are complete. Stop all development services, select the default Luna/low configuration rather than `MODEL_NAME`, restore the live `.env`, and reseed exactly once. Do not run either notebook or deploy anything.

### Success criteria

- [ ] One `npm run seed` rebuilds 26 waiting claims. With `npm run dev`, Dana Whitfield can sign in and use the dashboard as before; `curl` without a key to an agent-only route returns 401 and `/api/health` remains open.
- [ ] `uv run -m agent CLM-1001` completes that claim with the key supplied after dotenv loads. A dashboard dispatch then works the rest of the queue, with no claim decided or paid twice.
- [ ] The same dispatch refers `CLM-1901` for the check's stated reason and `Security check` section, visible on its dashboard claim, and its ledger contains no claim-model call. The other claims are decided as before. A tiny evaluation-set run contains only its original five sources and screens all five without flagging any.
- [ ] Dana signs off one held payout on the dashboard after that dispatch; it immediately shows one payment for the approved amount, `decided`, and no duplicate payment after any saved agent resume.
- [ ] After `npm run build`, stop the development processes and start only `uv run -m uvicorn backend.main:app --env-file .env` on port 8000. Through the Playwright MCP server at `http://127.0.0.1:8000`, Dana signs in and sees the claims at `/` and after a dashboard-page refresh, with no browser console errors.
- [ ] Restart the laptop backend with `AWS_LAMBDA_FUNCTION_NAME` set and the live `DATABASE_URL` changed to Supabase's transaction pooler on port 6543. Dana can still sign in and see claims, demonstrating request-scoped unprepared connections; then restore the ordinary local environment and stop the services.
- [ ] Report the recorded cost of the named claim, dispatch and tiny evaluation separately, including screening and judge cost where applicable, using backend/OpenRouter usage records rather than estimates. Record live evidence and any limitations in `docs/STATUS.md`, tick only observed criteria, and make the final Phase 4 commit with the pre-commit hook enabled.

## Risks and controls

| Risk | Control |
|---|---|
| A shared credential dependency accidentally turns the key into a handler | Explicit route inventory and key-only handler-action tests; handler identity continues to come only from verified bearer claims |
| The key becomes a Gateway tool input | Read it from `Request` and assert the filtered document has no key parameter |
| Screening is skipped, repeated on resume, or editable by the improver | First durable graph node outside versioned configuration, with flagged/pass/resume/fresh-thread stub tests |
| Screening cost disappears or a false positive changes scores | Record every raw response in the existing ledger; verify the tiny five live and keep `CLM-1901` out of both labelled sets |
| `CLM-1901` breaks whole evaluation creation because it has no handler label | Select evaluation sources through backend-held labels and test whole/tiny both sets |
| Sign-off and resume pay twice | One backend sign-off/payment transaction, no service notification, no post-interrupt `save_payment`, and idempotency/browser tests |
| A production build or Vitest unexpectedly reads root `.env` | Mode-specific Vite environment directories and repeated no-`.env` offline gates |
| Lambda exhausts Supabase connections or uses unsupported prepared statements | `NullPool`, `prepare_threshold=None`, instrumented unit tests and the local port-6543 check |
| The package works on macOS but not Lambda | Isolated server-group import plus a Linux Python 3.12 binary package built and measured in a temporary folder |

## Build checkpoints

Implement the four phases in order with tests alongside each behavior. After each phase, run its focused tests and the cumulative no-`.env` gate, tick only verified criteria here, update `docs/STATUS.md`, and commit with the pre-commit hook enabled. Never lower a rule or threshold, disable a check, push, open `data/evals/`, run a model in an automated test, or create an AWS resource.

Keep all live checks for the final section, after the single default-model reseed. Finish by reporting the complete offline check results, observed live outcomes, recorded model/judge spend and deployment zip size, then stop and wait for review rather than running Lab 2's deployment.
