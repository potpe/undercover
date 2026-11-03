# Status

The UnderCover claims pilot is complete.

- `backend/`: FastAPI and SQLite, initialized only from the approved business seed files. APIs cover the 25-claim queue, full claim bundles, policy wording, handlers, agent instructions, runs, decisions, activity, sign-off and payment. The backend blocks payments over $1,000 until an eligible handler signs off. Agent runs that finish without a decision are marked failed and returned to Waiting.
- `agent/`: LangChain `create_agent` with `run(claim_id, thread_id)` and API-only tools for claim data, policy wording, decisions and payment. The default model is `openai/gpt-6-luna` with low reasoning effort; `MODEL_NAME` overrides it. Each run records the selected model in the dashboard's Agent activity feed.
- `dashboard/`: branded, responsive claims queue, full claim workspace, live agent activity, decision reasons and policy citations, read-only instructions, and handler sign-off/payment controls.
- `e2e/`: five Playwright tests covering the queue, instructions, claim details, decisions and over-limit sign-off. They use an isolated SQLite database and never call a model.

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

Final `npm run check`: all Ruff, Pyright, Prettier, ESLint and tsc checks passed; 37 Python tests passed with 98.47% coverage; 14 Vitest tests passed with 93.19% statement coverage; five Playwright tests passed without model calls.
