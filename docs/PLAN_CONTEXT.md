# Context layer, memory and Gateway implementation plan

## Outcome

Move the claim agent's model tools and graph-initiated payment onto one of two equivalent routes: the existing direct app route or AgentCore Gateway's signed MCP tools. Add the read-only context service, complete every decision record, add optional customer-scoped AgentCore Memory, and show the agent's lookups, memory activity, refusals and full decision on the claim page.

The five phases below correspond to the brief's five steps and must be completed in order. Automated tests use isolated SQLite, local stand-ins and stub models. They never call a model, AWS, Supabase Auth or a real Gateway. This build creates no AWS resources and does not run the deployment notebook. Keep all live model checks for the final section, after one reseed on the default model, using the direct route with memory off.

## Course alignment and scope

> SIDENOTE: For consistency with the course, please read `labs/week3day3.ipynb`, which deploys the app and the context layer, creates the Gateway and the memory, and runs the agent against them, without running it. Where the notebook and this document differ, follow this document. Include this sidenote in your plan too.

The notebook has been read in full and was not run. Follow its deployment contract locally: `context.server.handler` serves a stateless, JSON, streamable-HTTP MCP endpoint at `/mcp`; the app target is `undercover-app`, the context target is `undercover-context`, and Gateway tool names are `<target>___<operation>`. Gateway requests use SigV4 for `bedrock-agentcore`; the context Function URL uses IAM auth. Memory has Semantic facts under `/customers/{actorId}/facts/` and claim summaries under `/customers/{actorId}/claims/{sessionId}/`.

Where the notebook and `docs/week3day3_product_CONTEXT.md` differ, the product brief controls. In particular, this plan includes the complete decision record, direct-route development, offline refusal/payment tests, customer isolation, dashboard provenance and the final direct-route/live-memory-off checks. The notebook's AWS deployment and teardown are later lab work, not part of implementation.

Leave the seven data-governance files in `context/` unchanged. Do not open `data/evals/`; retain its current seed/scoring behavior. Do not build memory into evaluations, mask personal fields or add customer document requests.

## Current-build facts to preserve or adapt

- This branch keeps the graph and tools in `agent/main.py`, not the reference build's `harness.py`, `tools.py`, `api.py` and `connection.py`. Keep the hand-written synchronous graph in `agent/main.py`; add focused route/tool adapters rather than renaming the whole agent or adding another framework.
- `agent/main.py` currently binds `read_claim`, document search/read and `calculate_payout`, then uses a structured-output `decide` node followed by `record_decision`. To let a section 14 refusal return to the model for correction, make `record_decision` a thin model tool that updates graph state only after the app accepts it. Keep payment, sign-off waits and security referrals in graph code; the model gets no `pay_claim` tool.
- Current tools expose `claim_id` to the model. A claim-scoped runtime context will instead supply the claim id, policy id, customer id, run kind and configuration version. The model cannot supply another claim, policy, customer or decision maker.
- The current backend client hardcodes `BACKEND_URL`. Read `UNDERCOVER_API`, defaulting to `http://localhost:8000`, when creating the direct client so the CLI's late dotenv load and the notebook's later `UNDERCOVER_API=<Lambda URL>` both work. Read `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` and `UNDERCOVER_MEMORY_ID` when building each claim graph, never at module import.
- This branch stores decisions on `claims_table` and exposes `policy_section`. Align the public decision record with `context/data_dictionary.yaml`: `section`, `calculation`, `decided_by`, `decided_at` and `manual_version`, plus the existing sign-off fields. Update judge/evaluation reads without changing their scoring behavior.
- The app currently has `document_sections` and `document_reads`, not the reference build's document store. Seed a backend-owned documents table from the two context Markdown files, retaining full contents and front-matter versions for instructions and decisions. Context owns section search/read once the two app operations are removed.
- Claim instructions are in `backend/agent_instructions.md`. Update those actual instructions and append both documents from the backend documents table when configuration is supplied. This branch's `backend/improver_recipe.md` has no claim-tool-list sentence; make no unrelated improver changes.
- The dashboard uses `ActivityPanel.tsx`, `DocumentSections.tsx` and `DecisionSection.tsx`. Extend the existing chronological `activity` API into typed steps rather than adding a parallel activity feed. Replace Sections read with Context used.
- The E2E judge stand-in currently uses port 8006. Keep the real context service on the required port 8006 and move the test-only judge stand-in to a distinct local port if context is added to an E2E process setup; do not change the product port to accommodate a test.

## Design

### 1. One model surface, two route adapters

Use four small boundaries around the existing graph:

- `agent/api.py`: the direct, API-key backend client and all non-model bookkeeping. Starting work, steps, model usage, work periods, failures, runs, versions, the improver, the security read/referral and the Gateway-refusal hold always use this direct route.
- `agent/direct.py`: model operations on the current direct app API plus unsigned streamable-HTTP MCP calls to `UNDERCOVER_CONTEXT`, defaulting to `http://127.0.0.1:8006/mcp`.
- `agent/gateway.py`: the same operations through a `MultiServerMCPClient`, signed on every request with AWS CLI credentials/region for `bedrock-agentcore`. Map local names to `undercover-app___...` and `undercover-context___...`.
- `agent/tools.py`: thin claim-scoped tool definitions shared by both routes, plus conversion of results and refusals into the model's ToolMessages and structured artifacts for activity recording. A small shared MCP sync bridge may live separately; it is not another agent framework.

The model sees the same local tool names and schemas on both routes: `get_claim`, `get_policy`, `get_claim_history`, `calculate_payout`, `record_decision`, the seven context tools and, when eligible, `search_memory`. The four read/calculation app tools take no id; `record_decision` takes no claim id or `decided_by`; `search_memory` takes no customer or claim id. `pay_claim` remains a graph operation, not a model tool.

Use one canonical claim serializer for direct and Gateway `get_claim` content and for the security check's direct claim read. Preserve the check's reason-first schema, complete policy-plus-claim evidence and existing retry/usage behavior. Ordinary agent decisions carry `UnderCover Agent (configuration version N)`; security referrals carry `Security check` and bypass the Gateway.

Configure MCP calls with `handle_tool_errors=False`. App/context errors become refusals with the original reason, never successful-looking content. A Gateway `McpError` is separately identified as a Gateway refusal, recorded through the direct bookkeeping API and returned to the model as a refusal. Network and unexpected programming failures retain the existing failure policy rather than being disguised as policy refusals.

The MCP client is async while this graph and its Postgres checkpoints must stay synchronous and Windows-compatible. Run each short async MCP operation on its own worker thread/event loop, close only that loop and return the result or exception to the synchronous caller. Do not call `asyncio.run` on the main thread or remove its event loop. The Gateway is stateless, so a session per call is sufficient. Add a regression that uses the bridge and then invokes a Mangum handler in the same process.

### 2. Gateway-refused payment still has a human exit

A normal approval's `record_decision` call and the graph's `pay_claim` call use the selected route. If the Gateway refuses payment, catch that Gateway error only in the payment node, record a Gateway refusal through the direct API, and call a new agent-key-only bookkeeping operation that:

- accepts a required refusal reason;
- changes only an approved, unpaid claim in `in_progress` to `waiting_for_sign_off`;
- marks sign-off as required and persists `gateway_refused` with the reason; and
- stays outside the six-operation agent OpenAPI document and outside the Gateway.

Route that state into the existing approval interrupt. Dana's backend-owned sign-off still records sign-off/payment atomically, and resume finishes without another payment call. The sign-off panel uses the Gateway-refusal explanation rather than claiming the current app approval limit was exceeded.

### 3. Independent context and backend document storage

`context/` becomes an independent Python package with `__main__.py`, `server.py`, focused read-only data/document helpers and tests. It reads its seven packaged files once at process import using paths relative to its own package. FastMCP uses `stateless_http=True` and `json_response=True`; every tool has a one-line docstring, required inputs and no writes.

Return a consistent result envelope with the answer and provenance. Single-file answers name their file and its `system`, `owner` and `reviewed` values. A document search carries the file/source for every returned section; the agent normalizes one or more source entries into a lookup artifact that the dashboard can group by file. Unknown terms return the glossary's known-term list. Unknown sections and invalid inputs are refusals, not empty successes.

For local development, one uvicorn server serves `/mcp` on `127.0.0.1:8006`. For Lambda, build a fresh FastMCP server around the already-loaded data for each invocation and wrap its streamable HTTP app with Mangum using `lifespan="auto"`. Disable DNS-rebinding protection only in that Lambda adapter because IAM protects the Function URL; the Function URL host must work on two sequential invocations in one process. The context Lambda needs no environment variables and writes no files.

The backend independently seeds plan rules and complete documents from `context/` only in its seed path. It does not import the context service or read context files at module import or while serving requests. Add `pyyaml` to project and `server` dependencies. Add a `context` group containing only the context Lambda's direct needs (`mcp`, `mangum`, `pyyaml`), and add `langgraph-checkpoint-aws>=1.2.4` to project dependencies. Update the lockfile and Ruff, Pyright, pytest and coverage scopes.

### 4. Complete, attempt-scoped decisions

`calculate_payout` stores the latest exact calculation and calculated payout against the claim's current attempt. The existing `failed_attempts + 1` identifies a fresh attempt: transient resume retains it, while an ordinary failure/new thread advances it. A later calculation in the same attempt replaces the earlier one. Keep that working calculation separate from the immutable calculation copied onto an accepted decision.

Split the model's decision draft from the app's `DecisionRequest`; only the latter contains `decided_by`. Custom request validation returns 422 messages naming claims manual section 14 for blank/whitespace reason, section or maker; a non-positive approval; a negative payout; and a decline/referral with a payout. These checks run before claim lookup and therefore have the same answer on an unknown claim. After lookup, an approval also needs a calculation for the current attempt and a requested payout exactly equal to its calculated payout, with missing/stale/mismatched cases refused as 422 naming section 14. Identical replay retains the existing idempotent behavior.

An accepted approval stores the exact calculation lines and calculated payout, maker, UTC decision time and the claims manual version from the documents table. Declines/referrals store no calculation. Existing handler sign-off/payment and evaluation scoring remain intact. Use shared approval-recording helpers in backend tests, agent tests and `e2e/api.ts`; where an API-played approval is needed, calculate one `expense` line under `emergency_medical` for the desired payout plus the plan deductible and use the exact returned payout, preserving every existing expected amount.

A successful `record_decision` tool updates claim state and routes to memory save, payment, sign-off or the end. A 422 stays inside the ToolNode refusal loop so the model can correct and call it again; no failed draft becomes a decision.

### 5. Customer-scoped memory and typed claim steps

When memory is enabled and the run is operational, compile the graph with `AgentCoreMemoryStore(memory_id=..., region_name=...)` while retaining the existing SQLite/Postgres checkpointer. Evaluations build without the memory store/tool even if the environment has a memory id.

Add a deterministic save node immediately after an accepted non-security decision and before payment/sign-off branches. Its durable position ensures one save across retries, restart and sign-off resume. Write a HumanMessage containing only summary, claimed benefit, incident date/place and items, then an AIMessage containing decision, payout, section and reason, using `(customer_id, claim_id)` as actor/session. Never copy the story or documents. Skip security referrals and evaluation copies. AWS/connection failure logs a warning and leaves the recorded decision/workflow intact.

`search_memory(query)` searches `("customers", <claim's customer>)` with limit five, returns result text, and filters the current claim's summary namespace. It never accepts a namespace from the model. Search failure becomes a model refusal. Instructions explain that memory supplements earlier-claim context but backend claim history remains authoritative for section 9 repeat claims.

Extend activity records with a kind and typed details while retaining chronological messages for existing bookkeeping. Use distinct kinds for `tool`, `lookup`, `memory_search`, `memory_save` and `refusal`, with ordinary existing entries retaining a general activity kind. Lookup details include the operation/input and one provenance entry per affected file. Tool steps show the route-specific remote name; the model's local name is mapped to the Gateway name only on that route. Refusals identify app/context versus Gateway and are visually distinct. The Context used panel deduplicates lookups by operation/input within each file and shows each file's source once. The decision panel shows the complete record and every calculation line.

## Phase 1: Tools from the Gateway

Implement the claim-scoped tool surface, direct/Gateway adapters, request-time environment reads, synchronous MCP bridge, model-tool decision flow, Gateway refusal handling and the payment hold/sign-off path. Phase 1 covers the app operations; Phase 2 adds the context operations through the same adapters.

### Success criteria

- [x] Tool-schema tests prove both routes expose identical local names and required inputs. `get_claim`, `get_policy`, `get_claim_history` and `calculate_payout` accept no id; `record_decision` accepts no id or `decided_by`; the model has no payment tool. A second claim's ids cannot be supplied through any model input.
- [x] A local streamable-HTTP stand-in names its app tools `undercover-app___...`. Tests prove model reads/calculation/decision and graph payment use it when `UNDERCOVER_GATEWAY_URL` is set, while start/steps/usage/work/failures, runs, versions, improver calls and the security read/referral still use the direct key client.
- [x] Signing tests with a fake AWS session prove every Gateway request uses SigV4 for `bedrock-agentcore`, the CLI session's frozen credentials and region, and the expected prefixed tool name. No real AWS call occurs.
- [x] Direct-route tests use the current app key API and the same local tool surface. `UNDERCOVER_API`, `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT` and `UNDERCOVER_MEMORY_ID` are read only at graph/client construction after a simulated late dotenv load; no setting is frozen at import.
- [x] Direct and stand-in-Gateway `get_claim` results are byte-for-byte identical, and the security check reads that same canonical claim text directly. Existing passing/flagged screen, retry, usage and checkpoint tests still pass; a security referral names `Security check` as maker and never calls the Gateway.
- [x] An app refusal on each route reaches the model as a refusal containing the app's reason, not a successful result. A stand-in Gateway `McpError` is recorded on the claim as a Gateway refusal and also reaches the model as a refusal, after which a stub model can finish the claim.
- [x] A successful `record_decision` tool updates graph state and ends model work; a section 14-style tool refusal returns to the model and a corrected later call can succeed. Existing model-call limits and usage recording still apply, and no decision is recorded for a refused draft.
- [x] With the app approval limit raised above the Gateway's $1,000 policy, a stand-in Gateway payment refusal records the reason and moves only the approved, unpaid in-progress claim to waiting for sign-off. Invalid hold transitions and handler-credential calls to the hold operation are refused; the operation is absent from the agent API document.
- [x] Dana's sign-off of the Gateway-held approval pays exactly once and resume makes no second payment call. The bundle exposes the Gateway hold cause for Phase 5. Existing ordinary small-payment and over-app-limit paths are unchanged.
- [x] A main-thread regression invokes the synchronous MCP bridge and then a Mangum handler successfully in one process. The graph and checkpoint connections remain synchronous and no async Postgres driver is introduced.
- [x] Focused route/graph/backend tests and the cumulative `npm run check` pass with root `.env` moved aside and restored, all coverage thresholds intact and no model/hosted calls. Update `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md` and `docs/STATUS.md` for the two route modules and boundaries; tick only verified items and commit Phase 1 with the pre-commit hook enabled.

## Phase 2: The context layer

Implement the packaged read-only MCP service, all seven tools and provenance, local/Lambda adapters, dependency/import checks, development startup and context-backed app seeding. Remove the app's document search/read operations and extend both agent routes with the context tools.

### Success criteria

- [x] Tool-list tests show exactly `lookup_term`, `describe_fields`, `get_plan`, `get_claim_rules`, `search_reference_data`, `search_documents` and `read_section`, each read-only with a one-line description and no optional inputs.
- [x] Tests against the real, unchanged files cover case-insensitive known/unknown terms; all three record descriptions; every plan with deductible benefits and add-ons; all claim rules; and case-insensitive reference searches requiring every query word with topics. Every answer includes the correct filename, system, owner and review date.
- [x] `search_documents(query)` searches/ranks both prepared documents and names each result's file/source. `read_section(document, number)` returns the same tagged section content expected from the former app behavior. Tests cover a missing section, invalid document and front matter/version never leaking into a section.
- [x] A file-read instrument proves all seven files are loaded and both documents split once at process startup, not once per tool request. The seven source files and the three legacy files in `data/` are unchanged.
- [x] Two sequential Function URL events in one process, with a realistic Function URL host, first list tools and then call one through `context.server.handler`; both return successful JSON MCP responses. This proves the fresh-server/Mangum lifespan and transport-security handling without AWS.
- [x] `uv run -m context` serves `http://127.0.0.1:8006/mcp`; `npm run dev` starts context beside backend, agent and dashboard using cross-platform scripts. Direct-route agent tests call the context tools through unsigned MCP and receive the same local schemas/results as their prefixed stand-in-Gateway equivalents.
- [x] Seed tests prove plans/add-ons/deductible rules come from `context/plans.yaml`, complete documents and versions `2026.1`/`2026.2` come from the two context Markdown files, and `data/company.json` still supplies the configurable approval limit. The product no longer reads `data/plans.json` or either legacy document.
- [x] Effective claim instructions end with both complete documents loaded from the backend documents table, not from a request-time file read. They name the context definitions, plans, rules, reference data and document tools as goals.
- [x] The former app document routes are gone, and `/api/agent/openapi.json` contains exactly `get_claim`, `get_policy`, `get_claim_history`, `calculate_payout`, `record_decision` and `pay_claim`, with one-line descriptions, required inputs, no `servers` and no key input. Context refusal behavior remains a refusal on both agent routes.
- [x] `pyproject.toml` and `uv.lock` include the required project/server/context dependencies; Ruff, Pyright, pytest and coverage include `context/`. `npm run check:context` imports `context.server` in an isolated context-group environment without altering `.venv`, and the existing server-group import still passes without packaged `context/` code.
- [x] Focused context/seed/OpenAPI tests and the cumulative no-`.env` `npm run check` pass. Update `docs/SPEC.md`, `docs/TECH_STACK.md` (including context Lambda package, handler, IAM Function URL and empty environment), `docs/PROCESS.md`, `docs/CODE_STANDARDS.md`, `docs/STATUS.md` and `data/README.md`; tick verified items and commit Phase 2.

## Phase 3: A complete decision record

Implement attempt-scoped calculation storage, section 14 request/workflow validation, complete persisted decision records, maker injection and shared test helpers. Keep decision/payment idempotency, atomic sign-off and evaluation behavior.

### Success criteria

- [x] Tests cover blank and whitespace reason, section and `decided_by`; zero and negative approvals; nonzero declines/referrals; missing current-attempt calculation; stale-attempt calculation; and payout/calculation mismatch. Every case returns 422 naming section 14 and the missing/invalid part, with no accepted decision. Request-owned cases return identically on an unknown claim before lookup.
- [x] Two calculations in one attempt make the latest authoritative; resume on the same attempt retains it; a fresh ordinary retry cannot use the prior attempt's calculation. Declines and referrals need no calculation and persist none.
- [x] A complete approval response contains decision, exact payout, exact calculation lines/calculated payout, `section`, plain-language reason, `decided_by`, UTC `decided_at` and manual version `2026.2`. Existing sign-off adds handler/time and identical decision/payment replays return the unchanged record.
- [x] Normal agent records name the UnderCover Agent and pinned configuration version, while that field is absent from the model draft/tool schema. A flagged security referral records `Security check`, zero payout, no calculation and the manual version without using a configuration version or Gateway.
- [x] A stub model first makes an invalid record call, receives the app's section 14 refusal, then corrects it; the refusal and both attempts are visible in agent records while only the corrected decision is stored. This behavior works on direct and stand-in-Gateway routes.
- [x] Shared backend/agent/E2E approval helpers create a matching calculation and maker before direct API approvals without changing existing expected payouts or assertions. Decline/referral helpers send the new maker field without fabricating calculations.
- [x] Judge/evaluation code reads the new `section` field and all existing scoreboard comparisons, grades and copy behavior pass unchanged. The complete claim API and dashboard fixtures use the data-dictionary field names.
- [x] Focused decision/agent/evaluation tests and the cumulative no-`.env` `npm run check` pass with thresholds intact. Update the decision-record rules in `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/STATUS.md` and `data/README.md`; tick verified items and commit Phase 3.

## Phase 4: Memory of each customer

Add the optional AgentCore store, deterministic post-decision events, customer-scoped search tool, failure behavior and goal-style instruction updates. Preserve the existing checkpointer and keep all evaluations memory-independent.

### Success criteria

- [ ] Construction tests prove `UNDERCOVER_MEMORY_ID` is read at graph build and creates `AgentCoreMemoryStore(memory_id=<id>, region_name=<CLI region>)`. With no id, no store or memory tool is constructed; the existing SQLite/Postgres checkpoint selection and four-connection Postgres pool are unchanged.
- [ ] Stand-in-store tests after an ordinary approval, decline and referral verify exactly two events in actor/session order: customer HumanMessage with summary, benefit, incident date/place and items, then agent AIMessage with decision, payout, section and reason. Neither event contains the story or supporting documents.
- [ ] Replay/resume tests prove the durable save node writes once even when payment, Gateway holding, restart or sign-off continues afterward. Security-check referrals, claims with no recorded decision and evaluation copies save nothing.
- [ ] With memory enabled, operational graphs expose `search_memory(query)` and evaluation graphs do not. Search tests prove the only namespace is `("customers", <this customer>)`, limit five, text-only results and removal of the current claim's summary; another customer's facts/summaries cannot be returned.
- [ ] A stand-in search failure reaches the model as a refusal. A stand-in save failure logs a warning, retains the decision and allows the normal payment/sign-off outcome. No test calls AWS or waits for background memory extraction.
- [ ] `backend/agent_instructions.md` names the context resources and optional customer memory as goals, and states that backend claim history, not memory, decides section 9 repeat claims. Existing hidden-instruction handling and document-append behavior remain unchanged.
- [ ] `langgraph-checkpoint-aws>=1.2.4` is locked in project dependencies but absent from the isolated app/context server requirements. Focused memory/graph tests and the cumulative no-`.env` `npm run check` pass. Update `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md` and `docs/STATUS.md`; tick verified items and commit Phase 4.

## Phase 5: Every step on the claim page

Implement typed activity/step payloads through the backend, agent API and strict TypeScript client; route-specific tool names; lookup provenance; memory/refusal steps; Context used; the complete decision/calculation panel; and Gateway-specific sign-off copy. Finish all product documentation and the offline gate before live verification.

### Success criteria

- [ ] Backend and agent tests cover chronological `tool`, `lookup`, `memory_search`, `memory_save` and `refusal` steps with typed details. Lookup steps contain operation/input and file/system/owner/reviewed provenance; memory steps contain query/results or saved event summaries; refusal steps contain reason and source.
- [ ] Direct tool steps use local names such as `lookup_term`; Gateway steps use names such as `undercover-context___lookup_term`. Tests prove the model still sees local names and that app/context/Gateway refusals are recorded once, including a section 14 refusal followed by its corrected record attempt.
- [ ] Vitest covers every step kind, chronological updates while a claim is in progress, visually distinct accessible refusals, and empty/populated states. TypeScript remains strict with no `any` and API fixtures use the same discriminated step shapes as the backend.
- [ ] The Context used panel replaces Sections read, groups deduplicated lookups by file and shows each file's source system, owner and review date. A multi-file document search appears under each correct file without duplicating unrelated lookups; obsolete document-read UI/API remnants are removed.
- [ ] The decision panel shows decision, payout, section, reason, maker, decision time, manual version, sign-off/payment details and each calculation line with its amount. Declines/referrals show no calculation. Vitest and Playwright cover an approval with calculation and a Gateway-held claim whose panel says the Gateway refused payment rather than citing the app limit.
- [ ] Shared Playwright helpers record matching calculations and typed steps without a model, then verify Context used, complete decision details and refusal presentation in the real dashboard. No test contacts hosted Auth, AWS, a Gateway or a model.
- [ ] The page follows `docs/BRAND.md`: rounded panels, accessible light/dark colors, Raspberry emphasis for refusals, JetBrains Mono for ids/amounts, short plain labels and no horizontal overflow at desktop/mobile widths.
- [ ] `docs/SPEC.md`, `docs/TECH_STACK.md`, `docs/PROCESS.md`, `docs/CODE_STANDARDS.md`, `docs/STATUS.md` and `data/README.md` describe the context/memory/data ownership, six-operation API, complete record, settings, development/check commands and current dashboard. `UNDERCOVER_GATEWAY_URL`, `UNDERCOVER_CONTEXT`, `UNDERCOVER_MEMORY_ID`, `UNDERCOVER_API` and AWS CLI credential/region behavior are documented; legacy data files remain present but are no longer product inputs.
- [ ] `npm run build` succeeds with root `.env` absent. The final offline `npm run check`, including both isolated server/context imports, passes with `.env` moved aside and restored and with every coverage threshold intact. Record exact test/coverage results in `docs/STATUS.md`; tick only verified offline criteria.

## Final one-seed live verification and handover

Run these checks only after all five phases' offline criteria pass. Stop development services, restore the live `.env`, select the default Luna/low configuration rather than `MODEL_NAME`, and temporarily ensure `UNDERCOVER_GATEWAY_URL` and `UNDERCOVER_MEMORY_ID` are unset. Keep `UNDERCOVER_API` pointed at the laptop app and `UNDERCOVER_CONTEXT` at its default/local address. Restore the original environment afterward. Do not run the notebook, contact AWS or create any cloud resource.

### Success criteria

- [ ] Run `npm run seed` exactly once and confirm 26 waiting operational claims. Start `npm run dev`; backend, agent, dashboard and context all start, and an unsigned local MCP client can list/call the context tools at `http://127.0.0.1:8006/mcp`. Dana Whitfield can sign in as before.
- [ ] With the direct route and memory off, `uv run -m agent CLM-1001` decides and pays/records the claim as before. Its API record contains the exact calculation, maker, decision time and manual version, proving the Step 1 direct smoke and Step 3 complete-record smoke. No memory tool/step appears.
- [ ] Next run `uv run -m agent CLM-1005`, then `uv run -m agent CLM-1006`, in that order. Both finish with complete decisions and neither uses memory. Record each named claim's reported model tokens/cost from the backend ledger, using sequential no-run totals if necessary rather than estimates.
- [ ] Start one dashboard dispatch at N=25. It works the remaining queue without re-deciding or re-paying the three named claims, preserves the security screen/referral behavior and finishes with a record-built summary. Record its model cost, active time and whole-run time from the run record.
- [ ] Run one evaluation of the evaluation set's five tiny claims. It uses five fresh copies, has no memory tool/events and leaves the operational queue byte-for-byte unchanged. Record model and judge costs and all scoreboard measures; do not open `data/evals/`.
- [ ] Through the Playwright MCP server as Dana, inspect CLM-1005 and CLM-1006 at desktop and mobile widths. Both pages show chronological plain-named tool calls, context lookups with inputs and file provenance, a grouped/deduplicated Context used panel, and complete decisions with calculation lines. Memory steps and Gateway-only names/refusals remain covered by offline tests, not expected in this live pass.
- [ ] The same browser review finds no console/page errors, inaccessible controls or horizontal overflow. Context sources match the unchanged governance files, decision maker/manual version are visible, and any held sign-off copy matches the actual hold cause.
- [ ] Record live outcomes, exact offline totals/coverage and named/dispatch/evaluation spend separately in `docs/STATUS.md`, including any limitation rather than inferring success. Stop all services, restore the original `.env`, confirm the seed ran only once and no AWS resource was created or changed. Tick only observed criteria and make the Phase 5 handover commit with the pre-commit hook enabled; do not push.

## Risks and controls

| Risk | Control |
|---|---|
| MCP async calls remove the main-thread loop or break Windows checkpoints | Short worker-thread loops, synchronous graph/psycopg unchanged, and a bridge-plus-Mangum regression |
| An app error looks like a successful MCP result | `handle_tool_errors=False`, shared refusal conversion and direct/Gateway refusal tests |
| Gateway refusal strands an otherwise payable approval | Agent-key-only hold transition, existing atomic sign-off/payment and exactly-once resume test |
| Model accesses another claim/customer or fabricates the maker | Claim-scoped runtime injection, schema assertions and customer-only memory namespace tests |
| FastMCP fails on its second Lambda request or rejects the Function URL host | Load data once, build a fresh server per invocation, explicit Lambda transport setting and two-event test |
| A stale or invented payout passes section 14 | Current-attempt calculation storage, exact-payout check and missing/stale/mismatch 422 tests |
| Memory carries injected text, leaks across customers or changes evaluations | Never save story/documents/security referrals, customer-only namespace, skip evaluation store/tool and stand-in isolation tests |
| Provenance is lost between MCP results and the dashboard | Structured source artifacts on every context result, typed lookup steps and grouped/deduplicated panel tests |
| Seeding makes the deployed app depend on packaged context code | Context reads confined to seed path, backend documents table for requests, isolated app/context import checks |
| Live verification accidentally enables AWS/memory or spends without evidence | Direct/memory-off environment check, one seed, no notebook/AWS actions and backend-recorded cost reporting |

## Build checkpoints

Implement the five phases in order with tests beside each behavior. After each phase, run its focused tests and the cumulative no-`.env` gate, tick only criteria with evidence in this file, update `docs/STATUS.md`, and commit with the pre-commit hook enabled. Never lower a rule or threshold, disable a check, use `git commit --no-verify`, push, open `data/evals/`, run a model in an automated test, modify the seven context source files or create an AWS resource.

Keep every live check in the final section. Phase 5's handover commit follows that single-seed live pass and includes its observed evidence and spend. Stop after handover and wait for review rather than running the deployment notebook.
