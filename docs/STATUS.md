# Status

The harness build is in progress. Phases 1 through 5 are complete.

- `backend/`: FastAPI and SQLite, initialized only from the approved business seed files. APIs cover the 25-claim queue, full claim bundles, section-level policy/manual search and reads, handlers, split agent instructions, decisions, activity, sign-off and payment. The backend blocks payments over $1,000 until an eligible handler signs off. Agent runs that finish without a decision are marked failed and returned to Waiting.
- `agent/`: LangChain `create_agent` with `run(claim_id, thread_id)` and API-only tools for claim data, policy wording, decisions and payment. The default model is `deepseek/deepseek-v4.1-flash` with low reasoning effort; `MODEL_NAME` overrides it. Each run records the selected model in the dashboard's Agent activity feed.
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

The local database is `backend/undercover.db` and is git-ignored. To reset all pilot decisions and activity, stop the servers, remove the database, then restart:

```sh
uv run python -c 'from pathlib import Path; Path("backend/undercover.db").unlink(missing_ok=True)'
```

## Check

```sh
npm run check
```

This runs Ruff, Pyright, pytest with at least 80% coverage, Prettier, ESLint, tsc, Vitest with at least 80% coverage, and Playwright. Stop the development servers first so Playwright can start its isolated test database.

## Verification

The real-model smoke run approved `CLM-1001` for $420.00, cited policy sections 3 and 10, recorded a three-sentence reason and ended Decided. The over-limit flow remained blocked before sign-off and recorded $3,750.00 after Dana signed off. Final browser checks had no console errors.

Harness Phase 5: `npm run check:ts` passed 17 tests with 92.18% statement coverage and seven Playwright tests passed. Desktop/mobile checks covered to-dos, errors, sections and instructions. A temporary stub-backed browser run moved five planned items to done while 20 claims outside the cap stayed waiting.
