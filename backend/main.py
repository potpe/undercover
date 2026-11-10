"""The UnderCover backend."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException

from backend.database import (
    add_activity,
    get_claim_bundle,
    initialize_database,
    list_claims,
    list_handlers,
    read_document_section,
    record_model_call,
    record_work_period,
    search_document,
)
from backend.models import (
    ActivityRequest,
    AgentRun,
    ClaimBundle,
    ClaimSummary,
    DecisionRequest,
    DocumentSearchRequest,
    DocumentSectionContent,
    DocumentSectionRequest,
    DocumentSectionSummary,
    FailureRequest,
    FinishRunRequest,
    Instructions,
    ModelCallRequest,
    PaymentRequest,
    PayoutRequest,
    PayoutResponse,
    QueueRunRequest,
    RunPlanRequest,
    RunRequest,
    RunResponse,
    SignOffRequest,
    WorkOutcome,
    WorkPeriodRequest,
    WorkRequest,
)
from backend.payout import calculate_claim_payout
from backend.runs import (
    create_run as create_agent_run,
)
from backend.runs import (
    current_run,
    fail_claim,
    finish_run,
    get_run,
    save_plan,
    work_claim,
)
from backend.workflow import record_decision, record_payment, sign_off


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


def agent_post(
    path: str, timeout: float, body: dict[str, Any] | None = None
) -> httpx.Response:
    base_url = os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002")
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        return client.post(path, json=body)


def notify_agent_run(run_id: int) -> None:
    with suppress(httpx.HTTPError):
        agent_post(f"/runs/{run_id}", 5).raise_for_status()


def notify_agent_resume(claim_id: str) -> None:
    with suppress(httpx.HTTPError):
        agent_post(f"/claims/{claim_id}/resume", 5).raise_for_status()


def run_claim_via_service(claim_id: str, thread_id: str) -> str:
    run_id = int(thread_id.split(":", 1)[0])
    response = agent_post(
        f"/claims/{claim_id}/work",
        120,
        {"thread_id": thread_id, "run_id": run_id},
    )
    response.raise_for_status()
    return response.json()["result"]


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def claims() -> list[ClaimSummary]:
    """List the claims queue."""
    return [ClaimSummary(**claim) for claim in list_claims()]


@app.get("/api/claims/{claim_id}", operation_id="get_claim")
def claim(claim_id: str) -> ClaimBundle:
    """Return a claim and its related records."""
    bundle = get_claim_bundle(claim_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return ClaimBundle(**bundle)


@app.post("/api/claims/{claim_id}/payout", operation_id="calculate_payout")
def payout(claim_id: str, request: PayoutRequest) -> PayoutResponse:
    """Calculate an approved claim's payout using UnderCover's rules."""
    return calculate_claim_payout(claim_id, request)


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def decision(claim_id: str, request: DecisionRequest) -> ClaimBundle:
    """Record the agent's decision for a claim."""
    record_decision(claim_id, request)
    return claim(claim_id)


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(
    claim_id: str, request: SignOffRequest, background_tasks: BackgroundTasks
) -> ClaimBundle:
    """Save handler sign-off and ask the agent to resume payment."""
    sign_off(claim_id, request)
    background_tasks.add_task(notify_agent_resume, claim_id)
    return claim(claim_id)


@app.post("/api/claims/{claim_id}/failure", operation_id="fail_claim_recovery")
def fail_claim_recovery(claim_id: str, request: FailureRequest) -> ClaimBundle:
    """Return a failed recovered claim to the existing retry workflow."""
    fail_claim(claim_id, request.error)
    return claim(claim_id)


@app.post("/api/claims/{claim_id}/payment", operation_id="record_payment")
def payment(claim_id: str, request: PaymentRequest) -> ClaimBundle:
    """Record a claim payment after checking the approval rules."""
    record_payment(claim_id, request)
    return claim(claim_id)


@app.post("/api/claims/{claim_id}/run", operation_id="run_claim", status_code=202)
def run_claim(
    claim_id: str, request: RunRequest, background_tasks: BackgroundTasks
) -> RunResponse:
    """Start the UnderCover Agent on a waiting claim."""
    run = create_agent_run(request.thread_id, 1, claim_id)
    background_tasks.add_task(notify_agent_run, run["run_id"])
    return RunResponse(run_id=run["run_id"], claim_id=claim_id, status="in_progress")


@app.post("/api/runs", operation_id="create_queue_run", status_code=202)
def queue_run(request: QueueRunRequest, background_tasks: BackgroundTasks) -> AgentRun:
    """Start a capped queue run for the UnderCover Agent."""
    run = create_agent_run(request.thread_id, request.claim_cap)
    background_tasks.add_task(notify_agent_run, run["run_id"])
    return AgentRun(**run)


@app.get("/api/runs/current", operation_id="get_current_run")
def current_agent_run() -> AgentRun | None:
    """Return the active or latest agent run."""
    run = current_run()
    return AgentRun(**run) if run else None


@app.get("/api/runs/{run_id}", operation_id="get_agent_run")
def get_agent_run(run_id: int) -> AgentRun:
    """Return one agent run and its ordered to-dos."""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return AgentRun(**run)


@app.post("/api/runs/{run_id}/plan", operation_id="plan_agent_run")
def plan_agent_run(run_id: int, request: RunPlanRequest) -> AgentRun:
    """Save an agent-selected ordered plan within the run cap."""
    return AgentRun(**save_plan(run_id, request.claim_ids, request.model_name))


@app.post("/api/runs/{run_id}/work", operation_id="work_run_claim")
def work_run_claim(run_id: int, request: WorkRequest) -> WorkOutcome:
    """Work one selected claim through the agent service."""
    return WorkOutcome(**work_claim(run_id, request.claim_id, run_claim_via_service))


@app.post("/api/runs/{run_id}/finish", operation_id="finish_agent_run")
def finish_agent_run(run_id: int, request: FinishRunRequest) -> AgentRun:
    """Complete a run after checking for unfinished selected claims."""
    return AgentRun(**finish_run(run_id, request.summary))


@app.post("/api/claims/{claim_id}/model-calls", operation_id="record_model_call")
def model_call(claim_id: str, request: ModelCallRequest) -> ClaimBundle:
    """Record the exact OpenRouter usage reported for one model call."""
    if not record_model_call(claim_id, request.model_dump()):
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim(claim_id)


@app.post("/api/claims/{claim_id}/work-periods", operation_id="record_work_period")
def work_period(claim_id: str, request: WorkPeriodRequest) -> ClaimBundle:
    """Record one period of active agent work on a claim."""
    if not record_work_period(claim_id, request.model_dump()):
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim(claim_id)


@app.post("/api/claims/{claim_id}/activity", operation_id="record_activity")
def activity(claim_id: str, request: ActivityRequest) -> ClaimBundle:
    """Record an agent activity entry for a claim."""
    if get_claim_bundle(claim_id) is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    add_activity(claim_id, request.message)
    return claim(claim_id)


@app.post(
    "/api/claims/{claim_id}/document-search", operation_id="search_claim_documents"
)
def document_search(
    claim_id: str, request: DocumentSearchRequest
) -> list[DocumentSectionSummary]:
    """Search policy or manual sections relevant to a claim."""
    if get_claim_bundle(claim_id) is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return [
        DocumentSectionSummary(**section)
        for section in search_document(request.document, request.query)
    ]


@app.post("/api/claims/{claim_id}/document-section", operation_id="read_claim_document")
def document_section(
    claim_id: str, request: DocumentSectionRequest
) -> DocumentSectionContent:
    """Read and record one policy or manual section for a claim."""
    if get_claim_bundle(claim_id) is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    section = read_document_section(claim_id, request.document, request.section_id)
    if section is None:
        raise HTTPException(status_code=404, detail="Document section not found")
    return DocumentSectionContent(**section)


@app.get("/api/agent/instructions", operation_id="get_agent_instructions")
def agent_instructions() -> Instructions:
    """Return the queue and claim agents' read-only instructions."""
    queue = (
        Path(__file__).with_name("queue_instructions.md").read_text(encoding="utf-8")
    )
    claim_text = (
        Path(__file__).with_name("agent_instructions.md").read_text(encoding="utf-8")
    )
    return Instructions(queue=queue, claim=claim_text)


@app.get("/api/handlers", operation_id="list_handlers")
def handlers() -> list[dict[str, Any]]:
    """List the claims handlers."""
    return list_handlers()
