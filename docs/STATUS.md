# Status

The swarm and scoreboard implementation is complete. Improvement Phases 1–2 add Supabase password sign-in, handler permissions, configuration/recipe version 1, version-pinned runs, audited settings and browser appearance. Judge/improver phases remain in progress. Offline checks pass; hosted acceptance has remaining provider/connection limitations documented below.

- `backend/`: FastAPI and SQLAlchemy Core on SQLite offline or Supabase Postgres. APIs cover claims, document reads, deterministic payouts, dispatches, evaluations, usage/time ledgers, decisions, sign-off, payment and record-built summaries/scores. The backend enforces the $1,000 sign-off rule and idempotent writes. Handler sources and decisions are backend-only.
- `agent/`: a synchronous durable claim graph plus a model-free LangGraph dispatcher using `Send`, N-way concurrency and targeted retries. Claim threads and attempts survive stopped runs. A process-wide synchronous pool caps checkpoint connections at four with a separate saver per claim. Langfuse keeps claim/thread sessions and adds run tags.
- `agent/service.py`: a separate FastAPI service on port 8002 starts dispatches/evaluations, works named claims and resumes sign-offs. One non-blocking slot prevents concurrent runs. Startup marks stale runs stopped and recovers saved sign-offs. `uv run -m agent CLM-1001` works one claim without a run; bare CLI queue execution is removed.
- `dashboard/`: every page requires a Supabase Auth session, and the header shows the signed-in handler with sign-out. The queue starts N=1–25 dispatches (default 3), with claim states/attempts, record-built summaries and model/active/wall time. Scoreboard starts whole/tiny evaluations, lists newest first with every measure, and links handler/agent comparisons to isolated copy pages with no run/sign-off controls. Instructions show only the claim agent. Held payouts use the signed-in identity; handlers without over-limit permission see why they cannot sign off.
- `e2e/`: ten Playwright tests sign in through a local asymmetric-key Supabase Auth stand-in on port 8005. They cover queue, instructions, claim work/decisions, sign-off notification, dispatch summaries, N payloads, evaluation payloads and an API-played scoreboard evaluation. Tests use isolated SQLite and the port-8003 agent stand-in, never hosted services or models.

## Set up

```sh
uv sync
npm install
```

For hosted/live work, set `DATABASE_URL`, `OPENROUTER_API_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_BASE_URL`, `VITE_SUPABASE_URL`, `VITE_SUPABASE_PUBLISHABLE_KEY` and `HANDLER_PASSWORD` in `.env`. The Supabase project needs the three handler users with sign-up disabled and asymmetric JWT signing keys; no Supabase secret is used. `UNDERCOVER_AGENT` is optional and defaults to `http://127.0.0.1:8002`.

The seeded default configuration is `openai/gpt-6-luna` with low reasoning. Seeding with `MODEL_NAME` instead creates that model at `default` reasoning and a zero-price one-model catalog. Runs pin their configuration version; the service no longer independently chooses a model for each claim. Tests do not read `.env` and need no hosted service. Vite reads root `.env` only in development mode; tests use explicit local values.

Configuration replaces Instructions. Settings displays the approval limit, improvement objectives/noise/tolerance/budget/concurrency and the read-only model catalog. Senior handlers confirm platform changes; every changed value records its handler and time. A changed approval limit applies to new decisions, while old approvals retain their original sign-off requirement. Light/dark/system appearance stays in this browser.

## Reseed

Stop development services, then run:

```sh
npm run seed
```

This intentionally resets the selected database, including decisions, payments, runs, evaluations, ledgers, activity and checkpoints. It restores exactly 25 waiting operational claims and the separate evaluation sources/labels, with an empty scoreboard. Normal startup preserves work, and evaluations need no reseed.

## Run

```sh
npm run dev
```

Open `http://localhost:5173`. The backend listens at `http://localhost:8000` and the agent service at `http://localhost:8002`.

On Claims, choose Agents at once from 1 to 25 (default 3) and Start dispatch. A dispatch works every waiting or interrupted operational claim, oldest submitted first; N limits concurrency, not claim count. The panel shows each claim, decision counts, held payouts, handler handoffs, unfinished claims and model/active/wall time.

On Scoreboard, choose Evaluation set or Held-out set, the whole set or its five tiny claims, and N. Start evaluations one at a time relative to dispatches. Open an evaluation for handler/agent decisions, payouts, reasons, misses and per-claim cost/time. A stopped run remains labelled stopped; undecided copies count as referrals.

Over-limit operational approvals wait for sign-off. Signing off saves the handler/time, shows In progress, then resumes the same thread and pays once. Evaluation approvals held for sign-off count as finished, stay unpaid and cannot be signed off.

With the services running, `uv run -m agent CLM-1001` works only that named claim with no dispatch/evaluation.

## Check

```sh
npm run check
```

This runs Ruff, Pyright, pytest with at least 80% coverage, Prettier, ESLint, tsc, Vitest with at least 80% coverage, and Playwright. Stop development services first so Playwright can start isolated services.

## Verification

Improvement Phase 2: the full gate passes 122 Python tests at 93.67% coverage, 37 Vitest tests at 89.08% statement coverage and 12 Playwright tests. All model/Auth interactions are stubs; no hosted resources have been reseeded during implementation. Existing databases need the planned explicit seed after services stop before running the changed schema; normal startup does not migrate or reset old tables.

Improvement Phase 1: `npm run check` passed with `.env` absent. The dashboard suite has 32 tests at 88.58% statement coverage, and all ten Playwright tests sign in through the local Auth/JWKS stand-in. Backend tests cover signed tokens, audience/expiry, missing and invalid credentials, unknown users and Dana/Marcus sign-off permissions.

Final swarm offline gate: with `.env` absent, `npm run check` passed 102 Python tests at 93.50% coverage, 28 Vitest tests at 90.14% statement coverage and ten Playwright tests without hosted/model calls. Additional regressions cover atomic pool initialization, named-thread persistence, current-run usage after resume, startup cleanup ordering and terminal evaluation cleanup. Playwright MCP review found no console errors or horizontal overflow at desktop/mobile sizes across dispatch, Scoreboard, evaluation copy and instructions.

Live verification used one Supabase seed and default Luna/low. The stopped N=3 run became `stopped` with its three interrupted claims' threads and zero attempts preserved. The subsequent N=25 work completed its remaining dispatch with a record-built summary in 39.13 seconds against 187.83 seconds of accumulated claim work. Provider in-flight-budget responses and session-pooler pressure caused real retries/handoffs; a killed N=3 claim has no final work period, so a like-for-like timing comparison is unavailable. The backend pool was reduced to two connections to leave headroom, while the claim checkpoint pool remains capped at four. Observed `postgres` connection counts ranged from 10 to 17; the 17-connection peak and associated failures mean the hosted connection criterion remains unchecked.

`CLM-1008` waited for Dana's sign-off and paid $2,240.00 after 2.03 seconds with one decision/payment. The whole evaluation at N=25 and held-out tiny evaluation at N=5 completed with every measure; the operational queue was byte-for-byte unchanged. Langfuse's v2 observations API found 25 traces by `evaluation-4` and also found the `evaluation-5` traces. The earlier full N=25 dispatch was worked before the trace-propagation fix, so its whole-dispatch tag criterion remains unchecked rather than being backfilled.

`MODEL_NAME=openrouter/free` was restored and live services were stopped. Supabase continues to report RLS disabled on `undercover` tables; review access policies before client exposure.
