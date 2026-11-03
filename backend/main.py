"""The UnderCover backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException

from agent.main import run as agent_run
from backend.database import (
    DATA_DIR,
    add_activity,
    get_claim_bundle,
    initialize_database,
    list_claims,
    list_handlers,
)
from backend.models import (
    ActivityRequest,
    ClaimBundle,
    ClaimSummary,
    DecisionRequest,
    Instructions,
    PaymentRequest,
    PolicyWording,
    RunRequest,
    RunResponse,
    SignOffRequest,
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
    start_run(claim_id)
    background_tasks.add_task(execute_run, claim_id, request.thread_id)
    return RunResponse(claim_id=claim_id, status="in_progress")


@app.post("/api/claims/{claim_id}/activity", operation_id="record_activity")
def activity(claim_id: str, request: ActivityRequest) -> ClaimBundle:
    """Record an agent activity entry for a claim."""
    if get_claim_bundle(claim_id) is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    add_activity(claim_id, request.message)
    return claim(claim_id)


@app.get("/api/agent/instructions", operation_id="get_agent_instructions")
def agent_instructions() -> Instructions:
    """Return the UnderCover Agent's read-only instructions."""
    content = (
        Path(__file__).with_name("agent_instructions.md").read_text(encoding="utf-8")
    )
    return Instructions(content=content)


@app.get("/api/policy-wording", operation_id="get_policy_wording")
def policy_wording() -> PolicyWording:
    """Return the policy wording used for claim decisions."""
    return PolicyWording(
        version="2026.1",
        content=(DATA_DIR / "policy_wording.md").read_text(encoding="utf-8"),
    )


@app.get("/api/handlers", operation_id="list_handlers")
def handlers() -> list[dict[str, Any]]:
    """List the claims handlers."""
    return list_handlers()
