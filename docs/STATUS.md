# Status

The pilot is being built. Phases 1 through 4 are complete.

- `backend/`: FastAPI with a SQLite store initialized from the approved business seed files; health, claims queue, full claim bundle, policy wording and handlers APIs; decision, sign-off and payment operations with the $1,000 guard.
- `agent/`: LangChain `create_agent` with a two-argument entry point and API-only tools for claim data, policy wording, decisions and payment; backend-run orchestration and ordered activity.
- `dashboard/`: branded, responsive claims queue and read-only agent instructions, with loading and error states.
- `e2e/`: browser tests for the 25-claim queue and read-only instructions; no model calls.

The local database is `backend/undercover.db` and is git-ignored. Tests use isolated temporary databases.

## Run

```sh
uv sync
npm install
npm run dev
```

The dashboard is at `http://localhost:5173` and the backend at `http://localhost:8000`.

## Check

```sh
npm run check
```

Phase 4 verification: Python checks remain green; `npm run check:ts` passed seven tests with 98.07% statement coverage, and two Playwright tests passed. Desktop and mobile browser checks found no horizontal overflow.
