# Status

The durable-state build is in progress. Phase 1 is complete.

- `backend/`: FastAPI and SQLite, initialized only from approved seed files. APIs cover claims, section-level policy/manual reads, deterministic rule-based payouts, capped runs, durable to-dos, attempts, decisions, activity, sign-off, payment and summaries. Payments over $1,000 stay blocked until sign-off. First failures return claims to Waiting; second failures leave them for a handler; completed work cannot be repeated.
- `agent/`: LangChain `create_agent` on OpenRouter. A queue agent lists, plans, delegates, checks and finishes a run; each claim attempt is a fresh claim subagent with API-only claim, document, payout, decision and payment tools. Claim work uses a stable claim/thread Langfuse session when Langfuse keys are configured. The default is `deepseek/deepseek-v4.1-flash` with low reasoning; `MODEL_NAME` overrides it.
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

Durable Phase 1: `npm run check:python` passed 73 tests with 96.43% coverage. The previous full Python, TypeScript and Playwright gate remains the baseline for later phases.
