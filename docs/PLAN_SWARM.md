# Swarm and scoreboard implementation plan

## Outcome

Replace the model-driven queue agent with a deterministic LangGraph dispatcher that works every waiting or interrupted claim, oldest submitted first, with up to N independent claim agents. Add per-call OpenRouter cost and active-work-time records, a dispatch summary built from backend records, and isolated evaluations with a scoreboard for quality, mistakes, cost and time.

The existing claim graph, tools, instructions, $1,000 sign-off guard, idempotent writes and per-claim checkpoints remain the foundation. The dispatcher owns no claim checkpoint and uses no model. Evaluation copies use the same claim graph but never enter the operational queue, never expose handler answers through claim APIs, and cannot be signed off.

## Course alignment and scope

> SIDENOTE: For consistency with the course, please read `labs/week2day4.ipynb` and build the dispatcher in the same style as the lab. Where the lab and this brief differ, follow the brief. Include this sidenote in your plan too.

The lab has been read. Reuse its patterns: a `StateGraph` with no LLM, a list node, one `Send` per claim, an additive `results` reducer, `max_concurrency=N`, worker `RetryPolicy`, and deterministic scoring. Follow the brief where the product differs: claims retain separate durable threads, the dispatcher has no checkpointer, sign-off interruptions do not pause other claims, traces stay claim-scoped with a run tag, and the backend—not the agent service—reads handler decisions and scores evaluations.

Leave `docs/PLAN.md`, `docs/PLAN_HARNESS.md` and `docs/PLAN_DURABLE.md` unchanged. Tick only criteria below that have evidence. Do not add sign-in, real payments, customer messages, cloud dispatch, a shared claim thread, a dispatcher checkpointer, or another agent framework.

## Design

### Records, ownership and run identity

Evolve the current queue-run store into backend-owned dispatch/evaluation records while retaining the existing claims, decisions, sign-offs, payments and activity.

- A shared run record has a numeric id, kind (`dispatch` or `evaluation`), status (`in_progress`, `completed` or `stopped`), concurrency N, model name, nullable reasoning effort, created/completed/stopped timestamps, and a nullable evaluation set/scope. A stopped run is historical; a later dispatch creates a new run rather than reviving it.
- A run-claim record associates each selected claim with that run, its deterministic order, current outcome and error/result text. This replaces the model-written to-do list; it is not edited by an LLM.
- Claim-level work state retains the durable `agent_thread_id` and adds persistent failed-attempt count. This count must survive a stopped dispatch so a later real error is still the second failure. Rate limits, timeouts and process interruption do not increment it.
- A model-call ledger records claim id, nullable run id, model, nullable reasoning effort, prompt/completion/total tokens, reported USD cost and timestamp. A required nullable `run_id` supports direct claim work without making request fields optional.
- A work-period ledger records claim id, nullable run id, thread id, UTC start/finish timestamps and active seconds. Record work in the harness around every claim invocation, including ordinary failed attempts and retry calls. A process kill can leave its last period unfinished; later work creates another period rather than inventing completion time for the killed process.
- Run totals include only rows carrying that run id, including failed attempts. Post-sign-off work stays with the claim's associated dispatch; direct/named work with no dispatch or evaluation uses `run_id=null`. Each run's wall time is measured from its first work start to its completion, separately from the sum of active claim seconds.

For an operational dispatch, select only non-evaluation claims whose status is `waiting` or `in_progress`, ordered by `submitted_on` and then claim id. Exclude `decided`, `waiting_for_sign_off` and `left_for_handler` claims. A dispatch at N works all selected claims; N limits concurrency, not the number selected.

### Instrumentation and tracing

Change the default to `openai/gpt-6-luna` with `reasoning_effort="low"`. When `MODEL_NAME` is set, use that model with no reasoning effort and record the effort as null, displayed as `none`. Update old DeepSeek fixtures and documentation rather than leaving two defaults.

In both the tool-calling model node and the structured decision node, read OpenRouter's reply usage (`response_metadata.token_usage`, sourced from `usage`) and record its prompt/completion/total tokens and `usage.cost` through a backend operation. Use structured output's raw-response option for the decision call so its usage is not lost when parsing `DecisionRequest`. Do not estimate cost or use a tokenizer in place of OpenRouter's figures.

The harness measures active work with a monotonic clock and stores UTC boundaries for timelines. Initial work, same-thread retries, restarts and sign-off payment each add their own active periods; the interval between an interrupt and a handler's sign-off adds none.

Keep `trace_claim`'s claim/thread session. Add the run id to LangChain config metadata as `langfuse_tags`, using an unambiguous tag such as `dispatch-<run_id>` or `evaluation-<run_id>`. Apply the same tag on initial invocation, transient retry, restart continuation and sign-off resume. Direct work has no run tag. Missing Langfuse keys remain a no-op in automated tests.

### Dispatcher and failure semantics

Add `agent/dispatcher.py` as the fifth agent part. The five parts are the claim graph, dispatcher, checkpoint adapter, tracing adapter and service entry point. Its graph follows the lab:

`START → list_claims → Send(work_claim) per claim → collect/finish → END`

- Dispatcher state contains the run identity, the ordered work items and `Annotated[list[WorkResult], operator.add]`. It is compiled without a checkpointer and invoked synchronously with `max_concurrency=N`.
- The list node reads the backend run's already-selected work items. It does not ask a model to choose or order claims.
- The worker calls exactly one harness function with the claim id and run context. That function is the dispatcher's only route to the claim agent; it begins or continues backend claim work, invokes the existing claim graph on its own thread, records time, and settles the run-claim from the returned backend state.
- Retain per-claim service locks so sign-off resume and dispatch cannot execute one checkpoint concurrently. A dispatch skips a signed-off claim already being resumed and reports it as still in progress rather than starting another invocation.

Use a worker `RetryPolicy(max_attempts=3, retry_on=(ModelRateLimitError, ModelTimeoutError))`, with the lab's backoff behavior. The harness lets these two exceptions escape without failing or settling the claim. A retry therefore rebuilds/continues the same claim thread and attempt from its latest checkpoint.

Attach a worker `error_handler` using LangGraph's `NodeError` context:

| Outcome | Claim afterward | Dispatch behavior |
|---|---|---|
| Claim decided, paid where allowed, or held for sign-off | Terminal for this dispatch; held operational claims remain `waiting_for_sign_off` | Mark its run-claim done. |
| Rate limit/timeout before retries are exhausted | `in_progress`, same thread and failed-attempt count | LangGraph waits and retries the worker. |
| Rate limit/timeout after retries are exhausted | Still `in_progress`, same thread and failed-attempt count | Error handler returns an unfinished result; other claims continue and the summary lists it for the next dispatch. |
| Any other error, first failure | `waiting`, failed-attempt count 1, fresh thread on retry | Error handler records failure and the collector sends that claim once more in the same dispatch. |
| Any other error, second failure | `left_for_handler`, failed-attempt count 2 | Record the final outcome; no third attempt. |
| Agent process killed | Claim and attempt unchanged; old run later becomes `stopped` | No handler runs. The next dispatch selects the interrupted claim and resumes its saved thread. |

The collector may issue one follow-up `Send` only for a run-claim whose first ordinary failure returned `retry`. Finalization asks the backend to build and save a structured dispatch summary from run-claim and claim records: approved/declined/referred counts, every held payout with claim id and amount, every claim left for a handler, and every claim still in progress. An approval held for sign-off counts as approved and also appears in the held-payout list. No model writes this summary.

### Checkpoint connections

Replace per-claim direct Postgres connections with one synchronous psycopg `ConnectionPool` per agent process, configured with `min_size=0`, `max_size=4`, `autocommit=True`, `prepare_threshold=0`, `dict_row` and `search_path=undercover`. Claims waiting for a pool connection wait rather than fail.

Create a separate `PostgresSaver` for each claim invocation on that shared pool, so each saver has its own lock; do not share one saver across the swarm. Run `setup()` once during pool initialization, and close the pool at service shutdown. Keep synchronous psycopg and `durability="sync"` for Windows. SQLite tests continue to use isolated synchronous savers and no hosted connection.

### Service and backend API boundary

Move backend-to-agent HTTP calls into `backend/agent_service.py`. Dashboard-facing backend start routes call the agent service first with N and, for evaluations, set/scope; they create no run record before that call.

- `POST /api/dispatches` requires `concurrency` and returns the service's accepted dispatch or a clear unavailable/409 response.
- `GET /api/dispatches/current` returns the latest dispatch, its ordered run-claims, record-built summary, model/reasoning, cost, active seconds and wall seconds.
- `POST /api/evaluations` requires `concurrency`, `evaluation_set` (`evaluation` or `held_out`) and `tiny` (required boolean).
- `GET /api/evaluations` lists evaluations newest first with all scoreboard measures.
- `GET /api/evaluations/{run_id}` returns its configuration, status, measures and per-claim comparison rows.
- Service-side backend operations create/start/fail/complete runs, record model calls/work periods, and return selected work. Every operation has a unique `operation_id`, a one-line docstring, typed inputs/outputs and no optional request-body inputs; nullable run association fields are still required.

The agent service reserves one non-blocking run slot before creating a dispatch or evaluation. A concurrent second start receives 409 immediately rather than waiting or reusing the active run. After creating the backend record it returns 202 without doing claim work in the request, then executes the dispatcher in a background task and releases the slot only after finalization. Backend unavailability during creation or an unexpected orchestration error must release the slot and leave a diagnosable stopped record where one exists.

On service startup, mark every dispatch/evaluation still recorded as `in_progress` as `stopped`; do not resume those run records. Preserve the claims' own state/threads for the next dispatch, and retain independent recovery of claims already signed off. Dashboard users never call port 8002 directly, and the claim agent receives no run-start tool.

Remove `agent/queue.py`, its middleware/instructions, the actual `backend/queue_instructions.md` file, plan/to-do APIs and table, and the old to-do browser test. `uv run -m agent CLM-1001` remains a thin named-claim entry point with no run; bare `uv run -m agent` and the old queue-cap arguments fail with useful usage text. Supabase reseeding drops the old to-do table with the recreated schema.

### Evaluation isolation and scoring

Seeding additionally loads:

- `data/evals/held_out_claims.json` into a held-out source table outside the queue.
- Both decisions files into a handler-decision table keyed by evaluation set and source claim id.

These records are used only by backend evaluation creation/scoring. No claim read, document tool, prompt, agent-service response or operational dispatch query reads or embeds them.

Creating an evaluation selects all 25 sources or exactly the five whose label has `tiny=true`. It copies each source claim with a fresh unique id and writes that new id into the copy's JSON `claim_id` as well as its primary key. The run-claim retains the internal source-id mapping needed for scoring. Evaluation copies carry their evaluation run id so queue listing and dispatch selection can exclude them while `get_claim` and the existing claim graph can work them normally.

Evaluation copies use the same tools, graph, checkpoints, usage/time records, retries and payout rules. Under-limit approvals may complete the ordinary recorded-payment path; no real money is involved. An approval over the limit remains unpaid and `waiting_for_sign_off`, but its evaluation run-claim counts as finished. Clear the finished evaluation copy's saved claim thread, including this held interrupt, so it cannot later resume. The copy page identifies it as an evaluation and shows no sign-off form; the backend sign-off operation also rejects it.

If the service stops, startup marks the evaluation `stopped` without presenting it as completed. Scoreboard reads still score its selected records deterministically; a copy with no recorded decision is treated as `refer` with payout 0 and a clear no-decision reason.

For each source claim, compare the copy's recorded decision/payout with the handler label. With `agent = refer, payout = 0` for an undecided copy:

- `decision_match`: decision kinds match.
- `right`: decision kinds match and `abs(agent_payout - handler_payout) <= 1`.
- Same decision: mistake cost is the absolute payout difference for an approval, otherwise 0.
- Different decision: agent decline costs $1,500; agent approval costs its payout whether paid or not; agent referral costs $50.
- `decision_accuracy`: mean of decision matches.
- `accuracy`: mean of `right`.
- `cost_of_mistakes`: sum of mistake costs.
- `straight_through`: share whose effective agent decision is not `refer`.
- Model totals come from the run's model-call ledger, failed attempts included; report total model cost and model cost per selected claim.
- Per-claim active seconds include all of that evaluation's work periods and exclude sign-off waits; report average and slowest claim seconds.
- Whole-run seconds use the run timeline from first work to completion/stopping, not the sum of claim times.

Only the evaluation detail API may place handler and agent results side by side for the scoreboard. `get_claim` on a copy never contains the handler decision, payout, reason, tags or other answer-key content.

### Dashboard

Use the existing brand, responsive panels and React-state navigation.

- The queue page replaces Claim cap with “Agents at once,” an integer input from 1 to 25 defaulting to 3, and a “Start dispatch” button. It disables invalid/active starts and displays a specific message if the agent service is offline or another run is active.
- The run panel lists the dispatch's selected claims and their current states/attempts, model and reasoning, model cost, active work time and whole-run time. Its completed summary renders the backend counts and each held, left-for-handler or still-in-progress claim; it does not display a model-written narrative.
- A new Scoreboard navigation item and page contain the evaluation start controls (set, whole/tiny, N), newest-first evaluation list, status and every measure. Selecting an evaluation opens its comparison table with handler/agent decision and payout, agent reason, cost and active time. Highlight decision and payout misses with text/icon treatment as well as accessible brand colors, and link each copy to its claim page.
- The instructions page shows only the claim agent's read-only instructions.

## Phase 1: Default model, cost, active time and run tags

Establish the instrumentation and model configuration before introducing parallel work. Add the portable ledgers and backend operations, then capture usage in both claim-model nodes without changing claim decisions or payment routing.

### Success criteria

- [x] Model tests prove the default is `openai/gpt-6-luna` with low reasoning; a `MODEL_NAME` override has no reasoning effort, and run metadata records that override's effort as null/`none`. No DeepSeek default remains in code, fixtures or current operating docs.
- [x] Stub replies containing known OpenRouter usage create one model-call record per tool-loop and structured-decision reply, with exact prompt/completion/total tokens and `usage.cost`; raw structured output still produces the existing typed decision.
- [x] Backend ledger tests cover required inputs, portable numeric/timestamp round-trips, run isolation and required nullable `run_id=null` for direct work. Totals include failed-attempt calls and never include another run's calls.
- [x] Harness timing tests with a controlled monotonic clock record active seconds and UTC boundaries across initial work, retry and sign-off resume. A simulated multi-minute sign-off gap adds no active seconds; ordinary failed attempts remain included.
- [x] Fake Langfuse tests prove the existing claim/thread session is unchanged and `langfuse_tags` contains exactly the dispatch/evaluation tag on start, retry, recovery and resume; direct work has no run tag and missing keys still disable tracing.
- [x] Existing stub-model claim tests still prove one decision/payment and interrupt/resume behavior after instrumentation; no test calls OpenRouter, Langfuse or Supabase.
- [x] `npm run check:python` passes Ruff, Pyright and all tests with at least 80% coverage.

## Phase 2: Model-free dispatcher, run records and recovery

Build the new backend run/run-claim lifecycle and the lab-style dispatcher. Connect it through the agent service and `backend/agent_service.py`, replace queue-agent execution, and harden the shared checkpoint pool before changing the dashboard.

### Success criteria

- [ ] A stand-in worker test with at least 25 claims proves oldest-submitted selection, one `Send` per claim, additive results and never more than N concurrent invocations at N=1, 3 and 25. The dispatcher graph contains no model and no checkpointer.
- [ ] Dispatch selection includes every operational `waiting` and `in_progress` claim, excludes every other state and all evaluation copies, and is not capped to N claims. Each run-claim retains its selected order.
- [ ] A stub claim that raises `ModelRateLimitError` once resumes from its saved step on the same thread with no new failed attempt and records one decision/payment. Separate timeout coverage exercises the same policy without real sleeps.
- [ ] A worker that exhausts its rate-limit/timeout retries leaves the claim and run-claim `in_progress`, preserves its thread/attempt, appears in the record-built summary, and does not stop other claims. A later dispatch resumes it.
- [ ] An ordinary stub error returns the claim to waiting and the same dispatch retries once with a fresh thread; a second ordinary error leaves it for a handler. No claim receives a third attempt, and one bad claim does not stop its peers.
- [ ] Tests replay decision/payment boundaries under parallel workers and prove unchanged decisions, amounts, handlers and timestamps, one success activity per business effect, and no duplicate payment.
- [ ] A simulated stopped dispatch leaves attempts/threads intact; startup marks that run stopped, and a new dispatch resumes its unfinished claims without recreating the old run or reworking decided/held claims. A claim actively resuming after sign-off is skipped by dispatch.
- [ ] Backend finalization returns exact approved/declined/referred counts, each held claim and payout, each left-for-handler id and each still-in-progress id, plus run-only cost, active seconds and wall seconds. No model participates.
- [ ] Service tests prove one non-blocking slot across both run kinds, immediate 409 on a second start, 202/background execution for the first, and slot release after completion or orchestration failure. Backend start routes write nothing before contacting the service.
- [ ] Fake-pool tests prove one process pool capped at four, a separate `PostgresSaver`/lock per claim, one setup, synchronous required options and waiting rather than connection failure. SQLite tests never open Postgres.
- [ ] Queue-agent code, queue instructions, to-do APIs/table and bare CLI execution are removed or replaced; `uv run -m agent CLM-1001` still starts only that claim with no run. All affected Python tests are migrated rather than deleted without equivalent coverage.
- [ ] `npm run check:python` passes with at least 80% coverage and hosted/model environment variables cleared.

## Phase 3: Start and inspect dispatches from the dashboard

Replace the queue controls and to-do presentation with the new dispatch API. Make start failures visible and prove browser-to-backend-to-service calls with the existing port-8003 stand-in.

### Success criteria

- [ ] The queue shows “Agents at once,” default 3, accepts only integers 1–25, and starts one dispatch with the entered N. Active/invalid starts are disabled or rejected with clear messages.
- [ ] When the stand-in is unavailable, the dashboard says the agent service is not running and no dispatch record is created. A mocked 409 for an active dispatch/evaluation is also shown without another run.
- [ ] The run panel renders each selected claim, live status/attempts, model/reasoning, model cost, active time and wall time; polling continues while the dispatch is in progress and stops afterward.
- [ ] Completed panel tests render exact record-built decision counts and every held payout, left-for-handler and still-in-progress claim, with claim links and no model-authored summary dependency.
- [ ] The instructions page contains only the claim agent instructions; old queue text and cap/run-queue labels are absent.
- [ ] Playwright starts a dispatch at a chosen N and verifies the port-8003 stand-in receives its JSON body through the backend. The old to-do end-to-end test is replaced by dispatch-claim/status/summary coverage using deterministic API records, never a model.
- [ ] Vitest covers default/boundary/invalid N, loading/polling, offline/conflict errors, all summary sections and cost/time formatting. Strict TypeScript has no `any` and coverage remains at least 80%.
- [ ] `npm run check:ts` and `npm run e2e` pass with no hosted service or model calls.

## Phase 4: Isolated evaluations and deterministic scoring API

Seed the source/answer-key tables, create fresh copies, run them through the same dispatcher/harness, and expose scored evaluation records. Build this API and execution path before the Scoreboard page.

### Success criteria

- [ ] Seed tests find 25 source claims and 25 labels in each set, exactly five `tiny` labels in each, while `GET /api/claims` still returns only the 25 operational claims. Reseeding empties all evaluations, copies, ledgers and checkpoints and drops obsolete to-dos.
- [ ] Evaluation creation tests cover both labelled sets, whole/tiny selection, required N/set/scope, new ids written into both primary key and JSON, internal source mapping, model/reasoning, and no mutation of source/queue records.
- [ ] The agent service never requests handler decisions, and no claim/agent tool or prompt contains answer-key content. `get_claim` on a copy returns its full business bundle but no handler decision/payout/reason/tags; only the evaluation detail operation exposes the side-by-side comparison.
- [ ] A stub-model evaluation uses the same dispatcher and claim graph, leaves queue counts/statuses byte-for-byte unchanged, and a later operational dispatch never selects its copies. Cost/time rows belong only to that evaluation.
- [ ] An over-limit evaluation approval remains unpaid, counts as finished, and has its saved claim thread cleared. Its page has no sign-off form, and direct sign-off on the copy returns 409 without changing it. Completed non-held copies also have no resumable evaluation checkpoint.
- [ ] Startup marks a partially worked evaluation `stopped`; scoreboard reads identify that state and treat every undecided selected copy as a zero-payout referral.
- [ ] A hand-worked scoring fixture covers a correct approval, wrong amount within/over $1, wrongful decline, wrongful approval whether paid or held, needless referral, correct decline/referral and an undecided copy. API results exactly match independently calculated decision accuracy, accuracy, mistake cost, straight-through rate, total/per-claim model cost, average/slowest active seconds and whole-run seconds.
- [ ] Evaluation list/detail responses are newest first, typed and complete with per-claim handler/agent decisions and payouts, agent reason, cost/time and miss indicators. All new operations satisfy the OpenAPI rules.
- [ ] `npm run check:python` passes all evaluation tests on isolated SQLite/stubs with at least 80% coverage and no network/model calls.

## Phase 5: Scoreboard, end-to-end coverage and documentation

Deliver the evaluation controls and scoreboard, then update the product documents and prove the complete offline product before any live run.

### Success criteria

- [ ] Scoreboard navigation starts evaluations with a chosen set, whole/tiny scope and N=1–25, shows offline/409 errors, and prevents a second active dispatch/evaluation start.
- [ ] The newest-first evaluation list shows set/scope, N, model/reasoning, status and every required measure with clear units and USD/percentage/time formatting. Stopped evaluations remain identifiable.
- [ ] Evaluation detail shows handler and agent decision/payout side by side, agent reason, per-claim cost/time and easy-to-spot decision/payout misses; every copy id opens the correct claim page and no sign-off control appears there.
- [ ] Playwright verifies that starting an evaluation sends N, set and scope to the port-8003 stand-in through the backend, and that an evaluation played deterministically through service-side backend APIs appears on the scoreboard with hand-calculated measures and linked claims, following the intent of the brief's `e2e/run.spec.ts` example.
- [ ] Vitest covers evaluation controls, list/detail states, every measure, miss rendering, stopped state and copy navigation. Strict TypeScript and both coverage thresholds remain intact.
- [ ] Playwright MCP review at desktop and mobile sizes covers dispatch controls/panel, Scoreboard list/detail, an evaluation copy and the claim-only instructions page, with keyboard-accessible controls, sufficient contrast, no console errors and no horizontal overflow.
- [ ] `docs/SPEC.md` updates queue work and team visibility for dispatches/evaluations/scoreboard while leaving Rules unchanged. `docs/TECH_STACK.md` documents Luna, ledgers, the model-free dispatcher, isolated answer keys, shared four-connection checkpoint pool and service boundary.
- [ ] `docs/PROCESS.md` replaces the five-claim cap with N-based dispatch/evaluation operation, named-only CLI, reseeding, offline checks and Windows behavior. `docs/CODE_STANDARDS.md` updates the agent/data rules, including rules 10 and 13, for the five-part agent and removal of queue middleware/to-dos. `docs/STATUS.md` has current setup, run, evaluation, recovery and verification instructions.
- [ ] With `.env` temporarily moved away and safely restored, `npm run check` passes Ruff, Pyright, pytest, Prettier, ESLint, tsc, Vitest and Playwright with at least 80% Python/TypeScript coverage and no hosted/model calls.

## Phase 6: One-seed live swarm and scoreboard verification

Stop services, preserve the student's `.env` without printing secrets, temporarily remove `MODEL_NAME` so the default Luna/low configuration is exercised, and run `npm run seed` exactly once before these live runs. Do not reseed between the restart or evaluations. Restore the original override and stop services afterward.

### Success criteria

- [ ] Supabase MCP inspection after the single seed shows 25 waiting operational claims, held-out sources/labels only in their separate tables, empty runs/scoreboard/ledgers, and no obsolete to-do table. Normal startup does not reseed.
- [ ] Through Playwright MCP, start a dispatch at N=3 with default `openai/gpt-6-luna`/low. Once claims have saved partial work, use `kill -9` on every process from `lsof -ti tcp:8002` (or the documented Windows equivalent) so `npm run dev` stops the others; confirm the old dispatch is marked stopped after restart without altering claim attempts.
- [ ] Restart `npm run dev` and start a new dispatch at N=25. It resumes every interrupted claim on the same thread/attempt and works every remaining waiting claim; recorded start times demonstrate up to 25 at once and no connection-starvation failure occurs.
- [ ] The N=25 dispatch finishes with a record-built summary and takes well under the sum of claim active times. Compare claims worked from the start with their N=3 counterparts; if materially slower, report where the time goes while retaining four checkpoint connections per process.
- [ ] Held claims wait unpaid; signing one off through Playwright MCP shows In progress and then paid within five seconds with handler/sign-off/payment timestamps. Every selected claim has at most one decision and payment.
- [ ] During live dispatch work, read-only Supabase MCP `pg_stat_activity` inspection for user `postgres` shows at most 12 connections; backend and agent per-process limits remain at four each.
- [ ] Run one whole evaluation-set evaluation at N=25 and one held-out `tiny` evaluation at N=5. Both appear on Scoreboard with every measure and per-claim comparison; the operational queue's counts and records are identical before and after, and no evaluation copy can be signed off.
- [ ] Through Langfuse's API, each N=25 dispatch claim retains its own session and all of that dispatch's claim traces are found together by its run tag; evaluation traces carry their own evaluation tag.
- [ ] Restore the student's original `MODEL_NAME`, stop all development services, and run the final `npm run check` with `.env` moved away and restored. Record live timings, costs, connection evidence and the final offline results in `docs/STATUS.md`.

## Risks and controls

| Risk | Control |
|---|---|
| Parallel replay duplicates a decision or payment | Separate claim threads/savers, per-claim execution locks, backend row locks and idempotent writes; replay tests at graph boundaries. |
| N is mistaken for a claim cap | Backend selection tests prove all waiting/interrupted claims are selected while LangGraph's `max_concurrency` alone limits active workers. |
| Retry handling changes attempt semantics | Distinct tests for transient retry, exhausted transient, first/second ordinary failure and process kill; persistent claim-level failure counts. |
| Evaluation data leaks to the agent or queue | Separate source/label tables, evaluation markers on copies, negative API/prompt/tool tests and queue/dispatch filters. |
| Cost or time is inferred incorrectly | Record only OpenRouter usage, use controlled clocks in tests, attribute by run id, and independently calculate every scoreboard measure. |
| Swarm exhausts Supabase connections | One synchronous pool capped at four per agent process, one saver per claim, live `pg_stat_activity` evidence and no fallback to extra direct connections. |

## Build checkpoints

Implement the phases in order with tests first for each new behavior. After each phase, run its focused checks, tick only verified criteria, update `docs/STATUS.md`, and commit with the pre-commit hook enabled. Keep automated tests on isolated SQLite, fakes and stub models; never weaken checks, create unrequested hosted resources, or push. Finish with the one-seed live evidence above and the complete offline `npm run check`.
