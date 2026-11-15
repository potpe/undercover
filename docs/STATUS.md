# Status

## Context build in progress

Phase 1 was committed as `04d1541` with pre-commit checks enabled. Phase 2 passes the cumulative no-`.env` gate: 299 Python tests at 93.37% coverage, 51 dashboard tests at 88.28% statement coverage, 14 Playwright tests, and isolated server/context imports (27/31 packages). `.env` was restored afterward; the upstream Mangum warning remains unsuppressed. Additional context assertions pass all 26 focused tests. The backend also imports with the context package explicitly blocked. A local CLI smoke listed seven tools and called `lookup_term` unsigned with correct glossary provenance, then stopped the service. All seven governance files and three legacy plan/document files remain unchanged. Phases 3–5 and final live verification remain pending.

Phase 1 is verified offline: 273 Python tests at 93.10% coverage, 51 dashboard tests at 88.28% statement coverage, 14 Playwright tests, and the isolated server import (26 packages). The cumulative gate ran with root `.env` absent and restored afterward; Ruff, Pyright, Prettier, ESLint and TypeScript checks passed. One upstream Mangum event-loop deprecation warning remains unsuppressed. Gateway/app refusal recovery, trusted maker injection, repeated request signing, complete structured MCP artifacts and hold-commit recovery are covered. Backend hold guards include a mutation that failed as expected. Independent review confirmed its four findings resolved. Phases 2–5 and final live verification have not run. No reseed, live model call or AWS resource change has been made for this build.

The claim, screening and improver models now request at most 7,152 output tokens instead of the claim models' previous 8,192; the improver previously had no cap. Construction tests verify all three paths. The complete no-`.env` gate passes 241 Python tests at 93.87% coverage, 51 dashboard tests at 88.28% statement coverage, 14 Playwright tests and the isolated server import. This addresses the observed HTTP 402 request-size failure, not the provider's changing credit balance. No live model call, reseed or deployment was made for this correction.

Security Phase 4 is verified offline: static dashboard/Lambda behavior and Lambda database lifecycle are covered, and `npm run check` with root `.env` absent passes 241 Python tests at 93.87% coverage, 51 Vitest tests at 88.28% statement coverage, 14 Playwright tests, and the isolated server import. The server group installs 26 distributions. Both production builds (root `.env` and process-environment values with `.env` absent) completed without build warnings. The full Python check emitted one upstream Mangum event-loop deprecation warning; it was not suppressed. The refreshed Linux x86_64/Python 3.12 ZIP is 20,183,161 bytes (20.183161 decimal MB) at `/private/var/folders/lt/qmlktfbd38x783kbt_1_p4br0000gn/T/opencode/undercover-lambda.NpextJ/undercover-app.zip`; its contents were verified. No AWS resources were created.

## Current live verification (2026-11-14)

One seed completed successfully with the default `openai/gpt-6-luna` / low configuration; the original `.env` was restored. Startup showed 26 waiting claims. Dana signed in to the dashboard, which showed all 26 claims. Health returned 200 and an agent-only route without a key returned 401.

The named CLI run for `CLM-1001` approved and paid $420.00. Its ledger records 12 model calls, including one screening call; reported cost was $0.00291554, with 50,689 prompt tokens and 987 completion tokens (51,676 total).

The N=3 dispatch (run 1) ended with status `completed` but did not decide or refer any claims: all 25 remaining claims were left for handlers. OpenRouter returned HTTP 402 because the request's 8,192-token output limit exceeded its affordable 7,152 tokens. The dispatch ledger has zero tokens and $0 cost. `CLM-1901` was left for a handler before screening: no decision, security-screen activity or model ledger row. The tiny evaluation was not started, no judge call occurred, and handler sign-off was not attempted because no held approvals resulted. Current operational state is one paid claim and 25 claims left for handlers; the dispatch status is not evidence of successful claim processing.

Standalone and Lambda-mode browser checks used headless Playwright via Node and each showed 26 claims with no console or page errors; the Lambda-mode check used the transaction pooler on port 6543. The requested authenticated Playwright MCP verification was not completed. All services were stopped; `.env` remains restored. There was no second seed or AWS activity.

## Earlier implementation checkpoints

Security Phase 3 adds the generated eight-operation agent OpenAPI, shared policy/history reads, current-limit-first public payment guard, and atomic sign-off/payment without an agent-service notification. Failure injection proves sign-off and payment roll back together; repeated graph resume makes no payment call. The complete no-`.env` gate passes 234 Python tests at 93.79% coverage, 51 dashboard tests at 88.28% statement coverage and 14 Playwright tests. A pre-existing dispatch test race now waits for the actual POST response before inspecting the stand-in. At the Phase 3 handoff, live verification remained deferred to the final one-seed pass.

Security Phase 2 adds the first durable hidden-instruction screen, fixed outside versioned instructions, with reported usage on every attempt and round-budget checks before retries. A flagged claim is referred without invoking the claim model. The queue seeds 26 claims; unlabelled `CLM-1901` stays out of both evaluation sets. The no-`.env` full gate passes 228 Python tests at 93.84% coverage, 51 dashboard tests at 88.28% statement coverage, and 14 Playwright tests. Resume/sign-off, same-claim fresh threads, provider timeout/error, parse exhaustion and screening cost aggregation have stub coverage. No live reseed or model call has run.

Security Phase 1 adds request-time agent API-key authentication, explicit route credential boundaries, handler bearer tokens on every dashboard request, and a key-only named-claim CLI start followed by direct graph execution. `UNDERCOVER_API_KEY` must contain a random value of at least 32 characters. The backend starts without it but refuses agent-only requests; the agent service fails before recovery when it is missing. The key never grants handler permissions.

Security Phase 1's complete `npm run check` passes with root `.env` absent and safely restored: 216 Python tests at 93.70% coverage, 51 dashboard tests at 88.28% statement coverage, and all 14 Playwright tests. Agent-side test clients carry only the fixed non-secret service key, while local handler sessions remain separate. Removing bearer injection makes all three new API regression tests fail. A separate security review found no required corrections after replacing path middleware with explicit route dependencies. No live reseed or model call has run.

The swarm, scoreboard and Improvement Phases 1–4 are implemented and pass the offline gate. This includes Supabase password sign-in, handler permissions, versioned configuration/recipes, judging, durable improvement rounds, budget controls and the Improvement page. Hosted connection limitations remain documented below. No live improvement round was run.

- `backend/`: FastAPI and SQLAlchemy Core on SQLite offline or Supabase Postgres. APIs cover claims, judging, versioned configurations/recipes, settings/audits, improvement rounds/proposals, spending and verdicts as well as dispatches, evaluations and claim operations. The backend enforces handler permissions, the approval limit, and idempotent writes. Handler sources and answer keys stay backend-only.
- `agent/`: a synchronous durable claim graph, model-free LangGraph dispatcher and separate recipe-guided improver. Claim threads and round approvals use synchronous checkpoints; the shared Postgres pool remains capped at four connections, with a separate saver per claim or round. Langfuse groups improver stretches by round and tags round evaluations alongside their run tags.
- `agent/service.py`: a separate FastAPI service on port 8002 starts dispatches, evaluations and improvement rounds, works named claims and resumes sign-offs/proposals. One named, non-blocking slot prevents competing work; approval waits release it. Startup stops interrupted work and resumes recorded round decisions and signed-off claims. `uv run -m agent CLM-1001` works one claim without a run; bare CLI queue execution is removed.
- `dashboard/`: every page requires a Supabase Auth session, and the header shows the signed-in handler with sign-out. The dashboard includes Claims, Scoreboard, Configuration, Improvement and Settings. Improvement shows round stages/spend, proposal review, audit history, held-out scores and an accessible version chart. Senior-only actions are explained to other handlers; held payouts use the signed-in identity.
- `e2e/`: fourteen Playwright tests sign in through a local asymmetric-key Supabase Auth stand-in on port 8005. They cover queue, claim work/decisions, sign-off, dispatch/evaluation payloads, scoreboard copies, proposal approval and senior permissions. Tests use isolated SQLite and local service/auth stand-ins, never hosted services or models.

## Set up

```sh
uv sync
npm install
```

For hosted/live work, set `DATABASE_URL`, `UNDERCOVER_API_KEY`, `OPENROUTER_API_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_BASE_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` and `HANDLER_PASSWORD` in `.env`. The Supabase project needs the three handler users with sign-up disabled and asymmetric JWT signing keys; no Supabase secret is used. `UNDERCOVER_AGENT` is optional and defaults to `http://127.0.0.1:8002`.

The seeded default configuration is `openai/gpt-6-luna` with low reasoning. Seeding with `MODEL_NAME` instead creates that model at `default` reasoning and a zero-price one-model catalog. Runs pin their configuration version; the service no longer independently chooses a model for each claim. Tests do not read `.env` and need no hosted service. Vite development and production builds can read root `.env` or process environment values; test mode uses its own environment and never reads root `.env`.

Configuration replaces Instructions. Settings displays the approval limit, improvement objectives/noise/tolerance/budget/concurrency and the read-only model catalog. Senior handlers confirm platform changes; every changed value records its handler and time. A changed approval limit applies to new decisions, while old approvals retain their original sign-off requirement. Light/dark/system appearance stays in this browser.

## Reseed

Stop development services, then run:

```sh
npm run seed
```

This intentionally resets the selected database, including decisions, payments, runs, evaluations, ledgers, activity and checkpoints. It restores exactly 26 waiting operational claims and the separate evaluation sources/labels, with an empty scoreboard. `CLM-1901` includes its security policy/customer but no handler decision and enters neither evaluation set. Normal startup preserves work, and evaluations need no reseed.

## Run

```sh
npm run dev
```

Open `http://localhost:5173`. The backend listens at `http://localhost:8000` and the agent service at `http://localhost:8002`.

On Claims, choose Agents at once from 1 to 25 (default 3) and Start dispatch. A dispatch works every waiting or interrupted operational claim, oldest submitted first; N limits concurrency, not claim count. The panel shows each claim, decision counts, held payouts, handler handoffs, unfinished claims and model/active/wall time.

On Scoreboard, choose Evaluation set or Held-out set, the whole set or its five tiny claims, and N. Start evaluations one at a time relative to dispatches. Open an evaluation for handler/agent decisions, payouts, reasons, misses and per-claim cost/time. A stopped run remains labelled stopped; undecided copies count as referrals.

On Improvement, a senior handler can start or stop one round, review configuration/recipe proposals, approve or reject with a reason, and inspect round history and held-out version scores. Approval waits survive service restart. The complete round loop is verified offline with stubs; do not run live rounds as part of the offline check.

Over-limit operational approvals wait for sign-off. Signing off saves the handler/time and payment atomically, immediately showing Decided without contacting the agent service. A saved agent thread can finish later without another payment call. The public agent payment API refuses amounts over the current settings limit before claim lookup. Evaluation approvals held for sign-off count as finished, stay unpaid and cannot be signed off.

With the services running, `uv run -m agent CLM-1001` works only that named claim with no dispatch/evaluation.

## Check

```sh
npm run check
```

This runs Ruff, Pyright, pytest with at least 80% coverage, an isolated `server`-group import, Prettier, ESLint, tsc, Vitest with at least 80% coverage, and Playwright. Stop development services first so Playwright can start isolated services.

## Verification

Branch review fixes: same-user sign-in/token-refresh events preserve unsaved forms and dialogs while updating the bearer token; identity changes still clear the previous handler and page state. Configuration/recipe diffs retain content resembling diff headers. Regression tests cover both fixes; the offline gate passes 206 Python tests at 93.69% coverage, 48 Vitest tests at 88.32% statement coverage and 14 Playwright tests with root `.env` absent and safely restored. Both findings are marked fixed in `docs/BUGS.md`.

Improvement Phase 4: durable round orchestration, separate improver tools and checkpoint, proposal approval/rejection, candidate evaluation/verdicts, budget enforcement, Langfuse round tracing and the Improvement page pass the local stub/SQLite tests. With root `.env` moved aside and restored, `npm run check` passed 201 Python tests at 93.68% coverage, 45 Vitest tests at 87.89% statement coverage and 14 Playwright tests. No hosted service, model, live round or reseed was used.

Improvement Phase 3: Jev grading runs concurrently in the backend through one SDK call site, using a 10-second timeout and no retries. Grades/check probabilities and separate judge costs are persisted; failed grading is excluded from averages, undecided claims receive F/0, and free mode leaves every claim ungraded. Scoreboard shows grades and handler references. The no-`.env` gate passes 127 Python tests at 93.85% coverage, 38 Vitest tests at 89.16% statement coverage and 12 Playwright tests; the real Decisions SDK talks only to a local judge stand-in.

Improvement Phase 2: the full gate passes 122 Python tests at 93.67% coverage, 37 Vitest tests at 89.08% statement coverage and 12 Playwright tests. All model/Auth interactions are stubs; no hosted resources have been reseeded during implementation. Existing databases need the planned explicit seed after services stop before running the changed schema; normal startup does not migrate or reset old tables.

Improvement Phase 1: `npm run check` passed with `.env` absent. The dashboard suite has 32 tests at 88.58% statement coverage, and all ten Playwright tests sign in through the local Auth/JWKS stand-in. Backend tests cover signed tokens, audience/expiry, missing and invalid credentials, unknown users and Dana/Marcus sign-off permissions.

Final swarm offline gate: with `.env` absent, `npm run check` passed 102 Python tests at 93.50% coverage, 28 Vitest tests at 90.14% statement coverage and ten Playwright tests without hosted/model calls. Additional regressions cover atomic pool initialization, named-thread persistence, current-run usage after resume, startup cleanup ordering and terminal evaluation cleanup. Playwright MCP review found no console errors or horizontal overflow at desktop/mobile sizes across dispatch, Scoreboard, evaluation copy and instructions.

Live verification used one Supabase seed and default Luna/low. The stopped N=3 run became `stopped` with its three interrupted claims' threads and zero attempts preserved. The subsequent N=25 work completed its remaining dispatch with a record-built summary in 39.13 seconds against 187.83 seconds of accumulated claim work. Provider in-flight-budget responses and session-pooler pressure caused real retries/handoffs; a killed N=3 claim has no final work period, so a like-for-like timing comparison is unavailable. The backend pool was reduced to two connections to leave headroom, while the claim checkpoint pool remains capped at four. Observed `postgres` connection counts ranged from 10 to 17; the 17-connection peak and associated failures mean the hosted connection criterion remains unchecked.

`CLM-1008` waited for Dana's sign-off and paid $2,240.00 after 2.03 seconds with one decision/payment. The whole evaluation at N=25 and held-out tiny evaluation at N=5 completed with every measure; the operational queue was byte-for-byte unchanged. Langfuse's v2 observations API found 25 traces by `evaluation-4` and also found the `evaluation-5` traces. The earlier full N=25 dispatch was worked before the trace-propagation fix, so its whole-dispatch tag criterion remains unchecked rather than being backfilled.

`MODEL_NAME=openrouter/free` was restored and live services were stopped. Supabase continues to report RLS disabled on `undercover` tables; review access policies before client exposure.
