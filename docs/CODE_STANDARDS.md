# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Keep the agent in five parts: the claim graph, dispatcher, checkpoint adapter, tracing adapter and `service.py` as the service entry point. Graph nodes take the place of middleware; do not add another agent framework.
8. Keep business state, the approval limit, idempotent decisions/payments and record-built summaries in the backend.
9. Use stubs and isolated SQLite in tests; never call a model or hosted service, and never weaken a check or coverage threshold.
10. The dispatcher uses no model and no checkpoint of its own. Select all waiting/interrupted operational claims oldest first, use one `Send` per claim and `max_concurrency=N`; N is never a claim cap. Keep transient retries on the same thread and one fresh retry after an ordinary error.
11. Use synchronous psycopg and durable claim checkpoints on Windows. Share one four-connection pool per agent process, with a separate saver per claim, and wait rather than opening extra connections.
12. Record OpenRouter-reported tokens/cost and active work time with the current run, or explicitly with no run. Never estimate usage or count a sign-off wait as active work.
13. Evaluations use fresh isolated copies through the same claim graph. Handler sources and decisions are backend-only: no queue, dispatch, claim read, prompt, tool or agent-service operation may expose them. Held evaluation approvals need no sign-off and must not retain resumable progress.
