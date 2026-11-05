# Tech stack

- `backend/`: FastAPI and SQLite. Every API operation has an `operation_id`, a one-line docstring and no optional inputs. APIs cover claims, section-level document lookup and reads, capped runs, to-dos, attempts and run summaries. Paying a claim is a separate operation that takes the amount, and the backend enforces the $1,000 sign-off rule.
- `agent/`: LangChain's `create_agent` on OpenRouter. `agent/main.py` runs one fresh claim subagent with a claim id and thread id; `agent/queue.py` organizes a stored run with list, plan, work, to-do check and finish tools. Agents reach claims, instructions and document sections only through backend APIs. The default model is `deepseek/deepseek-v4.1-flash` with low reasoning effort; `MODEL_NAME` overrides it, such as `openrouter/free`.
- `dashboard/`: React, Vite and TypeScript. The queue shows current-run to-dos and controls; claim pages show activity, decisions and document sections read.
- `e2e/`: Playwright tests with an isolated SQLite database and a dedicated dashboard port. Tests never call a model.
- `data/`: the business data the backend starts from. The agent never sees `data/evals/`, which holds the answer key.

Python 3.12 with uv, all in the root `pyproject.toml`. Node 26. The OpenRouter key is `OPENROUTER_API_KEY` in `.env`.
