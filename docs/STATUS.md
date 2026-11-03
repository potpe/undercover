# Status

The pilot is being built. Phases 1 through 5 are complete.

- `backend/`: FastAPI with a SQLite store initialized from the approved business seed files; health, claims queue, full claim bundle, policy wording and handlers APIs; decision, sign-off and payment operations with the $1,000 guard.
- `agent/`: LangChain `create_agent` with a two-argument entry point and API-only tools for claim data, policy wording, decisions and payment; backend-run orchestration and ordered activity.
- `dashboard/`: branded, responsive claims queue, full claim workspace, live agent activity, decision details, read-only instructions and handler sign-off/payment controls.
- `e2e/`: five browser tests for the queue, instructions, full claim details, recorded decisions and over-limit sign-off; an isolated SQLite database is reset on each test-server start, and no model is called.

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

Phase 5 verification: `npm run check:ts` passed 14 tests with 93.19% statement coverage, and five Playwright tests passed. Desktop/mobile checks covered full details, long documents and sign-off; the browser console had no errors.
