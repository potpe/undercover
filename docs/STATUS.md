# Status

The pilot is being built. Phase 1 is complete.

- `backend/`: FastAPI with a SQLite store initialized from the approved business seed files; health, claims queue, full claim bundle, policy wording and handlers APIs.
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

Phase 1 verification: `npm run check:python` passed with 13 tests and 98.76% Python coverage.
