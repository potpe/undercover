# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts the backend on port 8000, the agent service on port 8002 and the dashboard on port 5173, passing `.env` to the Python services. On the queue page, choose 1–25 agents at once (default 3) and start a dispatch; N limits concurrency, not the number of waiting/interrupted claims. On Scoreboard, choose the evaluation or held-out set, whole or five `tiny` claims, and N. Only one dispatch/evaluation runs at once. `uv run -m agent CLM-1001` works one named claim with no run; bare `uv run -m agent` is invalid.

Stop the services before `npm run seed`, which intentionally resets the database selected by `DATABASE_URL` or `DATABASE_PATH`, including claims, runs, the scoreboard, ledgers and checkpoint state. It restores 26 waiting operational claims (the original 25 plus security claim `CLM-1901`) and the separate held-out sources and handler labels. The security handler decision is never seeded; evaluations still use only their original labelled sources. Normal startup never reseeds existing work. Evaluations create their own copies and need no reseed. Tests use isolated SQLite and stubs without loading `.env`; no Supabase, Langfuse or OpenRouter connection is needed.

`npm run check` runs every check:

- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests
- An isolated import with only the `server` dependency group; the project environment is left intact

The pre-commit hook runs the format, lint, type and unit test checks on every commit.

Set `UNDERCOVER_API_KEY` to a random value of at least 32 characters in `.env` for live agent work. Claim, screening and improver models request at most 7,152 output tokens, matching the observed provider allowance; if the allowance falls again, add credits or lower this application constant and check the input size rather than retrying the same oversized request. Every agent-side backend request sends it in `X-API-Key`, including clients created after dotenv loads. The backend starts without it, but agent-only requests return 401 and the agent service refuses to start. Shared claim reads also accept a handler's bearer token; dashboard actions always require handler sign-in. The named-claim CLI uses a separate internal start operation and invokes the claim graph without impersonating a handler.

Automated fixtures use a fixed, non-secret agent key and separate local handler tokens. Tests never read root `.env` or contact hosted Auth, models or AWS. Before the full offline gate, move root `.env` aside and restore it in a `finally` block.

The claim model uses one claim-scoped tool surface through either the direct app adapter or the configured Gateway adapter. The graph supplies claim/policy/customer ids and the required persisted decision maker (`UnderCover Agent (configuration version N)`; screening referrals use `Security check`); payment remains graph-only. Bookkeeping and the security check's read/referral always use the direct API-key client. `UNDERCOVER_API`, `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` and `UNDERCOVER_MEMORY_ID` are read when clients/claim graphs are constructed, allowing the CLI's late dotenv load. `UNDERCOVER_MEMORY_ID` is unused until Phase 4. MCP's short async operations run on worker-thread event loops so the graph remains synchronous and does not alter the main thread's event loop. A Gateway payment refusal is recorded directly and held for the existing atomic handler sign-off/payment flow; this bookkeeping route is agent-key-only and excluded from `/api/agent/openapi.json` and Gateway.

The first claim activity is the hidden-instruction check's result. Flagged claims become zero-payout referrals with the checker reason and `Security check` section. Each raw screening response contributes reported usage, including up to three structured-answer attempts. Saved claim progress does not repeat completed screening. Automated tests stub the checker independently of the claim model.

Signing off an eligible held payout records its payment immediately and atomically in the backend. The agent service need not be running, and any later resume finishes without another payment call. The agent's public payment route refuses every amount above the current approval limit, even after sign-off. The key-or-handler-protected `/api/agent/openapi.json` is the generated eight-operation contract for the later Gateway; this build does not deploy it.

`npm run build` writes `dashboard/dist` using the Supabase build variables from root `.env` or the process environment; it also works with `.env` absent. Tests use explicit stand-in values and never read root `.env`. Then `uv run -m uvicorn backend.main:app --env-file .env` serves the dashboard and API together on port 8000. Deep links outside `/api/` return the SPA; unknown API routes stay JSON 404. `npm run dev` is unchanged. `backend.main.handler` is the Lambda entry point and disables lifespan startup, so Lambda invocation does not seed the database. For a local Lambda-mode check, supply `AWS_LAMBDA_FUNCTION_NAME` and the transaction-pooler URL on port 6543 without permanently changing `.env`; Lambda uses request-scoped unprepared connections. Never deploy or create AWS resources as part of these checks.

## Planning

When asked to plan, as in "Please plan the pilot", write the plan to `docs/PLAN.md` and write no code.

- Split the work into phases, usually 5 or 6, each building on the last.
- Give each phase detailed success criteria that you can verify yourself: tests that pass, commands and their output, API calls and their responses, and pages you check in the browser with the Playwright MCP server. Write each one as a checkbox.
- Then stop and wait until you are asked to implement the plan.

## Implementing

When asked to implement the plan, as in "Please implement the plan for the pilot", build every phase in order without stopping. For each phase:

1. Build it, with tests.
2. Check every success criterion and fix whatever fails.
3. Tick off its criteria in `docs/PLAN.md` and update `docs/STATUS.md`.
4. Commit. The pre-commit hook runs the checks; if it fails, fix the problem and commit again.

When the last phase is done, run `npm run check` and report the results.

## Rules

- Add tests with each change. Tests never call a model; use a stub model.
- Outside a plan, commit each change once you have checked it.
- Run `npm run check` before you hand work over.
- Never use `git commit --no-verify`.
- Never lower a threshold, turn off a rule or exclude a file to get a green run. If a rule looks wrong, stop and ask.
- Keep `docs/STATUS.md` up to date.
- Never push.
- Don't open `data/evals/` unless you're asked to.
- Everything must work on Windows: write `package.json` scripts as `uv run` commands or `.mjs` files. Keep the agent and its Postgres checkpoint connection synchronous because async Postgres connections do not work with Windows' default event loop.
