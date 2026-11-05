# Harness implementation plan

## Outcome

Grow the pilot into a queue-working agent harness. A queue agent will plan and work a capped set of waiting claims through fresh claim subagents, with a durable to-do list, targeted document lookup, automatic recovery and a handler-readable summary. The existing policy, payout and sign-off rules remain in force.

No new agent framework or evaluation data is needed. The existing LangChain `create_agent` is used for both agent levels, and SQLite enforces the workflow rules that must not depend on model behavior.

## Design

### Runs, claims and to-dos

Add a `runs` table for the thread id, claim cap, optional named-claim target, actual model name, state, summary and timestamps. Add a `run_todos` table for the run id, claim id, agent-selected position, `to_do`/`in_progress`/`done`/`left_for_handler` state, attempt count, one-line result and last error.

Add `left_for_handler` as a claim state for a claim that has failed twice. It is not a policy decision and carries no fabricated payout or referral reason. A first failure returns an undecided claim to `waiting`; a second moves it to `left_for_handler`. Attempt counts belong to the run's to-do item, while the resulting claim state persists.

A queue run plans exactly `min(claim_cap, available_waiting_claims)` items, unless it targets one named claim. The backend rejects duplicate, unknown, non-waiting, over-cap and under-filled plans. This gives the agent freedom over order while making the cap deterministic.

The queue agent receives tools to list eligible claims, save its ordered plan, work a claim, read the current to-do list and finish with a summary. The work tool returns a one-line result rather than throwing on a claim failure. The finish operation returns 409 while any selected item is still `to_do` or `in_progress`, so the agent must check for and address missed claims before completing.

### Fresh claim work and recovery

Each work call creates a new claim agent with no conversation messages from earlier claims. Its thread id includes the run, claim and attempt number. It reads only that claim and the document sections it selects, then reports one line: decision and payout, or the error.

The backend owns attempt transitions:

- A successful decision, including an approval waiting for sign-off, marks the item `done`.
- A failure before a decision records the error, increments attempts and returns the claim to `waiting` after attempt one.
- The same claim is then eligible for one fresh retry.
- A second failure marks the item and claim `left_for_handler`.
- Repeating work for a completed item returns its existing result without invoking another subagent. Existing decision and payment guards remain unchanged.

### Document lookup

Index the Markdown sections of policy wording 2026.1 and claims manual 2026.2 in SQLite at initialization. Keep the index small and deterministic: split on section headings, search section titles and text with case-insensitive query terms, return ranked section ids, titles and short snippets, then return full content only for a requested section.

Use two claim-scoped API operations: one searches a required document (`policy_wording` or `claims_manual`) with a required query, and one reads a required section id. Reading a section stores a `document_reads` record and activity entry. The claim response exposes the ordered, de-duplicated sections read so the dashboard can show them separately from other activity. Remove the agent's whole-policy-wording tool and whole-document response path.

The queue and claim instruction sets are served separately from the backend. The claim instructions follow the manual's workflow: policy wording takes precedence; read the benefit, exclusions, general exclusions, documents, history and payment rules; use the manual for referrals, repeat claims, wild animals, receipt-free meals and interpretation notes. Neither instruction set contains handler answers or evaluation content.

### Main API additions

All operations retain unique operation ids, one-line docstrings, typed responses and fully required request bodies.

| Operation | Purpose |
|---|---|
| `POST /api/claims/{claim_id}/document-search` | Return ranked section metadata for a required document and query. |
| `POST /api/claims/{claim_id}/document-section` | Return and record one requested section for this claim. |
| `POST /api/runs` | Start a background queue run with a required thread id and claim cap. |
| `POST /api/claims/{claim_id}/run` | Preserve single-claim running by creating a targeted run with cap one. |
| `GET /api/runs/current` | Return the active or latest run, its to-dos, model and summary. |
| `POST /api/runs/{run_id}/plan` | Save the agent's ordered, cap-validated claim ids and actual model name. |
| `POST /api/runs/{run_id}/work` | Start one selected claim subagent and return its one-line outcome. |
| `POST /api/runs/{run_id}/finish` | Store the summary and complete only when no selected claim was missed. |
| `GET /api/agent/instructions` | Return separate read-only `queue` and `claim` instruction text. |

`GET /api/runs/{run_id}` supplies the queue agent and dashboard with a specific run when needed. Existing claim, decision, payment and sign-off APIs remain, with work on `left_for_handler` claims rejected unless a later product brief adds a reset workflow.

## Phase 1: Document sections, instructions and model

Build the section index, search/read APIs and document-read history. Split the instructions into queue and claim text, and change the default model to `deepseek/deepseek-v4.1-flash` with low reasoning effort while retaining `MODEL_NAME` overrides.

Update `docs/SPEC.md` so decisions use both the policy wording and manual; remove the manual from "Not for now" and describe section lookup and visible document reads.

### Success criteria

- [x] A backend test searching the manual for `meals without receipts` ranks section 10 and reading it returns the $35-per-traveler rule without returning the whole manual.
- [x] Searches for `wild animals`, `third claim in 12 months`, `unattended belongings` and `pre-existing conditions` return the relevant manual sections; policy searches return only the requested wording sections.
- [x] Reading a section twice for one claim produces one entry in its `documents_read` list and clear activity; searches and reads reject unknown documents and section ids without exposing any other file.
- [x] API tests confirm that neither search nor read responses contain the complete source document, and that the agent package no longer has a whole-document wording tool or direct data-file reads.
- [x] `GET /api/agent/instructions` returns distinct queue and claim instructions. The claim instructions require both documents, repeat-history review and a two- or three-sentence reason; neither text contains evaluation answers.
- [x] Model tests show DeepSeek V4.1 Flash with low reasoning by default, `openrouter/free` when overridden, and the actual selected model still recorded on claim activity.
- [x] `docs/SPEC.md` accurately includes the claims manual and no longer describes it as out of scope.
- [x] `npm run check:python` passes with Ruff, Pyright, all tests and at least 80% Python coverage.

## Phase 2: Durable runs, to-do planning and caps

Add run and to-do persistence, the current/specific run responses and plan validation. Keep the dashboard unchanged in this phase; prove the new state machine through backend tests first.

### Success criteria

- [ ] Creating a queue run with cap five returns 202 and an `in_progress` run with the required thread id and no to-dos while the agent is planning.
- [ ] A stub-driven plan of five waiting claims stores the five ids in the agent's chosen order as `to_do`, including position, zero attempts and empty result/error fields.
- [ ] Tests reject six items under cap five, duplicate ids, unknown or already-decided claims, an under-filled plan while more eligible claims remain, and a second plan for the same run.
- [ ] Caps one and 25 work, a cap above 25 or below one returns 422, and a run with fewer waiting claims than its cap plans only those claims.
- [ ] A named-claim run creates a target of exactly that waiting claim with cap one; it cannot plan a different claim.
- [ ] `GET /api/runs/current` and `GET /api/runs/{run_id}` return ordered to-dos, cap, target, model, status, summary and timestamps; the current response still shows the latest completed run for handlers.
- [ ] OpenAPI tests continue to prove unique operation ids, docstrings and required request fields.
- [ ] `npm run check:python` passes with all new state and validation tests and at least 80% coverage.

## Phase 3: Fresh claim subagents and automatic recovery

Connect selected to-do items to the existing claim agent, creating a fresh `create_agent` instance for every claim attempt. Replace the whole-document tool with search and section-read tools. Add the work operation's success, failure, retry and idempotency behavior.

### Success criteria

- [ ] A stub-model test works one claim through claim read, targeted wording/manual section reads, decision and permitted payment; its result is one line, its to-do is `done`, and a second claim agent starts with only its own claim message.
- [ ] An approval over $1,000 marks the to-do `done` while the claim remains `waiting_for_sign_off` and unpaid; the existing backend payment guard still returns 409 before sign-off.
- [ ] A stub provider exception on attempt one returns a retry outcome, records the error, leaves the claim `waiting`, sets the to-do back to `to_do`, and does not raise out of the work tool.
- [ ] Calling work again uses a fresh subagent and a new attempt-specific thread id; success after that retry marks the item `done` with no duplicate decision or payment.
- [ ] Two failed attempts leave the claim and to-do `left_for_handler`, with the second error visible and no decision or payment. A third work call does not invoke a model.
- [ ] A failure on one claim does not change any other claim or to-do, and repeating a completed work call returns the stored result without another model call.
- [ ] Tests cover interruption after a decision without creating a second decision or payment, and no failure path leaves a claim `in_progress`.
- [ ] `npm run check:python` passes with stub models only and at least 80% coverage.

## Phase 4: Queue-agent orchestration and completion check

Add the queue agent using the same LangChain stack and its API-only planning, work, to-do check and finish tools. Start queue runs and named-claim runs in the background, preserving immediate HTTP 202 responses and visible failures.

### Success criteria

- [ ] A five-claim stub-model run plans five claims, delegates each to a fresh claim subagent, updates every to-do and finishes with a short stored summary; the other 20 claims remain `waiting`.
- [ ] An uncapped trial using cap 25 works every waiting claim, while the agent rather than a Python loop chooses the to-do order through its plan tool.
- [ ] A named-claim run works only the requested claim and completes with one done to-do; decided, in-progress and left-for-handler targets return 409.
- [ ] In a mixed stub run, the first claim fails and retries or moves to a handler without stopping the remaining claims from being worked.
- [ ] A stub run that tries to finish with a selected claim still `to_do` receives 409, remains `in_progress`, and can finish only after the missed claim is worked or left for a handler. Claims outside the cap correctly remain waiting without blocking completion.
- [ ] Duplicate plan/work/finish calls cannot decide or pay a claim twice, and a failed queue run never leaves an undecided claim `in_progress`.
- [ ] The current run response exposes the actual default or override model and a final summary containing counts for done and left-for-handler items.
- [ ] `npm run check:python` passes all orchestration tests with no real model calls and at least 80% coverage.

## Phase 5: Live to-do list, run controls and document visibility

Extend the queue page with a current-run panel, a required numeric claim cap and a Run queue action. Poll claims and the current run only while a run is active. Show the agent-selected to-do order, each item's state/attempts/result/error, the run's model and final summary. Keep the named Run agent action on each waiting claim.

Add a Sections read panel to the claim page and show the new Left for handler claim state. Render separate Queue agent and Claim agent instruction sections, both read only, while retaining the existing responsive brand treatment.

### Success criteria

- [ ] Vitest covers cap input validation, starting one queue run, to-do rendering and transitions, polling that stops on completion, all four to-do states, the final summary, errors and single-claim running with strict TypeScript and no `any`.
- [ ] Component tests show ordered document sections on the claim page and separate queue/claim instructions without editing controls.
- [ ] Playwright tests prepare run/to-do/document-read state through API calls or stub-driven backend tests, never a model, then verify the queue's live to-do panel, cap-five behavior, Left for handler state and claim sections in the real browser.
- [ ] With the Playwright MCP browser, watch a stub-backed run move from an empty planning state to populated and completed to-dos; confirm claim statuses and results agree with the backend response.
- [ ] Desktop and mobile browser checks at 1440 px and 390 px show readable to-dos, errors, document sections and both instruction sets, with keyboard-accessible controls and no horizontal overflow or console errors.
- [ ] `npm run check:ts` and `npm run e2e` pass with Vitest coverage at least 80% and no model calls.

## Phase 6: Documentation, live five-claim run and full verification

Finish `docs/SPEC.md`, `docs/TECH_STACK.md` and `docs/STATUS.md` so they describe queue and named runs, fresh subagents, both instruction sets, section lookup, the retry limit, the DeepSeek default and the visible run/to-do state. Include reseeding and cap-five trial instructions.

For the live check, reseed first and temporarily remove `MODEL_NAME` from `.env` so the run proves the default rather than the override. Restore the student's override afterwards. Cap the real run at five claims.

### Success criteria

- [ ] The three product documents match the implemented behavior, including the manual, model default/override, run caps, failure handling and test commands; `docs/PLAN.md` remains unchanged.
- [ ] A final code/data-path review finds no direct agent reads of business files, no use of `data/evals/`, no held-out claims in the queue, no real payments or customer messages, and no additional agent framework.
- [ ] After reseeding, one real queue run capped at five on `deepseek/deepseek-v4.1-flash` processes exactly five claims and ends with a summary; the other 20 remain waiting, no claim is decided or paid twice, and no claim remains in progress.
- [ ] During that live run, the Playwright MCP browser shows the to-do list filling and updating, the selected model, each claim's activity and sections read, and any sign-off or handler state correctly.
- [ ] The final browser check covers queue, current run, full claim, sections read and both instruction sets with no console errors.
- [ ] From a clean test-server start, `npm run check` passes Ruff, Pyright, pytest, Prettier, ESLint, tsc, Vitest and all Playwright tests; both coverage reports remain at least 80%, and automated tests make no model calls.

## Build checkpoints

Implement the phases in order. After each phase, tick only verified criteria here, update `docs/STATUS.md`, and commit with the pre-commit hook enabled. Never push. Keep `docs/PLAN.md` untouched, and run the complete `npm run check` at the end.
