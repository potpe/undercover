# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Keep the claim graph, dispatcher, checkpoint adapter, tracing adapter and `service.py` as the service entry point; add only focused route/tool adapters around the hand-written graph. Graph nodes take the place of middleware; do not add another agent framework.
8. Keep business state, the approval limit, idempotent decisions/payments and record-built summaries in the backend.
9. Use stubs and isolated SQLite in tests; never call a model or hosted service, and never weaken a check or coverage threshold.
10. The dispatcher uses no model and no checkpoint of its own. Select all waiting/interrupted operational claims oldest first, use one `Send` per claim and `max_concurrency=N`; N is never a claim cap. Keep transient retries on the same thread and one fresh retry after an ordinary error.
11. Use synchronous psycopg and durable claim checkpoints on Windows. Share one four-connection pool per agent process, with a separate saver per claim, and wait rather than opening extra connections.
12. Record OpenRouter-reported tokens/cost and active work time with the current run, or explicitly with no run. Never estimate usage or count a sign-off wait as active work.
13. Evaluations use fresh isolated copies through the same claim graph. Handler sources and decisions are backend-only: no queue, dispatch, claim read, prompt, tool or agent-service operation may expose them. Held evaluation approvals need no sign-off and must not retain resumable progress.
14. Keep the claim model surface claim-scoped and shared by `agent/direct.py` and `agent/gateway.py`. Bind claim, policy and customer identifiers, plus the decision maker, from graph runtime context; never accept them from model tool inputs. Persist required `decided_by` as `UnderCover Agent (configuration version N)` for agent decisions and `Security check` for screening referrals. Decision recording is a model tool; payment is graph-only. Bookkeeping, runs, versions, improver work and security read/referral stay on the direct API-key route.
15. Read `UNDERCOVER_API`, `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` and `UNDERCOVER_MEMORY_ID` at client or graph construction, not module import. `UNDERCOVER_MEMORY_ID` is unused until Phase 4. Keep MCP calls synchronous to the graph by running each short async call in its own worker-thread event loop; do not use or replace the main-thread event loop.
16. Use one canonical claim serializer for direct/Gateway claim reads and the direct security check. Preserve app/context/Gateway refusal reasons as model refusals. A Gateway payment refusal is recorded through the direct API and uses the agent-key-only hold bookkeeping operation; keep it outside the public agent OpenAPI and Gateway. Sign-off/payment remain atomic in the backend and a resumed graph must not pay twice.
