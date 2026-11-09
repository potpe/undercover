# Code standards

1. Keep it simple, do not overcomplicate.
2. Comments only when necessary.
3. Be concise: a short README and no emojis.
4. IMPORTANT: avoid overly defensive code. Handle exceptions only when necessary.
5. Use uv: ALWAYS `uv run`, NEVER `python3`.
6. TypeScript: strict mode, never `any`.
7. Keep the agent in four parts: the claim graph and its nodes, the queue agent, checkpoint/tracing adapters, and `service.py` as the service entry point. Graph nodes take the place of middleware; do not add another agent framework.
