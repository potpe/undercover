# Status

The pilot is being built. Phases 1 and 2 are complete.

- `backend/`: FastAPI with a SQLite store initialized from the approved business seed files; health, claims queue, full claim bundle, policy wording and handlers APIs; decision, sign-off and payment operations with the $1,000 guard.
- `agent/`: the original hello-world agent skeleton; claim tools arrive in Phase 3.
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

Phase 2 verification: `npm run check:python` passed with 28 tests and 98.41% Python coverage.
