# Status

The swarm and scoreboard build is implemented through Phase 5. Hosted verification is pending.

- `backend/`: FastAPI and SQLAlchemy Core on SQLite offline or Supabase Postgres. APIs cover claims, document reads, deterministic payouts, dispatches, evaluations, usage/time ledgers, decisions, sign-off, payment and record-built summaries/scores. The backend enforces the $1,000 sign-off rule and idempotent writes. Handler sources and decisions are backend-only.
- `agent/`: a synchronous durable claim graph plus a model-free LangGraph dispatcher using `Send`, N-way concurrency and targeted retries. Claim threads and attempts survive stopped runs. A process-wide synchronous pool caps checkpoint connections at four with a separate saver per claim. Langfuse keeps claim/thread sessions and adds run tags.
- `agent/service.py`: a separate FastAPI service on port 8002 starts dispatches/evaluations, works named claims and resumes sign-offs. One non-blocking slot prevents concurrent runs. Startup marks stale runs stopped and recovers saved sign-offs. `uv run -m agent CLM-1001` works one claim without a run; bare CLI queue execution is removed.
- `dashboard/`: the queue starts N=1–25 dispatches (default 3), with claim states/attempts, record-built summaries and model/active/wall time. Scoreboard starts whole/tiny evaluations, lists newest first with every measure, and links handler/agent comparisons to isolated copy pages with no run/sign-off controls. Instructions show only the claim agent.
- `e2e/`: ten Playwright tests cover queue, instructions, claim work/decisions, sign-off notification, dispatch summaries, N payloads, evaluation payloads and an API-played scoreboard evaluation. Tests use isolated SQLite and the port-8003 stand-in, never hosted services or models.

## Set up

```sh
uv sync
npm install
```

For hosted/live work, set `DATABASE_URL`, `OPENROUTER_API_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_BASE_URL` in `.env`. `UNDERCOVER_AGENT` is optional and defaults to `http://127.0.0.1:8002`.

The default model is `openai/gpt-6-luna` with low reasoning. `MODEL_NAME` overrides it with no reasoning effort, recorded as `none`. Tests do not read `.env` and need no hosted service.

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

Final offline gate: with `.env` absent, `npm run check` passed 99 Python tests at 93.25% coverage, 28 Vitest tests at 90.14% statement coverage and ten Playwright tests without hosted/model calls. Playwright MCP review found no console errors or horizontal overflow at desktop/mobile sizes across dispatch, Scoreboard, evaluation copy and instructions. The one-seed Luna/Supabase/Langfuse verification remains to be recorded.
