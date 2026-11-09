# Durable state implementation plan

## Outcome

Move UnderCover's records to Supabase without losing the offline test setup, and turn the claim agent into a durable LangGraph graph. A claim will use the business payout rules, be traced in one Langfuse session, pause for handler sign-off, resume payment afterward, and survive a process restart without a new attempt or a duplicate decision or payment.

The queue agent remains a LangChain `create_agent`. The backend remains responsible for claim state, the approval limit and every payment guard. The dashboard remains the same product except that sign-off now hands work back to the agent instead of paying directly.

## Course alignment and scope

- Follow the style of `labs/week2day3.ipynb`: a hand-written synchronous `StateGraph`, explicit state/nodes/edges, `ToolNode`, `PostgresSaver`, `interrupt()`, `Command(resume=...)`, and `durability="sync"`. Where the lab and the brief differ, the brief wins.
- Bring in the payout rules and Langfuse tracing from `labs/week2day1.ipynb` first. Move `labs/week2day1_payout.py` into the backend rather than importing code from a notebook folder at runtime.
- Keep the existing section-level document tools, capped queue, to-do list, two-error handoff and named-claim runs. Do not add handler rejection, real payments, customer messages, sign-in or evaluation access.
- Leave `docs/PLAN.md` and `docs/PLAN_HARNESS.md` unchanged. Tick criteria below only after their evidence exists.

## Design

### Payout rules and tracing

Add a claim-scoped backend payout operation:

| Operation | Required input | Result |
|---|---|---|
| `POST /api/claims/{claim_id}/payout` | Benefit, covered payout lines and third-party compensation | Ordered calculation, exact payout and `needs_sign_off` |

The backend looks up the claim's policy, plan, add-ons and approval limit from its own records. It does not trust a model-supplied policy or limit. Preserve the lab's line types and rule order: per-item/electronics/cash/hotel/receipt-free-meal limits, third-party compensation, applicable deductible, benefit or trip-cost maximum, and a zero floor. Use the limit in the seeded `company.json`, not the lab's hard-coded constant. API request fields, including zero/default values, are required.

Replace `calculate_total` with a `calculate_payout` claim-agent tool that calls this operation and returns the full calculation. Record the calculation and result in claim activity. The model still decides coverage and which lines are eligible; code calculates the money.

Trace each claim with Langfuse's LangChain `CallbackHandler`. Use the current `propagate_attributes(session_id=...)` pattern, with a stable session name containing the claim id and claim thread id. Apply the same session and callback configuration to the initial graph invocation, crash recovery and sign-off resumption so the work before and after a pause belongs to one session. Flush after a live invocation so verification does not depend on a later process exit. Missing Langfuse keys disable tracing cleanly in tests; tests use a fake callback/client and no network.

### One data layer for SQLite and Supabase

Replace raw SQLite statements with SQLAlchemy Core tables and queries shared by both dialects. Preserve all existing tables and behavior, including document sections/reads, runs, to-dos, attempts and activity.

- With no `DATABASE_URL`, use `DATABASE_PATH` or the existing local SQLite path. Tests always use isolated temporary SQLite files and never load `.env`.
- With `DATABASE_URL`, use the Supabase session pooler. Create/use the `undercover` schema and set Postgres `search_path=undercover` on the connection, leaving table names unqualified in shared Core queries. SQLite uses the same unqualified tables.
- Configure each SQLAlchemy process with `pool_size=2` and `max_overflow=2`, so its pool never exceeds four connections. The agent's synchronous checkpointer uses one separate connection per agent process.
- Store JSON with SQLAlchemy's portable JSON type and money/timestamps with explicit API serialization. Use transactions, uniqueness and conditional inserts/updates to make repeated steps safe on either database.
- Add durable `agent_thread_id` to the claim's work state, plus `signed_off_at` and `paid_at`. Retain `signed_off_by`; a paid claim response therefore exposes the amount, signing handler, sign-off time and payment time together.

Add an explicit `npm run seed` command. With services stopped, it resets the selected database, including runs, decisions, payments, activity and old checkpoint state, then recreates and seeds exactly the 25 approved queue claims. Postgres reseeding recreates only the `undercover` schema; SQLite reseeding recreates the local file/tables. Normal service startup may create missing tables but must never erase existing work. The checkpointer creates its own tables in `undercover` through `PostgresSaver.setup()`.

### Claim graph and payment authority

Use a typed `ClaimState` with `add_messages`, claim id, model-call count, structured decision fields, backend claim status and final result. Build this flow:

`START → agent ↔ tools → decide → record_decision → END / approval / pay → END`

- `agent` calls the tool-bound model; `tools` is a `ToolNode` containing claim read, document search/read and payout calculation. Tool/backend refusals return as tool messages so the model can correct itself. Record graph and tool activity on the claim.
- `decide` obtains the existing decision kind, payout, short reason and policy citation through structured output. `record_decision` is a separate node that calls the backend and stores the returned claim status in graph state. Separating these nodes means a replay after a crash need not generate a new decision.
- A decline or referral ends as decided. For an approval, the conditional edge uses only the backend response: `in_progress` goes directly to `pay`; `waiting_for_sign_off` goes to `approval`. The graph never compares the payout with the limit itself.
- `approval` calls `interrupt()` with the claim and held payout. Put no business side effect before the interrupt because LangGraph restarts that node from its beginning on resume. Once the backend has saved sign-off, resume with `Command(resume=...)` and continue to `pay`.
- `pay` calls the existing backend payment operation for every approval. The model has no `pay_claim` tool, and the dashboard does not call payment after sign-off. Backend validation remains authoritative even if a graph node is replayed or called incorrectly.
- Count all model calls and stop the tool loop at 30, returning control to the existing failure/retry handling rather than allowing an unbounded run.

Use the synchronous checkpointer and `durability="sync"` on every invoke. With Supabase, configure `PostgresSaver` as in the lab: synchronous psycopg, `autocommit=True`, `prepare_threshold=0`, `dict_row`, `search_path=undercover`, and `setup()` before compiling/running. Tests use LangGraph's SQLite checkpointer against isolated local state so they can rebuild a graph without network access while exercising the same nodes and resume semantics.

### Interruption, failure and idempotency

These are deliberately different paths:

| Event | Claim/to-do afterward | Next invocation |
|---|---|---|
| Process stopped or Ctrl-C before completion | Remains `in_progress`; attempts unchanged; existing thread retained | Rebuild the graph and call `invoke(None, ...)` with the same thread to continue from the latest checkpoint. |
| Approval waiting for a person | `waiting_for_sign_off`; its selected to-do is done; checkpoint and thread retained | After saved sign-off, use the same thread and `Command(resume=...)`; attempts remain unchanged. |
| Caught model/provider/tool failure before completion | Existing first/second-error behavior; claim returns to waiting or left for handler | A retry is a new attempt with a new thread and fresh claim state. |

A repeated decision that exactly matches the stored decision, payout, reason and citation returns 200 and the current claim without another write or activity entry. A different decision remains 409. A repeated payment for the already-paid amount likewise returns the claim as done; a different amount remains 409. Replaying either node therefore cannot duplicate its business effect. Repeating completed work still returns the stored to-do result without calling a model.

### Agent service, run recovery and sign-off

Add a small synchronous FastAPI service in `agent/service.py`, with a health operation and required-path operations to run a stored run and resume a stored claim. It reads the run, claim thread and saved sign-off from the backend rather than accepting replacement workflow state.

- `npm run dev` starts backend on 8000, agent service on 8002 and the dashboard on 5173 using cross-platform `uv run`/`.mjs` scripts. The backend and agent receive `.env` at process entry; tests do not load it.
- Backend run routes create the durable run, then notify `UNDERCOVER_AGENT` (default `http://127.0.0.1:8002`) in a background task. `uv run -m agent` remains a thin way to start a run through the same entry point, not a second orchestration implementation.
- Sign-off validates and commits the eligible handler, `signed_off_by`, `signed_off_at` and `in_progress` state before notifying the agent service. It returns without waiting for payment. Notification failure does not roll back sign-off or fabricate payment.
- On service startup and before each run, recover claims left `in_progress` with a saved thread, including signed-off claims. Resume an unfinished existing run and its existing to-do plan before planning more work. A new start while that run is stopped resumes it rather than creating a competing active run; its run id, queue thread, cap and attempt counts remain intact.
- The queue agent keeps `create_agent` and uses durable to-dos to distinguish an existing plan from a new one. Held claims already marked done are not reworked; their later sign-off resume changes payment, not their attempt count.

All new backend and service operations retain unique operation ids, one-line docstrings, typed responses and no optional request inputs.

## Phase 1: Backend payout rules and Langfuse sessions

Port the lab payout rules into a tested backend module, expose the claim-scoped payout operation and connect it to the existing claim agent. Add tracing around the current claim invocation before changing its orchestration.

### Success criteria

- [x] Backend payout tests cover each line category, Electronics Plus, multiple travelers/nights, third-party compensation, applicable and non-applicable deductibles, benefit/trip-cost caps, negative-to-zero handling, cent rounding, and exactly-at versus over the seeded approval limit.
- [x] Known queue examples produce the expected calculations without reading evaluation data: `CLM-1001` returns $420.00, `CLM-1003` returns $3,750.00 with `needs_sign_off=true`, and the passport and baggage examples apply their stated limits and offsets.
- [x] The payout operation uses the claim's stored policy and `company.json` limit, returns typed calculation steps, rejects unknown claims/invalid lines, and adds useful activity. OpenAPI tests confirm required inputs, a unique operation id and a docstring.
- [x] A stub-model claim run calls `calculate_payout`, records the returned amount and no longer exposes `calculate_total`; the agent has no direct reads of lab or business files.
- [x] A fake Langfuse client/handler proves that the callback is attached and that the session id contains the claim id and supplied thread id. Two invocations of the same claim/thread retain the same session, while a fresh attempt's new thread is distinguishable.
- [x] With `DATABASE_URL`, Langfuse keys and OpenRouter keys absent, the payout and tracing tests run entirely on SQLite/fakes with no network or model call.
- [x] `npm run check:python` passes Ruff, Pyright and all tests with at least 80% coverage.

## Phase 2: SQLAlchemy Core, Supabase selection and idempotent records

Move the existing persistence and workflow queries to SQLAlchemy Core, add the explicit seed/reset path and make decision/payment replay safe before introducing graph recovery.

### Success criteria

- [x] Existing backend tests still pass against isolated SQLite: 25 seeded claims, complete bundles, document reads, handlers, runs/to-dos, decisions, sign-off, payments and activity retain their current shape except for the new nullable thread/timestamp fields.
- [x] Configuration tests prove that no `DATABASE_URL` selects SQLite without network, while a Postgres URL selects the session-pooler connection with `search_path=undercover`, synchronous operation and a maximum of four pooled connections per process.
- [x] Shared Core queries contain no SQLite-only `?` placeholders or Postgres-only SQL; JSON, generated ids, numeric money and timestamps round-trip through the API correctly.
- [x] Reseeding twice is intentional and deterministic: each seed leaves exactly 25 waiting claims and empty decisions, payments, sign-offs, runs, to-dos, reads and activity. A separate test proves ordinary initialization does not wipe existing work.
- [x] Repeating an identical decision or paid amount returns 200 and the unchanged claim, with one business record and one original success activity; a changed decision/payment returns 409 without altering the original.
- [x] The backend still refuses over-limit payment without sign-off, wrong amounts, payment after decline/referral and sign-off by an unknown or ineligible handler.
- [x] The Supabase schema/reset logic is covered by SQL construction/fake-connection tests offline; no unit or Playwright test contacts Supabase.
- [x] `npm run check:python` passes with at least 80% coverage and `.env` absent.

## Phase 3: Durable hand-written claim graph

Replace only the claim agent's `create_agent` with the designed graph. Add portable checkpoint setup, synchronous durability, the structured decision node and graph-owned payment step.

### Success criteria

- [x] Graph construction tests verify the state, nodes and conditional edges, synchronous checkpointer selection and `durability="sync"` on start, resume and payment paths. The queue agent still uses `create_agent`.
- [x] A stub-model under-limit claim reads its claim/documents, calls the backend payout tool, records one structured decision and pays in the graph's `pay` node. There is no model-facing payment tool, and the result/activity match the backend.
- [x] A stub-model over-limit approval records its decision, returns a LangGraph interrupt, remains `waiting_for_sign_off` and unpaid, and has a checkpoint whose next work is approval/payment rather than a fresh agent start.
- [x] Rebuilding the graph and invoking the held thread with a saved sign-off resumes the same state and pays once; no coverage analysis or attempt increment is repeated.
- [x] A refusal from a tool/backend operation becomes a tool message and is offered back to the stub model; every graph/tool step is visible in activity. A scripted loop stops at 30 model calls and enters the existing failure path.
- [x] Tests replay the record-decision and pay nodes at their checkpoint boundaries and prove one decision, one payment and unchanged sign-off/payment timestamps.
- [x] The test checkpointer survives graph object reconstruction using isolated SQLite, while production factory tests confirm `PostgresSaver`, its required psycopg options and the `undercover` schema without opening a connection.
- [x] `npm run check:python` passes all graph tests with a stub model and at least 80% coverage.

## Phase 4: Agent service and sign-off resumption

Separate agent execution from the backend, make saved sign-off trigger a non-blocking resume, and update the dashboard so it no longer performs the payment itself.

### Success criteria

- [x] `npm run dev` starts backend, agent and dashboard with the documented ports and `.env`; process scripts work on Windows and stop all three together. The backend no longer imports or executes the claim/queue agent in process.
- [x] Agent service run/resume operations have unique operation ids, docstrings and typed responses. A run notification returns immediately, and service tests execute the stored run through stub models.
- [x] A Python integration test drives an over-limit claim to its interrupt, signs off through the backend and calls the real agent service with a stub model/checkpointer. The claim becomes `in_progress` after sign-off, then decided and paid once with the same thread and attempt count.
- [x] Sign-off saves handler id/name resolution and `signed_off_at` before notification; payment adds `paid_at`. Backend responses and activity show who signed off and when, and the signing handler cannot be changed by the resume request.
- [ ] An unavailable agent service leaves the saved sign-off and `in_progress` claim intact, returns the sign-off without waiting for payment, and performs no direct backend/dashboard payment. A later service start recovers it.
- [x] The dashboard button changes to a sign-off-only action, never calls `payClaim`, polls the signed-off `in_progress` claim and displays signing handler/time plus paid amount/time when the agent finishes. Existing claim, queue and instruction views still work.
- [x] Playwright uses a stand-in agent server on port 8003 through `UNDERCOVER_AGENT`; signing off in the browser produces exactly one resume request at that stand-in and never reaches port 8002 or a model. Python tests, not this stand-in, prove actual resume/payment.
- [x] `npm run check:ts` and `npm run e2e` pass with strict TypeScript, no `any`, at least 80% Vitest coverage and no external service calls.

## Phase 5: Restart recovery and interrupted queue completion

Add the shared recovery pass used at agent startup and before every run, and prove the distinction between process interruption and a real failed attempt.

### Success criteria

- [ ] A stub-model test stops a claim after its first completed graph steps, rebuilds the graph/checkpointer and continues with `invoke(None)` on the same thread. It resumes from the saved next node, keeps attempts unchanged and ends with one decision and at most one payment.
- [ ] A test that raises a provider error instead follows the existing failure path: attempt one returns to waiting/to-do, the retry has a new thread and only fresh initial messages, and a second real error still leaves the claim for a handler.
- [ ] Recovery covers an under-limit approval stopped before payment, a signed-off held claim stopped before resume/payment, and a claim whose decision/payment was saved just before the stop; replay changes no decision, amount, handler or timestamps.
- [ ] A stopped queue run resumes its existing run id, queue thread, plan and cap; remaining to-dos are worked, completed/held items are not duplicated, and the run can then finish with its summary. No second active run is created.
- [ ] Service startup and `uv run -m agent` both perform recovery before new work. A claim signed off while the agent was down is recovered without another sign-off or attempt.
- [ ] Repeated run, work, decision, payment and resume requests cannot produce a second decision/payment. Other claims and claims outside the run cap remain unchanged.
- [ ] Automated tests explicitly clear hosted/model environment variables and use temporary SQLite business/checkpoint state. With `.env` temporarily moved away and restored afterward, `npm run check` is green and makes no Supabase, Langfuse or OpenRouter connection.
- [ ] Both coverage gates remain at least 80%, all browser tests use the port-8003 stand-in, and no test threshold, lint rule or required check is weakened.

## Phase 6: Documentation and hosted five-claim verification

Update the operating documents, then prove the integrated product on Supabase, Langfuse and the default model. Any real-model run starts from a fresh seed and has a cap of at most five claims.

### Success criteria

- [ ] `docs/SPEC.md` describes durable claim work, graph-owned payment, sign-off resume and restart recovery in its queue section; its Rules section is unchanged. `docs/TECH_STACK.md` describes SQLAlchemy Core, Supabase, the two agent styles, Postgres checkpoints, Langfuse and the separate service.
- [ ] `docs/PROCESS.md` documents the three development processes, `npm run seed`, offline checks and Windows behavior. `docs/STATUS.md` has current setup, required keys, `UNDERCOVER_AGENT`, run/reseed/recovery instructions and verification results. The agent structure in `docs/CODE_STANDARDS.md` includes the service as its fourth part and graph nodes in place of middleware.
- [ ] After stopping services and running `npm run seed` with the lab's `DATABASE_URL`, Supabase MCP inspection shows the product and checkpointer tables only in `undercover`, exactly 25 waiting claims and no held-out/evaluation records. Starting services does not reseed them.
- [ ] With `MODEL_NAME` temporarily removed, a real DeepSeek V4.1 Flash run capped at five works exactly five claims and finishes with a summary; the other 20 remain waiting. No claim is decided or paid twice, and no unfinished claim remains incorrectly marked.
- [ ] During that run, stop the agent after a claim's first saved steps and start the next run. The claim and stopped queue resume with their original threads and unchanged attempts, complete from the saved state, and record only one decision/payment for the claim.
- [ ] An over-limit claim from the run waits unpaid for sign-off. Through the Playwright MCP browser, sign it off as an eligible handler; it shows `in_progress` between sign-off and payment, then paid within five seconds with the handler, sign-off time and paid time. If the capped plan did not select an over-limit claim, use a subsequent named run for this check without exceeding its cap of one.
- [ ] During the live work, a read-only Supabase MCP query of `pg_stat_activity` for user `postgres` reports no more than 12 connections. Backend and agent pool/connection settings remain within their per-process limits.
- [ ] Through Langfuse's API, the held claim's claim/thread session contains traces timestamped both before and after sign-off, including the graph's payment completion. No evaluation dataset or handler answer key is used.
- [ ] The final Playwright MCP review covers queue, run/to-dos, claim activity/sections, sign-off/payment details and both instruction sets at desktop/mobile sizes with no console errors or horizontal overflow.
- [ ] Restore the student's original `MODEL_NAME` override, stop the live services, and run `npm run check` again with `.env` moved away and safely restored. Ruff, Pyright, pytest, Prettier, ESLint, tsc, Vitest and Playwright all pass without network/model calls.

## Build checkpoints

Implement the phases in order. After each phase, run its focused checks, tick only verified criteria, update `docs/STATUS.md`, and commit with the pre-commit hook enabled. Keep automated tests on stubs and local SQLite, never push, and never create hosted resources beyond the requested Supabase schema/tables. Finish with the complete offline `npm run check` and the live evidence above.
