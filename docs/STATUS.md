# Status

The swarm build is in progress. Phases 1 through 3 are complete.

- `backend/`: FastAPI and SQLAlchemy Core, using SQLite offline or Supabase Postgres through `DATABASE_URL`. Postgres uses schema `undercover` and at most four pooled connections per process. APIs cover claims, document reads, deterministic payouts, concurrent dispatches, recovery, decisions, sign-off, payment, usage/time ledgers and record-built summaries. The backend enforces the $1,000 sign-off rule and treats identical decision/payment replays as done.
- `agent/`: a hand-written synchronous claim graph plus a model-free LangGraph dispatcher using `Send`, N-way concurrency and targeted retries. Each claim keeps its own thread. A process-wide synchronous pool caps Postgres checkpoint connections at four, with a separate `PostgresSaver` per claim; tests use isolated SQLite. Langfuse sessions remain claim/thread-scoped with dispatch/evaluation tags.
- `agent/service.py`: a separate FastAPI service on port 8002 for claim work, dispatches and sign-off resume. The backend calls `UNDERCOVER_AGENT` through `backend/agent_service.py`; sign-off is saved before notification and stands if the agent is unavailable. One non-blocking slot prevents concurrent dispatches/evaluations. Startup marks stale runs stopped and recovers signed-off work; the next dispatch resumes saved claim threads and attempts.
- `dashboard/`: branded, responsive claims queue and current-dispatch panel with N=1–25 (default 3), claim states/attempts, model/reasoning, cost/time and record-built summaries. Full claim workspace retains activity, sections read and decision details. Handlers only sign off; the agent then resumes and pays. The instructions page shows only the claim agent.
- `e2e/`: eight Playwright tests covering queue, claim-only instructions, claims, decisions, sign-off notification, dispatch claims/summary, N validation and the N-valued service request. They use isolated SQLite and a stand-in agent on port 8003, never hosted services or a model.

## Set up

```sh
uv sync
npm install
```

Set these values in `.env` for hosted/live work:

- `DATABASE_URL`: the Supabase session pooler connection string.
- `OPENROUTER_API_KEY`: the OpenRouter key.
- `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_BASE_URL`: the Langfuse project connection.

`MODEL_NAME` is optional, for example `MODEL_NAME=openrouter/free`; the default is `openai/gpt-6-luna` with low reasoning. `UNDERCOVER_AGENT` is optional and defaults to `http://127.0.0.1:8002`.

Tests do not read `.env` and need none of these services.

## Reseed

Stop the development services, then run:

```sh
npm run seed
```

This intentionally resets the database selected by `DATABASE_URL` (or local SQLite), including decisions, payments, runs, activity and old checkpoints, then restores exactly 25 waiting claims. Normal startup preserves existing work.

## Run

```sh
npm run dev
```

Open `http://localhost:5173`. The backend listens at `http://localhost:8000` and the agent service at `http://localhost:8002`.

For a low-cost trial, leave Claim cap at five and select Run queue. A waiting claim can also be run on its own from its claim page. With the services running, `uv run -m agent` starts a cap-five run; `uv run -m agent --claim-id CLM-1001` starts a named claim. `--claim-cap` changes the queue cap.

An over-limit approval waits for sign-off. Signing off saves the handler and time, shows the claim In progress, and asks the agent to resume payment. If the agent is down or stopped, the next service start or run recovers the saved work without adding an attempt. Real errors retain the existing fresh-retry and second-failure handoff behavior.

## Check

```sh
npm run check
```

This runs Ruff, Pyright, pytest with at least 80% coverage, Prettier, ESLint, tsc, Vitest with at least 80% coverage, and Playwright. Stop development services first so Playwright can start its isolated database and stand-in agent.

## Verification

Swarm Phase 3: 20 Vitest tests passed with 92.42% statement coverage and eight Playwright tests passed. The queue now starts N-way dispatches, displays record-built claim/decision summaries and exact cost/time, surfaces service-offline/conflict errors, and shows only claim-agent instructions. Browser tests prove N reaches the port-8003 agent stand-in. Phase 2's Python gate passed 90 tests with 92.97% coverage.

The previous durable-state verification used DeepSeek and completed five selected claims; 20 remained waiting. A stopped undecided claim resumed on its original thread with zero attempts and one decision/payment, and the stopped queue finished its original plan. `CLM-1003` waited for Dana's sign-off, showed In progress, then paid $3,750.00 after 3.71 seconds with one decision and one payment. The current Langfuse session contains traces before and after sign-off with no errors in the final run. Supabase showed eight `postgres` connections during live work, below the 12-connection limit. Desktop/mobile browser checks had no overflow or console errors. `MODEL_NAME=openrouter/free` was restored afterward.

Supabase reports RLS disabled on the `undercover` product tables. No remediation was applied in this build; review access policies before exposing the database to client applications.
