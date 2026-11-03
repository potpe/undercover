# Status

The pilot is being built. Phases 1 through 3 are complete.

- `backend/`: FastAPI with a SQLite store initialized from the approved business seed files; health, claims queue, full claim bundle, policy wording and handlers APIs; decision, sign-off and payment operations with the $1,000 guard.
- `agent/`: LangChain `create_agent` with a two-argument entry point and API-only tools for claim data, policy wording, decisions and payment; backend-run orchestration and ordered activity.
- `dashboard/`: the original backend health page; the claims interface arrives in Phase 4.
- `e2e/`: the original health-page test.

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

Phase 3 verification: `npm run check:python` passed with 36 tests and 98.45% Python coverage. All agent tests use stub models.
