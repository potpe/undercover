"""The UnderCover backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from agent.main import run as agent_run
from backend.database import (
    add_activity,
    get_claim_bundle,
    initialize_database,
    list_claims,
    list_handlers,
    read_document_section,
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
    FinishRunRequest,
    Instructions,
    PaymentRequest,
    QueueRunRequest,
    RunPlanRequest,
    RunRequest,
    RunResponse,
    SignOffRequest,
)
from backend.runs import (
    create_run as create_agent_run,
)
from backend.runs import (
    current_run,
    finish_run,
    get_run,
    save_plan,
)
from backend.workflow import (
    fail_run,
    record_decision,
    record_payment,
    sign_off,
    start_run,
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


def execute_run(claim_id: str, thread_id: str) -> None:
    try:
        agent_run(claim_id, thread_id)
        bundle = get_claim_bundle(claim_id)
        if bundle and bundle["decision"] is None:
            raise RuntimeError("Agent finished without recording a decision")
    except Exception as error:
        fail_run(claim_id, error)


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


@app.post("/api/claims/{claim_id}/decision", operation_id="record_decision")
def decision(claim_id: str, request: DecisionRequest) -> ClaimBundle:
    """Record the agent's decision for a claim."""
    record_decision(claim_id, request)
    return claim(claim_id)


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(claim_id: str, request: SignOffRequest) -> ClaimBundle:
    """Record a handler's sign-off for an over-limit payout."""
    sign_off(claim_id, request)
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
    start_run(claim_id)
    background_tasks.add_task(execute_run, claim_id, request.thread_id)
    return RunResponse(run_id=run["run_id"], claim_id=claim_id, status="in_progress")


@app.post("/api/runs", operation_id="create_queue_run", status_code=202)
def queue_run(request: QueueRunRequest) -> AgentRun:
    """Create a capped queue run for the UnderCover Agent."""
    return AgentRun(**create_agent_run(request.thread_id, request.claim_cap))


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


@app.post("/api/runs/{run_id}/finish", operation_id="finish_agent_run")
def finish_agent_run(run_id: int, request: FinishRunRequest) -> AgentRun:
    """Complete a run after checking for unfinished selected claims."""
    return AgentRun(**finish_run(run_id, request.summary))


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
