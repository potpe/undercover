# Configuration, improvement and handler sign-in implementation plan

## Outcome

Turn the claim agent into a versioned, measurable and handler-controlled product. Handlers will sign in with Supabase Auth; the backend will own configuration and recipe versions, platform settings, judge grades, improvement rounds, spending and keep/rollback decisions. The dashboard will replace Instructions with Configuration and add Improvement and Settings pages, with a consistent enterprise presentation in light and dark mode.

The existing claim graph, claim tools, isolated evaluation copies, deterministic dispatcher, durable claim threads and record-built scoring remain the foundation. The improver is a separate LangChain agent, not a change to the claim graph. No sign-up, real payment, customer messaging, API key, cloud deployment or other later-week work is added.

## Course alignment and scope

> SIDENOTE: For consistency with the course, please read `labs/week2day5.ipynb` and build the judge, the configuration's versions and the improver in the same style as the lab. Where the lab and this brief differ, follow the brief. Include this sidenote in your plan too.

The lab has been read in full, including its product setup. Reuse its Jev rubric and three questions, its F/C/B/A ordering and 0–3 score, its four improver tool names, its first recipe, `difflib.unified_diff`, `HumanInTheLoopMiddleware`, `Command(resume=...)` approval/rejection and its hand-transparent comparison rule. Follow the brief where the product differs: persistent backend records replace notebook globals, whole evaluations run inside rounds, the judge finishes evaluations in parallel with a 10-second call timeout, approvals use durable Postgres checkpoints, and spending includes the improver, every round evaluation and the judge.

Read `docs/week2day5_product_IMPROVE.md` as the controlling brief. Leave the earlier plans unchanged and tick only criteria in this file that have evidence. Build the phases in the requested order and write no production code until implementation is requested.

## Current-build facts to preserve or adapt

- The current backend stores complete handler label JSON, so retain and test `reason` and `reference`; do not invent a replacement answer-key store. Only the backend judge and the improver's evaluation-set view may read them.
- The claim backend client is currently in `agent/main.py`, not `agent/api.py`, and sign-off is in `ClaimWorkspace.tsx`/`DecisionSection.tsx`, not `SignOffForm.tsx`. Follow the brief's intent at these actual locations. Add a separate `agent/improver_api.py`; do not add score, evaluation-start or handler-decision functions to the claim agent's client/tools.
- Replace `SignOffRequest.handler_id` and the handler picker with the verified signed-in handler. Seed handler email addresses and change Marcus Oyelaran and Priya Raman to `can_approve_over_limit=false`; Dana Whitfield remains the senior handler.
- Replace per-request `MODEL_NAME` model construction for runs with the run's pinned version. Persist an unspecified reasoning effort as `default`, not `none`; `none` remains available as a real Luna level where the allowed-model file lists it.
- Keep the synchronous service lock in `agent/service.py`, but track what owns it so refusals name the active dispatch, evaluation or improver stretch. A waiting round releases the slot; its evaluations run inside an already-held improver stretch rather than competing for it.
- `finish_run` is the judging hook and must return well inside the existing 30-second client wait. The existing four-connection synchronous checkpoint pool and two-connection backend pool remain unchanged; an improver saver gets its own instance, not another unbounded pool.
- Vite currently has no root `envDir`. Pass the repo root as Vite's env directory only in the `dev:dashboard` command, so development reads root `.env` while Vitest and Playwright receive explicit stand-in values and never read it.

## Design

### 1. Authentication and authorization

Add `backend/auth.py` and `@supabase/supabase-js` to the dashboard. Add `pyjwt[crypto]` to the main Python dependencies with `uv add "pyjwt[crypto]"`.

- The dashboard uses `signInWithPassword`, `VITE_SUPABASE_URL` and `VITE_SUPABASE_PUBLISHABLE_KEY`. It has a sign-in page only, restores an existing session, shows the signed-in handler's name/email and signs out through Supabase. Every product page is behind the session gate.
- The dashboard sends the access token as `Authorization: Bearer ...` on every handler action. A small session/me operation returns the backend's handler record and permissions after token verification.
- The backend reads the Supabase origin once, constructs a `PyJWKClient` for `/auth/v1/.well-known/jwks.json`, verifies the signature and expiry with audience `authenticated`, and resolves the token email to a seeded handler. Missing, malformed, expired, wrong-audience or unknown-handler credentials cannot be used for an action. Use 401 for authentication failure and 403 for an authenticated handler without the required permission.
- Apply authentication to sign-off, named-claim work, dispatch/evaluation starts and all new round/proposal/settings mutations. Agent-facing claim writes and internal service/improver calls remain without handler sign-in until Week 3, as the brief provides.
- A handler whose seeded `can_approve_over_limit` is true (initially only Dana) may sign off a held payout, start or stop a round, approve or reject a proposal, or change a platform setting. Never hard-code an email for authorization. Marcus and Priya retain ordinary signed-in access and dispatch/evaluation actions, but the held-payout, Improvement and Settings UI explicitly explains what they cannot do and why. Backend checks remain authoritative.
- Signing off stores the verified handler id and existing timestamp flow, then resumes the same claim thread. No handler id is accepted from the browser.

### 2. Backend-owned settings, versions and diffs

Add focused backend modules for configuration/improvement records rather than placing the new workflow in route functions. Extend the existing SQLAlchemy Core metadata and destructive reseed so SQLite and Supabase share the same behavior.

Persist:

- **Configuration versions:** integer version, complete instructions, model, reasoning effort, improver reason, proposer, approving handler and proposed/approved timestamps. Version 1 comes from `backend/agent_instructions.md` at `openai/gpt-6-luna`/low. An approved proposal creates an immutable next version; a pending or rejected proposal does not change configuration history or the current pointer.
- **Recipe versions:** the same audit fields and complete recipe text. Version 1 comes from a new `backend/improver_recipe.md` containing the lab's five-step first recipe verbatim in substance, including a rule for each mistake and recipe improvement as the last step.
- **Runs:** add the configuration version and nullable owning round/purpose. Dispatches and Scoreboard evaluations pin the current version at creation; round evaluations pin the baseline/current or candidate version explicitly. Retain actual model/reasoning on the run for easy inspection.
- **Claim grades:** one row per evaluation copy with nullable numeric grade/letter when ungraded, same-grounds and same-section probabilities, and Jev's reported cost. An undecided copy has deterministic F/0 without a judge call.
- **Improvement rounds and proposals:** round thread id, open/finished/stopped status, detailed stage, recipe version, immutable settings/catalog snapshot, starting/current version, candidate version, before/after held-out scores, verdict/reason, evaluation progress, costs and timestamps. Proposal records distinguish configuration from recipe, hold the complete payload/diff/reason, and record every handler approval or rejection, rejection reason, handler and time. Multiple rejected/replacement proposals can therefore be audited without creating versions.
- **Settings and audit:** the current approval limit and improvement settings live in the database, with one audit row per changed setting containing old/new value, handler and timestamp. Seed the approval limit from `data/company.json`; seed objectives, noise, tolerance, round spending limit and evaluation concurrency from `data/improvement.json`.
- **Allowed models:** seed the read-only catalog from `data/models.json`, including numeric dollar prices per million input/output tokens. It is returned through backend reads but has no dashboard editor or improver mutation.
- **Improver usage:** round id, model/reasoning, prompt/completion/total tokens, OpenRouter `usage.cost` and timestamp. Evaluation model usage remains on its run; judge usage remains on its evaluation/grade records.

Use these lab values initially:

| Setting | Initial value |
|---|---|
| Objectives | held-out `accuracy >= 0.60`, `grade >= 1.00` |
| Noise | accuracy `0.10`; grade `0.15`; cost/claim `0.15`; time/claim `0.15` |
| Tolerance | accuracy `0.10`; grade `0.15`; cost/claim `1.00`; time/claim `0.25` |
| Round spending limit | `$0.50` |
| Improver evaluation concurrency | `25` |

| Allowed model | Reasoning efforts | Input $/million | Output $/million |
|---|---|---:|---:|
| `openai/gpt-6-luna` | `none`, `low`, `medium` | 0.10 | 0.50 |
| `google/gemini-3.5-flash-lite` | `minimal`, `low` | 0.30 | 2.50 |
| `deepseek/deepseek-v4.1-flash` | `low` | 0.30 | 1.20 |

With `MODEL_NAME` set, effective version 1 and the improver instead use that model at `default`, and the allowed catalog contains only that model at `default` with zero input/output prices. This preserves the brief's free-model exception without displaying a configuration that differs from the model actually used. The judge is disabled in this mode and rounds are built/tested but not run live.

Every new dashboard operation has typed inputs/outputs, a unique `operation_id`, a one-line docstring and no optional request-body inputs. Use explicit nullable fields where a value is genuinely absent. Provide operations for current/versioned configuration and diffs, settings read/update, round list/detail/start/stop, and proposal approval/rejection. Service-only operations create versions/evaluations, record round state/proposals/decisions/usage and return the improver view; public proposal routes authenticate and forward to the service rather than recording an approval themselves.

The claim agent gets a dedicated version operation containing only instructions, model and reasoning effort—no scores, labels or round history. Build each claim model from that version; `default` omits the reasoning argument while a real level such as `none` or `low` is passed explicitly. Named work without a run uses the current version without changing its existing queue semantics.

Generate configuration and recipe diffs in the backend with Python `difflib` in the lab's style. Return typed added/removed/context lines for a shared dashboard diff component; compare each version with its immediate predecessor and show a clear empty state for version 1.

### 3. Version scores and the keep/rollback rule

A version's evaluation-set and held-out scores are the arithmetic means of its **completed, whole-set evaluations owned by rounds only**. Tiny runs, stopped runs and Scoreboard-started runs never contribute. Each run contributes its own measures; a run with no graded claims has `grade=null`, and grade averages use only actual grades rather than converting missing grades to zero. A version with no grade anywhere in the required candidate runs cannot be kept.

Before the improver reads results, its round ensures that the current version has one qualifying evaluation-set run and two qualifying held-out runs, reusing existing qualifying runs and executing only the missing ones. Its baseline progress therefore reaches 3/3, whether those runs are reused or new. After a configuration approval, `run_evaluation` executes one whole evaluation-set run and two whole held-out runs for the candidate at the round's snapshotted concurrency.

The backend alone decides the verdict from the candidate's two held-out runs versus the current version's held-out averages:

- For accuracy and grade, improvement means `new > old + noise`; regression means `new < old - tolerance`.
- For model cost per claim and active seconds per claim, improvement means `new < old * (1 - noise)`; regression means `new > old * (1 + tolerance)`.
- The candidate must meet both floors, have at least one improvement and have no regression. Comparisons are strict, as in the lab.
- Missing grade, an unaffordable required evaluation or any stopped candidate evaluation means rollback regardless of other measures. A kept candidate atomically becomes current; a rolled-back candidate remains visible as an immutable version with its scores and verdict, while current stays unchanged.
- Record before/after scores and a precise reason listing missed floors, regressions or improvements. A round stopped before this verdict never changes current; a stop after the verdict never reverses it.

The rule uses `accuracy` (decision plus payout within $1), grade, claim-model cost per claim and active time per claim. Judge cost is accounted separately and is not silently added to the claim-model measure. Keep the lab's initial time tolerance even though database waiting affects it; live evidence will report that behavior rather than tuning the setting secretly.

### 4. Backend judge

Add `backend/judge.py` as the only module containing the alpha Decisions call. Use `typesafe/jev-1.13`, the `openrouter` package and the lab's three questions/rubric. The state contains handler decision/reason/reference and agent decision/reason/policy section; adapt the lab's `rationale` references to those explicit product fields without adding another judge question.

- Read one documented judge origin from the environment (default `https://openrouter.ai`) and pass it as `server_url` on every call because the client-level address is ignored. Set each call's timeout to 10 seconds and disable SDK retries. Tests point this origin at their stand-in.
- During evaluation finish/stop finalization, grade all selected copies concurrently, but hold no database transaction open across network calls. Persist results after each call or after the parallel batch without blocking other claims on one slow judge response. Overall finalization must remain below 20 seconds.
- The numeric grade is Jev's `score`; the letter is the F/C/B/A criterion with the greatest probability, independently of the numeric score. Store same-grounds and same-section `noul` values as probabilities. The UI displays Yes at probability at least 0.5 and No otherwise, plus Not graded when no result exists.
- An agent copy with no recorded decision receives F/0 and zero-valued checks without calling Jev. A failed/timed-out Jev call is Not graded, contributes no grade to the average and retains any reported cost only when a response actually supplied one. Other claims still finish.
- Add average grade and separate judge cost to evaluation list/detail responses. Keep all existing deterministic measures and per-claim cost/time. Handler reason/reference may appear in the existing dashboard evaluation comparison, as today’s dashboard may show both sets, but never enter the claim-agent API or the improver's held-out view.
- When `MODEL_NAME` is set, do not construct/call Jev; the free-mode exception leaves every claim Not graded, including an undecided one, and the run has no average grade. The deterministic F/0 rule above applies when judging is enabled.

### 5. Improver, durable approvals and one-run orchestration

Add `agent/improver.py` as the sixth agent part and `agent/improver_api.py` as its separate backend boundary. The improver is a LangChain `create_agent` using Luna/medium (or the free override), the lab's timeout/retry style, `parallel_tool_calls=False`, a per-round model-call cap of 30 and actual OpenRouter usage recording. Its goal-style wrapper explains the real claim agent's instructions, policy wording and claims manual, claim/policy/history inputs, document and payout tools, and forbids naming a claim in a configuration change. The recipe follows as data and may contain numbered steps.

Its only tools are the lab's four, closed over the current round rather than accepting arbitrary round ids:

- `read_results()` returns current configuration, round-eligible scores on both sets, wrong evaluation claims and the five lowest-graded correct evaluation claims from the current version's latest qualifying evaluation-set run, handler/agent decisions/payouts/reasons/references/sections, judge grade/letter/two probabilities, earlier rounds, snapshotted objectives/noise/tolerance/rule, remaining budget and allowed models. Wrong means the existing `right=false` decision/payout comparison. From held-out data it returns aggregate scores only: no claim ids, copies, labels, reasons, references, per-claim grades or run detail.
- `propose_change(instructions, model, reasoning_effort, reason)` proposes a complete next configuration. Validate model and effort against the round's snapshot before exposing a pending proposal to a handler. An invalid proposal is immediately resumed as a rejection with the allowed-model explanation and no handler decision/version is recorded.
- `run_evaluation()` runs the approved candidate once on evaluation and twice on held-out through the existing dispatcher, then obtains the backend verdict and returns its scores/reason. Each dispatcher invocation runs in a fresh `contextvars.Context`; it uses the service's internal execution path while the round already owns the run slot.
- `propose_recipe(recipe, reason)` pauses for the same approval/rejection flow and creates a recipe version only after approval. The current round continues to use its original recipe version.

Attach `HumanInTheLoopMiddleware` only to the two proposal tools with approve/reject decisions. Use a separate synchronous `PostgresSaver` instance on Supabase and `durability="sync"`, with isolated synchronous SQLite savers in tests. Every round gets a fresh UUID-based thread id. All stretches of one round share a Langfuse session named for that round; its evaluation claims carry both the existing evaluation-run tag and a round tag.

The backend is the dashboard's source of round state. Its stages cover baseline evaluations (1/3, 2/3, 3/3), improver working, configuration approval waiting, candidate evaluations (1/3, 2/3, 3/3), verdict, recipe approval waiting, finished and stopped. Once a verdict exists it remains visible while later work continues.

For a handler decision, the public backend authenticates/authorizes and forwards the handler to the agent service. The service first acquires the one-run slot, then records the proposal decision/handler/reason through the internal backend operation, then resumes the checkpoint with the lab's `Command` shape. A busy refusal names the active work and records nothing. While a proposal waits, the slot is free for a dispatch or evaluation, but the round remains the sole open round.

Stopping marks the round stopped in the backend first; subsequent round writes, claims and model calls are refused. The active service stretch checks this state before beginning new work and releases the slot when in-flight work ends. At service startup, stop rounds interrupted while working/evaluating, preserving the current-version rule above, but leave checkpointed configuration/recipe approval waits open for a later resume. An improver response with no proposal finishes the round with no candidate version; a final response without a recipe proposal finishes normally after any existing verdict.

### 6. Round spending and evaluation control

Round spend is the sum of its improver usage, all baseline and candidate claim-model usage, and all associated judge cost. The dashboard shows spend/limit from those records, not an estimate. A narrow model-call guard around the improver checks the remaining budget before every model call and records reported usage immediately afterward; once no budget remains it starts no further improver call, finishes the stretch with the spending reason and rolls back any candidate whose required evaluations cannot complete.

Before creating each round evaluation, the backend estimates:

- `claim count * highest historical model cost per claim for that model`.
- With no runs for the proposed model, use the current model's historical high scaled by `max(new input price/current input price, new output price/current output price)`.
- On a fresh seed where not even the current model has history, the estimate is zero because the prescribed formula has no base cost. This bootstraps the first baseline without inventing token usage; actual ledger costs govern every subsequent estimate and the hard stop.

Refuse an evaluation whose estimate exceeds the remaining round limit before creating its run. For a candidate, stop its remaining evaluation sequence and record a rollback with the affordability reason. If a baseline cannot be afforded, finish the round without invoking an improver that lacks the required results and record no candidate.

At every round-claim begin boundary, atomically recheck that the round is open and has budget left. Once actual spend reaches the limit, do not begin another claim or claim-model call; already-running claims may finish and are still recorded. Budget-skipped copies do not increment failure attempts. Finish the affected evaluation as `stopped`, grade any already-decided copies within the remaining budget, and roll back its candidate. Judge calls that cannot be made leave their claims Not graded rather than fabricating scores. The OpenRouter key's separately configured credit limit remains the external backstop.

### 7. Dashboard and API presentation

Keep React-state navigation and strict TypeScript. Extend `dashboard/src/api.ts` with typed auth-aware actions and models for versions, settings, rounds, proposals, diffs and judge fields. Split growing page code into focused components so existing lint limits and 80% coverage remain intact.

- **Sign-in:** clear email/password form, no sign-up, actionable loading/error state, session restoration and sign-out.
- **Configuration:** a page header explaining versioned instructions/model/reasoning; current-version summary; every version with audit metadata and round-only held-out scores; model/reasoning before/after; and readable diffs. It replaces Instructions and its navigation item.
- **Scoreboard:** retain all controls/measures and add configuration version, average grade and separate judge cost. Detail rows add letter, numeric 0–3 grade and Yes/No/Not graded checks without removing existing comparisons or copy links.
- **Improvement:** senior-only start/stop controls with permission explanations for others; live stage/progress and spend; pending configuration/model/reasoning/instructions or recipe diff with reason; approval and reasoned rejection; newest-first rounds with candidate, recipe, before/after scores, verdict/reason, decisions and cost; recipe versions; and an SVG chart with no new library. Use two clearly labelled scales/panels for accuracy and grade, a fixed chart size and an accessible data table/summary. Empty rounds and a no-proposal round remain explicit.
- **Settings:** readable forms for approval limit, objectives, four noise values, four tolerances, spending limit and evaluation concurrency; a read-only allowed-model/effort/price table; audit feedback; and light/dark/system appearance. Only a handler with `can_approve_over_limit=true` can submit platform changes. Appearance alone is stored in browser local storage and applied through a root theme attribute plus `prefers-color-scheme` for system mode.
- **Shared enterprise standard:** reusable page header, buttons, status badge, numeric table cells, loading/empty/error panel and confirmation dialog. Numbers are right-aligned with units; statuses always have color and a word; diff lines have symbols/text as well as accessible addition/removal colors. Confirm configuration/recipe decisions, settings saves and round start/stop in the dashboard's own keyboard-accessible dialog, never browser `confirm`.
- Preserve existing queue, claim, sign-off and Scoreboard behavior while aligning headers, active navigation, spacing, tables and buttons. Keep previous data during polling, reserve space for status/progress, and avoid clearing whole panels on refresh so content and focus do not jump. All states follow `docs/BRAND.md` in both themes at laptop size.

### 8. Offline verification affordances

Extend Playwright with two local stand-ins and explicit environment values:

- An Auth server using Node cryptography and its own asymmetric key to sign password-session JWTs with audience `authenticated` and handler email, serve JWKS, support the Supabase session endpoints used by the client and answer browser CORS preflights. It needs no hosted Supabase secret.
- A Decisions server returning deterministic Jev-shaped scores, criterion probabilities, `noul` probabilities and usage cost, including a response whose most likely letter deliberately differs from the rounded score.

Unit tests use generated keys/fake JWKS or an auth verifier fake, a stub claim/improver model, a stub judge and isolated SQLite business/checkpoint state. Python improver tests exercise real graph/HITL transitions with scripted tool calls; browser tests use the port-8003 agent stand-in and service-side API-played records, never a model. To approve a proposal end to end, create its round/pending proposal through internal APIs, approve in the browser, verify the authenticated backend-to-service request, then play the service's state/version write through the same internal API and verify the dashboard result. Migrate all existing fixtures/specs to sign in and include version/judge fields rather than weakening assertions.

## Phase 1: Handler sign-in and enforced permissions

Build Supabase password sign-in, backend JWT verification and the signed-in app shell first. Replace sign-off's handler picker with the verified identity and establish the reusable Auth stand-in so every later page is built and tested signed in.

### Success criteria

- [x] `data/handlers.json` and seeded records contain all three emails, Dana `can_approve_over_limit=true`, Marcus and Priya `false`; the dashboard header identifies the signed-in handler and sign-out returns to the sign-in page. There is no sign-up control.
- [x] Backend tests verify a locally signed token through JWKS with audience `authenticated` and resolve its email. Table-driven cases reject missing/malformed/expired/wrong-audience tokens and an email absent from handlers without calling the agent service or changing state.
- [x] Existing handler mutations—named claim, dispatch, evaluation and sign-off—require a valid token. Dana can sign off a held payout; Marcus and Priya receive 403 and the claim remains unsigned/unpaid. The request contains no browser-selected `handler_id`, and a successful sign-off still resumes the same thread exactly once.
- [x] The claim graph's service-facing decision, payment, document and usage operations retain their existing unauthenticated service boundary; no Supabase secret is introduced.
- [x] Vitest covers sign-in success/failure, restored sessions, every-page gating, identity/sign-out, bearer headers, 401/403 presentation and removal of the handler picker. Existing queue/claim/Scoreboard tests are migrated to an authenticated fixture without losing coverage.
- [x] Playwright's Auth stand-in serves JWKS and CORS correctly, every existing spec signs in, and a browser sign-off records Dana and one stand-in resume request with no direct payment request. Tests make no hosted Auth/model/judge call.
- [x] `npm run check:python`, `npm run check:ts` and `npm run e2e` pass with strict typing, no `any`, both coverage thresholds at least 80%, and root `.env` absent.

## Phase 2: Configuration versions, run pinning and Settings

Add backend-owned settings/audit, configuration and recipe version 1, run-version associations and diffs. Build claim agents from the pinned version, then deliver Configuration and the complete Settings page, including browser-only appearance.

### Success criteria

- [ ] One isolated seed creates configuration/recipe version 1 with the current instruction file, Luna/low and the lab recipe; reseeding restores both to version 1, clears all later versions/rounds/proposals/audits and preserves the existing 25 operational claims and isolated evaluation sources/labels.
- [ ] Seed tests find every initial setting and numeric model price from the tables above. Settings updates require a handler with `can_approve_over_limit=true` (initially Dana), validate complete typed inputs, record old/new value, handler and timestamp, and are refused for Marcus/Priya with no write. Allowed models and evaluation sets have no settings mutation.
- [ ] A dispatch and Scoreboard evaluation record the current version and actual model/reasoning; changing current later does not alter an existing run. A round-internal creation operation can instead pin a specified candidate/version and round. Claim construction tests prove instructions/model/effort come from that version and its fetch response contains no scores or decisions.
- [ ] `MODEL_NAME=openrouter/free` produces effective version 1 and improver settings at `openrouter/free`/`default`, a one-entry zero-price catalog and no reasoning argument. Normal Luna `none` remains distinguishable from `default`; old null/`none` fixtures for an unspecified effort are migrated.
- [ ] A settings test records a decision at exactly the old limit and one above it, changes the limit, then proves the new limit applies only to decisions recorded afterward. Existing held decisions/payment guards are not retroactively rewritten, and all product payout/sign-off paths read the database setting.
- [ ] Backend diff fixtures exactly match independently calculated `difflib` additions/removals for instructions and recipe text, including version 1's no-predecessor state. Configuration responses include current pointer, immutable versions, audit metadata and empty round-only score averages until qualifying runs exist.
- [ ] Configuration replaces Instructions in navigation and shows the current version, all versions, model/reasoning, metadata, held-out scores and readable diff with no editing control. Settings renders every editable value, the read-only model/effort/price catalog and Dana-versus-Marcus explanations; confirmation is required before a save.
- [ ] Light, dark and system selections apply the correct brand tokens, survive reload in the same browser and do not write platform settings. A Playwright test signs in, changes one setting as Dana and switches/reloads the theme; a Marcus test cannot change the setting.
- [ ] `npm run check` passes with `.env` moved away, no hosted calls and Python/TypeScript coverage at least 80%.

## Phase 3: Parallel Jev grading and Scoreboard measures

Implement the single judge module and persist per-copy grades during run finalization. Add the local Decisions stand-in and expose grade/checks/cost on Scoreboard without changing deterministic decision/payout scoring.

### Success criteria

- [ ] Judge construction/call tests inspect the exact model, three lab questions, handler/agent state, per-call `server_url`, 10-second timeout and disabled retries. Only `backend/judge.py` contains the alpha Decisions invocation.
- [ ] A response with numeric score `1.51` but greatest probability on C returns grade `1.51` and letter C, not B. Same-grounds/section values remain probabilities in the API and render Yes/No at the specified threshold.
- [ ] A completed evaluation fixture with hand-calculated grades returns their mean, separate judge cost and every claim's letter/score/checks beside all previous measures. An ungraded failed-judge claim is excluded from the mean; an all-ungraded run has `grade=null`, never zero.
- [ ] An undecided copy receives F/0 without a judge request, while other copies are still graded. A mixed timeout/failure fixture leaves only affected claims Not graded and completes the evaluation.
- [ ] A controlled parallel test with stubbed slow calls proves per-copy concurrency, no SDK retry and finish below 20 seconds, comfortably inside the existing 30-second caller timeout. Dispatch finalization performs no judge work.
- [ ] With `MODEL_NAME` set, no judge client/call is made, every claim—including an undecided one—shows Not graded and average grade is null. No test contacts OpenRouter.
- [ ] Scoreboard list/detail shows version, average grade and judge cost apart from claim-model cost; each detail row shows grade/letter and both checks with understandable Not graded states. Vitest and Playwright use the deterministic judge stand-in and retain existing copy isolation/links.
- [ ] Seed/API tests prove both sets' stored labels retain reason/reference for backend judging and dashboard comparisons, while claim reads/tools and claim-agent responses contain neither them nor grade data.
- [ ] `npm run check` passes with `.env` absent and both coverage thresholds at least 80%.

## Phase 4: Durable improver rounds and the Improvement page

Build the backend round state machine and verdict/budget logic, the separate improver agent/client, and then the live Improvement page. Keep every automated transition on stub models, isolated SQLite checkpoints and fake time/cost records.

### Success criteria

- [ ] Construction tests prove a sixth `agent/improver.py`, Luna/medium (or free/default), 30-call round cap, `parallel_tool_calls=False`, goal-style wrapper plus recipe data, only the four named tools, HITL on the two propose tools, synchronous durable saving and a fresh thread id per round. Claim graph/tools and the six-part agent boundary otherwise remain unchanged.
- [ ] `read_results` matches a hand-built fixture: current configuration, round-only averages on both sets, all wrong evaluation claims plus five lowest-graded correct ones with handler/agent/judge details, earlier rounds, snapshotted settings/rule, remaining budget and numeric model catalog. Recursive negative assertions find no held-out claim id, label, reason, reference, per-claim result or run detail. `agent/main.py` still exposes no score/evaluation-start/handler-decision client function.
- [ ] A version-score fixture mixes round whole runs, round tiny/stopped runs and Scoreboard whole runs; only completed whole round runs contribute. Baseline setup reuses qualifying runs and executes only the missing evaluation/two held-out runs, with all resulting spend assigned to the new round and progress reaching 3/3.
- [ ] A pending configuration proposal creates no version and changes nothing. Approval records Dana/time before resume and creates the next immutable version; rejection records Dana's reason and resumes the same checkpoint so the scripted improver receives it and may propose again. Recipe proposals behave identically and use the shared backend diff shape.
- [ ] An invalid model/effort proposal is automatically returned to the improver with the allowed choices without a handler-visible pending proposal, version or handler audit. One response cannot produce two proposals because parallel tool calls are disabled.
- [ ] After approval, `run_evaluation` invokes the existing dispatcher in fresh contexts for one evaluation-set and two held-out whole runs at the round's snapshotted concurrency. Each run pins the candidate and records round/evaluation tags; the backend, not the model, returns the verdict.
- [ ] Hand-worked verdict cases cover one kept version, objective miss, each of four improvements/regressions, exact noise/tolerance boundaries, no improvement, no grade, stopped evaluations and unaffordable evaluations. Accuracy/grade use differences and cost/time use fractions; only a kept version becomes current, and before/after/reason are exact.
- [ ] Spending tests cover improver calls, baseline/candidate model calls and judge cost in one total; estimate history and new-model price scaling; the fresh-seed zero-history bootstrap; refusal before an over-budget run is created; and a running evaluation cut short with no later claim starts, no extra failure attempt, `stopped` status and candidate rollback.
- [ ] Only one round is open. Table-driven API tests refuse round start/stop and proposal approval/rejection without a valid token and refuse all of them for Marcus/Priya without a state change. A dispatch/evaluation/improver stretch excludes competing starts and approvals/rejections with a message naming the active work; a waiting round releases the slot so dispatch/evaluation can run. A refused approval while a dispatch runs records no proposal decision and does not resume a checkpoint. Internal round evaluations are not refused by their own slot.
- [ ] Stop/restart tests cover before verdict (current unchanged), after a kept verdict (current retained), waiting configuration and recipe proposals surviving reconstruction, no new model/claim/backend writes after stop, and slot release when active work ends. A no-proposal reply finishes with `version=null`; settings/catalog changes during a round do not alter its snapshot but apply to the next round.
- [ ] Round records contain recipe/settings/candidate/reason/before/after/verdict/all proposal decisions/cost. Langfuse fakes prove one session per round across every improver stretch and both round and evaluation tags on its claims; usage comes only from reported `usage.cost`.
- [ ] The Improvement page shows every live stage/count, pending diffs/model/reasoning/reason, approve/reject controls with confirmation and rejection reason, stopped/finished/no-proposal states, spend/limit, newest-first round audit, recipe versions and the accessible SVG accuracy/grade chart. Marcus/Priya see specific start/stop/approval restrictions, and backend refusal messages remain visible without losing current data.
- [ ] Playwright signs in, API-plays a pending proposal, approves it through the authenticated dashboard/service boundary, plays the resulting state/version write and verifies Configuration/Improvement. Additional Vitest/E2E coverage exercises rejection, permission denial, empty history, chart data and stopped rounds without any model or hosted service.
- [ ] `npm run check` passes all Python/TypeScript/E2E checks with `.env` absent, no weakened rules and both coverage thresholds at least 80%.

## Phase 5: Enterprise dashboard consistency, documentation and offline gate

Apply the shared visual/interaction standard across every new and existing page without changing existing workflows. Finish all operating documents, review data/permission boundaries and prove the complete product offline before spending on live runs.

### Success criteria

- [ ] Every page has the same descriptive page-header pattern, active navigation, spacing, aligned tables, right-aligned numeric cells with units, consistent buttons and word-plus-color statuses. Shared dialog/diff/loading/empty/error components provide keyboard access, visible focus, actionable next steps and non-color cues.
- [ ] Queue, claim workspace, sign-off, dispatch summary, Scoreboard and evaluation copies retain their existing behavior and API effects; only sign-in, signed-in identity, version/judge display and visual consistency differ as required.
- [ ] Polling tests and Playwright MCP checks show stable dimensions and preserved content/focus during refreshes, with no full-panel flicker, layout jump, console error or horizontal overflow at a 1440×900 laptop viewport. Light/dark screenshots cover long diffs, proposal dialogs, grades, chart, settings and permission errors as well as existing pages. The `npm run dev` scripts and all local test stand-ins remain portable `uv run`, Vite or Node entry points that work on Windows as well as macOS.
- [ ] `docs/SPEC.md` describes sign-in, database approval limit, versions/judge/improver/settings and team visibility. Its access rule explicitly permits handler reasons to the improver only for the evaluation set, scores only from held-out, no answer keys to the claim agent, and both sets in the dashboard; no-sign-in is removed while no real payments/messages remains.
- [ ] `docs/TECH_STACK.md` documents the six-part agent, separate claim/improver clients, backend versions/rounds/verdict/budget, Jev, Supabase JWT validation, test stand-ins, tracing and version-pinned runs. `docs/PROCESS.md` covers Auth setup, signed-in operation, rounds, free-mode restriction, reseeding and offline checks on Mac/Windows.
- [ ] `docs/CODE_STANDARDS.md` updates rules 7 and 13 for the sixth part, separate improver/dashboard Supabase clients and evaluation/held-out boundaries, and adds/clarifies rule 14 for goal-style instructions around data recipes. `data/README.md` describes handler permissions/emails, label reason/reference, `improvement.json`, `models.json` and the approval-limit source. `docs/STATUS.md` matches the implemented setup, pages, commands and verified behavior.
- [ ] A final negative review proves no improver tool can mutate settings/models/evaluation sets/judge, no proposal applies before approval, no held-out detail reaches the improver, no handler answer reaches the claim agent, no automated test reads root `.env`, and no new chart/router/agent framework or real payment integration was added.
- [ ] With development services stopped and `.env` temporarily moved out of the way and safely restored, the final offline `npm run check` passes Ruff, Pyright, pytest, Prettier, ESLint, tsc, Vitest and Playwright with at least 80% Python/TypeScript coverage.

## Phase 6: One-seed live rounds, permissions and design review

Confirm the lab's three Supabase Auth users, disabled sign-up, asymmetric signing keys and OpenRouter credit limit before starting; do not create new hosted resources. Stop services, preserve `.env` without printing secrets, temporarily remove `MODEL_NAME` to exercise default Luna, run `npm run seed` exactly once, and start `npm run dev`. Do not reseed between any checks below or add extra real-model evaluations.

### Success criteria

- [ ] After the single seed, Configuration and recipe history contain only version 1, rounds/evaluations are empty, and 25 operational claims are waiting. Dana can sign in with `HANDLER_PASSWORD`; the default configuration is Luna/low and the OpenRouter key has its credit limit.
- [ ] Through Playwright MCP as Dana, run round 1 from Improvement: reject its first valid configuration proposal with a useful reason, approve the replacement, and approve the resulting recipe proposal. Run round 2 and approve its proposals as presented. If the real improver legitimately proposes nothing, record that behavior rather than manufacturing a proposal or adding another round.
- [ ] During both rounds the page visibly follows baseline/work/waiting/candidate/verdict/recipe/finished states and correct evaluation counts. Afterwards each round shows its recipe/settings/candidate, rejection/approval audit, held-out before/after, verdict/reason and total cost; Configuration shows every immutable version, score average and diff.
- [ ] Query Langfuse's API and show each round's improver traces together in one round-named session and its evaluation claim traces tagged with both round and evaluation run. Report the two version-1 held-out runs' absolute accuracy/grade differences and relative cost/time differences beside the configured noise/tolerance, including the observed database-related time behavior.
- [ ] Start exactly one dispatch at N=25 on the then-current version. Its run record shows that version and finishes without duplicate decisions/payments. For a payout left waiting for sign-off, Marcus's page explains why he cannot sign off and the backend refuses an attempted action; he also cannot change settings. Dana can sign off that same claim, which resumes its existing thread and becomes paid once.
- [ ] Capture every page in light and dark mode, including sign-in, queue, claim/held payout, Scoreboard/list/detail, Configuration, Improvement/proposal/round history and Settings. Give the complete screenshot set once to a fresh `designer` subagent for a senior-product-designer review against `docs/BRAND.md` and brief point 8; fix its findings and recheck affected views without another real-model round or reseed.
- [ ] Sum the two rounds' improver, claim-model and judge ledgers plus the dispatch's model/resume usage and report the live spend in `docs/STATUS.md`; the expected Luna total is under `$1.00`. Report any limitation honestly rather than repeating paid work to improve a number.
- [ ] Restore the student's original `MODEL_NAME`, stop development services and leave the live records intact. Run the final `npm run check` with `.env` absent and restored safely, record the offline and live evidence in `docs/STATUS.md`, and tick only the criteria actually established above.

## Risks and controls

| Risk | Control |
|---|---|
| Held-out answers leak into improvement or claim work | Separate backend-only judge/labels, aggregate-only held-out view, separate improver client and recursive negative API/prompt/tool tests. |
| Approval changes state before a handler decides | HITL interrupts before proposal tools, immutable pending proposal records, version creation only on approved resume and no-op/rejection tests. |
| Restart or concurrent dispatch corrupts a round | Unique round threads, synchronous checkpoints, backend stage/verdict records, named one-run slot and stop/restart tests on both sides of the verdict. |
| Budget accounting misses work or starts unbounded claims | One round ledger across improver/evaluations/judge, pre-run estimates, atomic begin checks, stopped partial evaluations and OpenRouter credit-limit backstop. |
| Judge latency breaks run finishing | One alpha call site, 10-second timeout, no retries, parallel per-claim grading and controlled below-20-second finalization tests. |
| A lucky candidate is kept incorrectly | Whole round-only averages, hand-calculated strict thresholds, no-grade/stopped/unaffordable rollback and immutable before/after evidence. |
| New pages look inconsistent or jump during polling | Shared enterprise components, explicit loading/empty/error states, stable polling layout, two-theme laptop screenshots and one fresh designer review. |
| Tests accidentally spend money or use hosted Auth | Explicit stand-in addresses, isolated SQLite/stub models, root `.env` excluded from all checks and final no-env quality gate. |

## Build checkpoints

Implement the six phases in order with tests alongside each behavior. After each phase, run its focused and cumulative checks, tick only verified criteria here, update `docs/STATUS.md`, and commit with the pre-commit hook enabled. Never lower a rule/threshold, push, create unrequested hosted resources or run a real improvement round before Phase 6. Finish with the complete offline `npm run check` and the single-seed live evidence above.
