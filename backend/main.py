"""The UnderCover backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException

from backend import agent_service
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
from backend.evaluations import (
    create_evaluation,
    evaluation_record,
    list_evaluations,
)
from backend.models import (
    ActivityRequest,
    ClaimBundle,
    ClaimSummary,
    CompleteWorkRequest,
    CreateEvaluationRequest,
    CreateRunRequest,
    DecisionRequest,
    DispatchRequest,
    DocumentSearchRequest,
    DocumentSectionContent,
    DocumentSectionRequest,
    DocumentSectionSummary,
    EvaluationRecord,
    EvaluationRequest,
    FailureRequest,
    Instructions,
    ModelCallRequest,
    PaymentRequest,
    PayoutRequest,
    PayoutResponse,
    RunRecord,
    RunRequest,
    RunResponse,
    SignOffRequest,
    StoppedRuns,
    WorkContext,
    WorkOutcome,
    WorkPeriodRequest,
)
from backend.payout import calculate_claim_payout
from backend.runs import (
    begin_work,
    complete_work,
    create_dispatch,
    current_dispatch,
    fail_claim,
    fail_work,
    finish_run,
    get_run,
    leave_work,
    stop_active_runs,
    stop_run,
)
from backend.workflow import record_decision, record_payment, sign_off, start_run


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


app = FastAPI(title="UnderCover", lifespan=lifespan)


def service_error(error: httpx.HTTPError) -> HTTPException:
    if (
        isinstance(error, httpx.HTTPStatusError)
        and error.response.status_code == HTTPStatus.CONFLICT
    ):
        return HTTPException(
            status_code=HTTPStatus.CONFLICT, detail=error.response.json()["detail"]
        )
    return HTTPException(
        status_code=503, detail="The agent service is not running or could not start"
    )


@app.get("/api/health", operation_id="health")
def health() -> dict[str, str]:
    """Check that the backend is running."""
    return {"status": "ok"}


@app.get("/api/claims", operation_id="list_claims")
def claims() -> list[ClaimSummary]:
    """List the operational claims queue."""
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
    background_tasks.add_task(agent_service.notify_resume, claim_id)
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
    """Work one named claim without creating a dispatch or evaluation."""
    start_run(claim_id)
    background_tasks.add_task(
        agent_service.work_named_claim, claim_id, request.thread_id
    )
    return RunResponse(claim_id=claim_id, status="in_progress")


@app.post("/api/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(request: DispatchRequest) -> RunRecord:
    """Ask the agent service to start a dispatch with N claim agents."""
    try:
        response = agent_service.start_dispatch(request.concurrency)
    except httpx.HTTPError as error:
        raise service_error(error) from error
    return RunRecord(**response.json())


@app.get("/api/dispatches/current", operation_id="get_current_dispatch")
def get_current_dispatch() -> RunRecord | None:
    """Return the latest dispatch and its record-built progress."""
    run = current_dispatch()
    return RunRecord(**run) if run else None


@app.post("/api/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(request: EvaluationRequest) -> RunRecord:
    """Ask the agent service to evaluate a labelled set with N agents."""
    try:
        response = agent_service.start_evaluation(
            request.concurrency, request.evaluation_set, request.tiny
        )
    except httpx.HTTPError as error:
        raise service_error(error) from error
    return RunRecord(**response.json())


@app.get("/api/evaluations", operation_id="list_evaluations")
def get_evaluations() -> list[EvaluationRecord]:
    """List evaluations newest first with their scoreboard measures."""
    return [EvaluationRecord(**run) for run in list_evaluations()]


@app.get("/api/evaluations/{run_id}", operation_id="get_evaluation")
def get_evaluation(run_id: int) -> EvaluationRecord:
    """Return one evaluation's measures and claim comparisons."""
    return EvaluationRecord(**evaluation_record(run_id))


@app.post(
    "/api/internal/evaluations", operation_id="create_evaluation", status_code=201
)
def internal_create_evaluation(request: CreateEvaluationRequest) -> RunRecord:
    """Create an evaluation and its isolated claim copies for the service."""
    return RunRecord(
        **create_evaluation(
            request.concurrency,
            request.evaluation_set,
            request.tiny,
            request.model_name,
            request.reasoning_effort,
        )
    )


@app.post("/api/internal/dispatches", operation_id="create_dispatch", status_code=201)
def internal_create_dispatch(request: CreateRunRequest) -> RunRecord:
    """Create the agent service's dispatch and select its claims."""
    return RunRecord(
        **create_dispatch(
            request.concurrency, request.model_name, request.reasoning_effort
        )
    )


@app.post("/api/internal/runs/stop-active", operation_id="stop_active_runs")
def internal_stop_active_runs() -> StoppedRuns:
    """Mark runs left in progress by a stopped agent service as stopped."""
    return StoppedRuns(stopped=stop_active_runs())


@app.get("/api/internal/runs/{run_id}", operation_id="get_run")
def internal_get_run(run_id: int) -> RunRecord:
    """Return one run for the agent service."""
    return RunRecord(**get_run_or_404(run_id))


def get_run_or_404(run_id: int) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post(
    "/api/internal/runs/{run_id}/claims/{claim_id}/begin",
    operation_id="begin_run_claim",
)
def internal_begin_work(run_id: int, claim_id: str) -> WorkContext:
    """Begin or resume one selected claim on its own durable thread."""
    return WorkContext(**begin_work(run_id, claim_id))


@app.post(
    "/api/internal/runs/{run_id}/claims/{claim_id}/complete",
    operation_id="complete_run_claim",
)
def internal_complete_work(
    run_id: int, claim_id: str, request: CompleteWorkRequest
) -> WorkOutcome:
    """Mark a selected claim done after checking its backend state."""
    return WorkOutcome(**complete_work(run_id, claim_id, request.result))


@app.post(
    "/api/internal/runs/{run_id}/claims/{claim_id}/fail",
    operation_id="fail_run_claim",
)
def internal_fail_work(
    run_id: int, claim_id: str, request: FailureRequest
) -> WorkOutcome:
    """Fail a claim and return whether the same dispatch may retry it."""
    return WorkOutcome(**fail_work(run_id, claim_id, request.error))


@app.post(
    "/api/internal/runs/{run_id}/claims/{claim_id}/leave",
    operation_id="leave_run_claim",
)
def internal_leave_work(
    run_id: int, claim_id: str, request: FailureRequest
) -> WorkOutcome:
    """Leave a rate-limited or timed-out claim for the next dispatch."""
    return WorkOutcome(**leave_work(run_id, claim_id, request.error))


@app.post("/api/internal/runs/{run_id}/stop", operation_id="stop_run")
def internal_stop_run(run_id: int, request: FailureRequest) -> RunRecord:
    """Stop a run after an agent-service orchestration error."""
    stop_run(run_id, request.error)
    return RunRecord(**get_run_or_404(run_id))


@app.post("/api/internal/runs/{run_id}/finish", operation_id="finish_run")
def internal_finish_run(run_id: int) -> RunRecord:
    """Finish a run with a summary and totals built from its records."""
    return RunRecord(**finish_run(run_id))


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
    """Return the claim agent's read-only instructions."""
    claim_text = (
        Path(__file__).with_name("agent_instructions.md").read_text(encoding="utf-8")
    )
    return Instructions(claim=claim_text)


@app.get("/api/handlers", operation_id="list_handlers")
def handlers() -> list[dict[str, Any]]:
    """List the claims handlers."""
    return list_handlers()
