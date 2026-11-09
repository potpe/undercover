# Status

The durable-state build is in progress. Phases 1 through 3 are complete.

- `backend/`: FastAPI and SQLAlchemy Core, using SQLite by default or Supabase Postgres when `DATABASE_URL` is set. Postgres uses the `undercover` schema and at most four pooled connections per process. APIs cover claims, section-level policy/manual reads, deterministic rule-based payouts, capped runs, durable to-dos, attempts, decisions, activity, sign-off, payment and summaries. Payments over $1,000 stay blocked until sign-off. First failures return claims to Waiting; second failures leave them for a handler; completed work cannot be repeated.
- `agent/`: a hand-written synchronous LangGraph claim graph with API-only claim, document and payout tools, structured decisions, interrupt/resume and graph-owned payment. The queue agent remains LangChain `create_agent`. Checkpoints use Supabase `PostgresSaver` or local SQLite, with sync durability and stable claim/thread Langfuse sessions. The default is `deepseek/deepseek-v4.1-flash` with low reasoning; `MODEL_NAME` overrides it.
- `dashboard/`: branded, responsive claims queue with capped Run queue controls and a live current-run/to-do panel; full claim workspace with activity and sections read; separate read-only queue/claim instructions; handler sign-off/payment controls and Left for handler states.
- `e2e/`: seven Playwright tests covering the queue, both instruction sets, claim details, decisions, sign-off, run to-dos, document reads and cap validation. They use an isolated SQLite database, a dedicated dashboard port and never call a model.

## Set up

```sh
uv sync
npm install
```

Set `OPENROUTER_API_KEY` in `.env`. Optionally set `MODEL_NAME`, for example `MODEL_NAME=openrouter/free`.

## Run

```sh
npm run dev
```

Open `http://localhost:5173`. The backend listens at `http://localhost:8000`.

For a low-cost trial, leave Claim cap at five and select Run queue. To work the whole queue, set the cap to 25. A waiting claim can also be run on its own from its claim page. The current-run panel shows planning, attempts, results and the final summary.

The local database is `backend/undercover.db` and is git-ignored. To reset all decisions, runs, to-dos and activity, stop the servers, remove the database, then restart:

```sh
uv run python -c 'from pathlib import Path; Path("backend/undercover.db").unlink(missing_ok=True)'
```

## Check

```sh
npm run check
```

This runs Ruff, Pyright, pytest with at least 80% coverage, Prettier, ESLint, tsc, Vitest with at least 80% coverage, and Playwright. Stop the development servers first so Playwright can start its isolated test database.

## Verification

After reseeding, the real DeepSeek V4.1 Flash run with cap five completed `CLM-1001` through `CLM-1005` on the first attempt and left the other 20 claims waiting. `CLM-1001` used the calculator tool to record and pay exactly $420.00; `CLM-1003` approved $3,750.00 and remained unpaid pending sign-off. Each claim recorded its activity and sections read, and the final browser check had no console errors. The student's original `MODEL_NAME` override was restored after the live check.

Durable Phase 3: `npm run check:python` passed 82 tests with 94.96% coverage. Stub-driven graph tests cover under-limit payment, over-limit interrupt/resume, same-thread completion, tool refusal, the 30-call cap and idempotent checkpoint replay. The previous full gate remains the baseline for later phases.
