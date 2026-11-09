# Status

The durable-state build is implemented. Hosted verification is in progress.

- `backend/`: FastAPI and SQLAlchemy Core, using SQLite offline or Supabase Postgres through `DATABASE_URL`. Postgres uses schema `undercover` and at most four pooled connections per process. APIs cover claims, section-level document reads, deterministic payouts, capped runs, durable to-dos, recovery, decisions, sign-off, payment and summaries. The backend enforces the $1,000 sign-off rule and treats identical decision/payment replays as done.
- `agent/`: a hand-written synchronous LangGraph claim graph with API-only claim, document and payout tools, structured decisions, `interrupt()` sign-off waits and graph-owned payment. The queue agent remains LangChain `create_agent`. Checkpoints use Supabase `PostgresSaver` or local SQLite with sync durability. Stable claim/thread Langfuse sessions include work before and after a pause.
- `agent/service.py`: a separate FastAPI service on port 8002 for claim work, queue runs and sign-off resume. The backend calls `UNDERCOVER_AGENT`; sign-off is saved before notification and stands if the agent is unavailable. Startup and each run recover stopped or signed-off work. A stopped queue run keeps its original id, plan, thread and attempts.
- `dashboard/`: branded, responsive claims queue and current-run panel; full claim workspace with activity, sections read, decision and payment details. Handlers only sign off; the agent then resumes and pays. The dashboard shows signing handler/time and paid time.
- `e2e/`: seven Playwright tests covering queue, instructions, claims, decisions, sign-off notification, to-dos, document reads and cap validation. They use isolated SQLite and a stand-in agent on port 8003, never hosted services or a model.

## Set up

```sh
uv sync
npm install
```

Set these values in `.env` for hosted/live work:

- `DATABASE_URL`: the Supabase session pooler connection string.
- `OPENROUTER_API_KEY`: the OpenRouter key.
- `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_BASE_URL`: the Langfuse project connection.

`MODEL_NAME` is optional, for example `MODEL_NAME=openrouter/free`; the default is `deepseek/deepseek-v4.1-flash` with low reasoning. `UNDERCOVER_AGENT` is optional and defaults to `http://127.0.0.1:8002`.

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

Durable Phase 5: with `.env` absent, `npm run check` passed 91 Python tests at 92.90% coverage, 17 Vitest tests at 91.93% statement coverage and seven Playwright tests. Tests cover payout rules, Langfuse sessions, idempotent writes, graph interrupt/resume, checkpoint replay, fresh error retries, downtime sign-off recovery and stopped queue completion.
