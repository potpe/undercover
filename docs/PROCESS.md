# Process

`uv sync` and `npm install` at the repo root install everything, including a git pre-commit hook. `npm run dev` starts the backend on port 8000, the agent service on port 8002, the context MCP service at `http://127.0.0.1:8006/mcp` and the dashboard on port 5173. Backend and agent receive `.env`; context runs without environment variables. `UNDERCOVER_API` selects the app URL (default `http://localhost:8000`); `UNDERCOVER_CONTEXT` selects the context MCP URL (default `http://127.0.0.1:8006/mcp`); `UNDERCOVER_GATEWAY_URL` enables Gateway model-tool routing when configured; and `UNDERCOVER_MEMORY_ID` enables optional operational-claim memory. These settings are read when clients/claim graphs are constructed. Gateway signing and enabled AgentCore Memory use the AWS CLI credentials and region; memory remains disabled when its id is absent and is not used for evaluations. On the queue page, choose 1–25 agents at once (default 3) and start a dispatch; N limits concurrency, not the number of waiting/interrupted claims. On Scoreboard, choose the evaluation or held-out set, whole or five `tiny` claims, and N. Only one dispatch/evaluation runs at once. `uv run -m agent CLM-1001` works one named claim with no run; bare `uv run -m agent` is invalid.

Stop the services before `npm run seed`, which intentionally resets the database selected by `DATABASE_URL` or `DATABASE_PATH`, including claims, runs, the scoreboard, ledgers and checkpoint state. It restores 26 waiting operational claims (the original 25 plus security claim `CLM-1901`) and the separate held-out sources and handler labels. The security handler decision is never seeded; evaluations still use only their original labelled sources. Normal startup never reseeds existing work. Evaluations create their own copies and need no reseed. Tests use isolated SQLite and stubs without loading `.env`; no Supabase, Langfuse or OpenRouter connection is needed.

`npm run check` runs every check:

- Python: ruff (lint and format), pyright, and pytest with 80% coverage
- TypeScript: ESLint, Prettier, tsc, and vitest with 80% coverage
- Playwright end-to-end tests
- An isolated backend import with only the `server` dependency group and an isolated context import with only the `context` dependency group; the project environment is left intact

The pre-commit hook runs the format, lint, type and unit test checks on every commit.

Set `UNDERCOVER_API_KEY` to a random value of at least 32 characters in `.env` for live agent work. Claim, screening and improver models request at most 7,152 output tokens, matching the observed provider allowance; if the allowance falls again, add credits or lower this application constant and check the input size rather than retrying the same oversized request. Every agent-side backend request sends it in `X-API-Key`, including clients created after dotenv loads. The backend starts without it, but agent-only requests return 401 and the agent service refuses to start. Shared claim reads also accept a handler's bearer token; dashboard actions always require handler sign-in. The named-claim CLI uses a separate internal start operation and invokes the claim graph without impersonating a handler.

Automated fixtures use a fixed, non-secret agent key and separate local handler tokens. Tests never read root `.env` or contact hosted Auth, models or AWS. Before the full offline gate, move root `.env` aside and restore it in a `finally` block.

The claim model uses one claim-scoped tool surface through either the direct app adapter or the configured Gateway adapter. The graph supplies claim/policy/customer ids and the required persisted decision maker (`UnderCover Agent (configuration version N)`; screening referrals use `Security check`); payment remains graph-only. Bookkeeping and the security check's read/referral always use the direct API-key client. `UNDERCOVER_API`, `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` and `UNDERCOVER_MEMORY_ID` are read when clients/claim graphs are constructed, allowing the CLI's late dotenv load. MCP's short async operations run on worker-thread event loops so the graph remains synchronous and does not alter the main thread's event loop. A Gateway payment refusal is recorded directly and held for the existing atomic handler sign-off/payment flow; this bookkeeping route is agent-key-only and excluded from `/api/agent/openapi.json` and Gateway.

The context service is a separate, read-only MCP package. Its seven governance files are immutable and each tool result has an `answer` plus flat `sources` containing file/system/owner/reviewed provenance. Backend seeding reads plans from `context/plans.yaml` and stores complete versioned wording/manual documents in its own table; backend requests and ordinary imports do not load the context package. The instructions append those backend-stored documents. Legacy plan and document copies in `data/` remain but are no longer product inputs; `data/company.json` still owns the approval limit. The app's generated agent API document contains exactly six operations: `get_claim`, `get_policy`, `get_claim_history`, `calculate_payout`, `record_decision` and `pay_claim`; context owns document search/section reads. Agent-key-only `POST /api/claims/{id}/steps` records typed activity steps in the existing timeline and is excluded from the six-operation OpenAPI document and Gateway; ordinary message activity bookkeeping is retained. The separate context Lambda uses `context.server.handler`, a Function URL with `AWS_IAM`, its context dependency group and no environment variables. `npm run check:context` performs its isolated import check, and `npm run check` includes that plus the isolated server import. No AWS resources are created by local work.

Accepted decisions contain `section`, `calculation` (approvals only), `decided_by`, UTC `decided_at` and the backend document's `manual_version` (`2026.2`), alongside the decision, payout and reason. A calculation has `lines: [{step, amount}]` and a `payout`. `calculate_payout` replaces the working calculation within the current `failed_attempts + 1` attempt; transient resume preserves that attempt, while a fresh ordinary retry advances it. The accepted approval snapshots its calculation; decline/referral records have no calculation. Identical decision replays preserve the original maker/time/version.

Section 14 validation returns specific custom 422 errors for blank reason/section/maker, invalid payout/decision combinations, and missing, stale or payout-mismatched current-attempt calculations. The model receives the refusal and may correct the `record_decision` tool call; rejected drafts are never recorded. Tests that create approvals through shared backend/agent/E2E helpers first calculate one `expense` under `emergency_medical` for the expected payout plus the plan deductible, then use the exact calculated payout. This keeps existing expected amounts intact. Judge/evaluation code reads `section`; evaluation scoring does not change.

When `UNDERCOVER_MEMORY_ID` is set, operational claim graphs construct `AgentCoreMemoryStore` with AWS CLI credentials and region; evaluations get no memory store or tool. The store is separate from the existing synchronous checkpointer and four-connection Postgres pool. `search_memory(query)` searches only `("customers", customer_id)`, limits results to five, returns text only and excludes the active claim summary using each result's `value["namespaces"]`; backend claim history remains authoritative for section 9. A deterministic post-decision node writes customer `HumanMessage` fields `summary`, `claim_type`, `incident_date`, `incident_location`, `items` and agent `AIMessage` fields `decision`, `payout`, `section`, `reason`. It excludes story/documents, security referrals and evaluation copies. Save errors warn and preserve the accepted decision/workflow; search errors reach the model as refusals. The two external SDK writes are not atomic, so do not assume exactly-once completion if a process stops between them.

The existing claim activity feed displays typed chronological steps alongside ordinary bookkeeping messages. The step kinds are `tool`, `lookup`, `memory_search`, `memory_save` and `refusal`; each carries typed details, while old activity records remain general activity entries. Context lookup provenance appears grouped by file under **Context used**; the decision view includes calculation, maker, timestamp, manual version and sign-off/payment facts. Gateway holds identify the Gateway refusal in the handler explanation. The agent sends steps through agent-key-only `POST /api/claims/{id}/steps`, outside `/api/agent/openapi.json`; the existing message activity endpoint remains for bookkeeping.

The first claim activity is the hidden-instruction check's result. Flagged claims become zero-payout referrals with the checker reason and `Security check` section. Each raw screening response contributes reported usage, including up to three structured-answer attempts. Saved claim progress does not repeat completed screening. Automated tests stub the checker independently of the claim model.

Signing off an eligible held payout records its payment immediately and atomically in the backend. The agent service need not be running, and any later resume finishes without another payment call. The agent's public payment route refuses every amount above the current approval limit, even after sign-off. The key-or-handler-protected `/api/agent/openapi.json` is the generated six-operation app contract for the Gateway; this build does not deploy it.

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
