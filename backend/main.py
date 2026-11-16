"""The UnderCover backend."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from http import HTTPStatus
from pathlib import Path, PurePosixPath
from typing import Annotated, Any

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from mangum import Mangum

from backend import agent_service
from backend.auth import agent_key, agent_or_handler, current_handler
from backend.configurations import (
    agent_configuration,
    current_recipe,
    get_configuration,
    list_configurations,
    list_recipes,
)
from backend.database import (
    add_activity,
    get_claim_bundle,
    get_customer_history,
    get_policy,
    initialize_database,
    list_claims,
    list_handlers,
    record_model_call,
    record_work_period,
)
from backend.evaluations import (
    create_evaluation,
    evaluation_record,
    list_evaluations,
    save_evaluation_scores,
)
from backend.improvement import list_rounds
from backend.improvement_budget import check_run
from backend.improvement_routes import router as improvement_router
from backend.models import (
    ActivityRequest,
    ActivityStepRequest,
    ClaimBundle,
    ClaimSummary,
    CompleteWorkRequest,
    ConfigurationHistory,
    ConfigurationVersion,
    CreateEvaluationRequest,
    CreateRunRequest,
    DecisionRequest,
    DispatchRequest,
    EvaluationRecord,
    EvaluationRequest,
    FailureRequest,
    GatewayHoldRequest,
    Handler,
    Instructions,
    ModelCallRequest,
    PaymentRequest,
    PayoutRequest,
    PayoutResponse,
    PlatformSettings,
    PolicyRecord,
    PreviousClaim,
    RecipeHistory,
    RoundRecord,
    RunRecord,
    RunRequest,
    RunResponse,
    SettingChange,
    SettingsUpdate,
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
from backend.settings import get_settings, list_setting_changes, update_settings
from backend.workflow import (
    gateway_hold,
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
app.include_router(improvement_router)
DASHBOARD_DIST = Path(__file__).resolve().parent.parent / "dashboard" / "dist"


def register_dashboard_fallback(target_app: FastAPI, dashboard_dir: Path) -> bool:
    """Register safe static and SPA routes when a built dashboard is available."""
    if not dashboard_dir.is_dir() or not (dashboard_dir / "index.html").is_file():
        return False
    root = dashboard_dir.resolve()
    index = root / "index.html"

    @target_app.get("/{path:path}", include_in_schema=False, name="dashboard_fallback")
    def dashboard_fallback(path: str) -> Response:
        parts = PurePosixPath(path).parts
        if path == "api" or path.startswith("api/") or "\\" in path or ".." in parts:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        try:
            target = (root / path).resolve(strict=True) if path else index
        except FileNotFoundError:
            target = index
        except (OSError, RuntimeError):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        try:
            target.relative_to(root)
        except ValueError:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        if target.is_file():
            return FileResponse(target)
        return FileResponse(index)

    return True


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


@app.get("/api/configuration", operation_id="list_configuration_versions")
def configuration_history(
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> ConfigurationHistory:
    """List claim configuration versions and their diffs."""
    return ConfigurationHistory(**list_configurations())


@app.get("/api/configuration/{version}", operation_id="get_configuration_version")
def configuration_version(
    version: int,
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> ConfigurationVersion:
    """Return one configuration version and its preceding diff."""
    return ConfigurationVersion(**get_configuration(version))


@app.get(
    "/api/agent/configuration/current",
    operation_id="get_current_agent_configuration",
    dependencies=[Depends(agent_key)],
)
def current_agent_configuration() -> dict[str, Any]:
    """Return the current score-free claim agent configuration."""
    return agent_configuration()


@app.get(
    "/api/agent/configuration/{version}",
    operation_id="get_agent_configuration",
    dependencies=[Depends(agent_key)],
)
def selected_agent_configuration(version: int) -> dict[str, Any]:
    """Return one score-free claim agent configuration version."""
    return agent_configuration(version)


@app.get("/api/recipes", operation_id="list_recipe_versions")
def recipe_history(
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> RecipeHistory:
    """List improvement recipe versions and their diffs."""
    return RecipeHistory(**list_recipes())


@app.get(
    "/api/internal/recipes/current",
    operation_id="get_current_recipe",
    dependencies=[Depends(agent_key)],
)
def internal_current_recipe() -> dict[str, Any]:
    """Return the current recipe for the improver service."""
    return current_recipe()


@app.get("/api/settings", operation_id="get_platform_settings")
def platform_settings(
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> PlatformSettings:
    """Return platform settings and the read-only allowed models."""
    return PlatformSettings(**get_settings())


@app.put("/api/settings", operation_id="update_platform_settings")
def change_platform_settings(
    request: SettingsUpdate,
    handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> PlatformSettings:
    """Change platform settings and record the signed-in handler."""
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only a handler who can approve over-limit payouts may change settings"
            ),
        )
    return PlatformSettings(
        **update_settings(request.model_dump(), handler["handler_id"])
    )


@app.get("/api/settings/changes", operation_id="list_setting_changes")
def setting_changes(
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> list[SettingChange]:
    """List audited platform setting changes, newest first."""
    return [SettingChange(**change) for change in list_setting_changes()]


@app.get("/api/rounds", operation_id="list_improvement_rounds")
def improvement_rounds(
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> list[RoundRecord]:
    """List improvement rounds, newest first."""
    return [RoundRecord(**round) for round in list_rounds()]


@app.get(
    "/api/claims",
    operation_id="list_claims",
    dependencies=[Depends(agent_or_handler)],
)
def claims() -> list[ClaimSummary]:
    """List the operational claims queue."""
    return [ClaimSummary(**claim) for claim in list_claims()]


@app.post(
    "/api/internal/claims/{claim_id}/work",
    operation_id="start_internal_named_work",
    dependencies=[Depends(agent_key)],
)
def internal_start_named_work(claim_id: str, request: RunRequest) -> RunResponse:
    """Start one named claim for the agent command line."""
    start_run(claim_id, request.thread_id)
    return RunResponse(claim_id=claim_id, status="in_progress")


@app.post(
    "/api/internal/claims/{claim_id}/gateway-hold",
    operation_id="record_gateway_payment_hold",
    dependencies=[Depends(agent_key)],
)
def internal_gateway_hold(claim_id: str, request: GatewayHoldRequest) -> ClaimBundle:
    """Record a Gateway payment refusal and require handler sign-off."""
    gateway_hold(claim_id, request)
    return claim(claim_id)


@app.get(
    "/api/claims/{claim_id}",
    operation_id="get_claim",
    dependencies=[Depends(agent_or_handler)],
)
def claim(claim_id: str) -> ClaimBundle:
    """Return a claim and its related records."""
    bundle = get_claim_bundle(claim_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return ClaimBundle(**bundle)


@app.get(
    "/api/policies/{policy_id}",
    operation_id="get_policy",
    dependencies=[Depends(agent_or_handler)],
)
def policy(policy_id: str) -> PolicyRecord:
    """Return one policy record."""
    record = get_policy(policy_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Policy not found")
    return PolicyRecord(**record)


@app.get(
    "/api/customers/{customer_id}/history",
    operation_id="get_claim_history",
    dependencies=[Depends(agent_or_handler)],
)
def customer_history(customer_id: str) -> list[PreviousClaim]:
    """Return a customer's previous claims."""
    history = get_customer_history(customer_id)
    if history is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return [PreviousClaim(**claim) for claim in history]


@app.get(
    "/api/agent/openapi.json",
    operation_id="get_agent_openapi",
    dependencies=[Depends(agent_or_handler)],
)
def agent_openapi() -> dict[str, Any]:
    """Return the generated OpenAPI contract for claim-agent tools."""
    operation_ids = {
        "get_claim",
        "get_policy",
        "get_claim_history",
        "calculate_payout",
        "record_decision",
        "pay_claim",
    }
    schema = deepcopy(app.openapi())
    schema.pop("servers", None)
    filtered_paths: dict[str, Any] = {}
    for path, path_item in schema["paths"].items():
        filtered_item = {
            key: operation
            for key, operation in path_item.items()
            if key in ("get", "post", "put", "patch", "delete", "options", "head")
            and operation.get("operationId") in operation_ids
        }
        if filtered_item:
            filtered_paths[path] = filtered_item
    schema["paths"] = filtered_paths
    return schema


@app.post(
    "/api/claims/{claim_id}/payout",
    operation_id="calculate_payout",
    dependencies=[Depends(agent_key)],
)
def payout(claim_id: str, request: PayoutRequest) -> PayoutResponse:
    """Calculate an approved claim's payout using UnderCover's rules."""
    return calculate_claim_payout(claim_id, request)


@app.post(
    "/api/claims/{claim_id}/decision",
    operation_id="record_decision",
    dependencies=[Depends(agent_key)],
)
def decision(claim_id: str, request: DecisionRequest) -> ClaimBundle:
    """Record the agent's decision for a claim."""
    record_decision(claim_id, request)
    return claim(claim_id)


@app.get("/api/me", operation_id="get_current_handler")
def get_current_handler(
    handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> Handler:
    """Return the signed-in handler and their permissions."""
    return Handler(**handler)


@app.post("/api/claims/{claim_id}/sign-off", operation_id="sign_off_claim")
def sign_off_claim(
    claim_id: str,
    handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> ClaimBundle:
    """Save the handler's sign-off and pay the approved claim."""
    sign_off(claim_id, handler["handler_id"])
    return claim(claim_id)


@app.post(
    "/api/claims/{claim_id}/failure",
    operation_id="fail_claim_recovery",
    dependencies=[Depends(agent_key)],
)
def fail_claim_recovery(claim_id: str, request: FailureRequest) -> ClaimBundle:
    """Return a failed recovered claim to the existing retry workflow."""
    fail_claim(claim_id, request.error)
    return claim(claim_id)


@app.post(
    "/api/claims/{claim_id}/payment",
    operation_id="pay_claim",
    dependencies=[Depends(agent_key)],
)
def payment(claim_id: str, request: PaymentRequest) -> ClaimBundle:
    """Record a claim payment after checking the approval rules."""
    record_payment(claim_id, request)
    return claim(claim_id)


@app.post("/api/claims/{claim_id}/run", operation_id="run_claim", status_code=202)
def run_claim(
    claim_id: str,
    request: RunRequest,
    background_tasks: BackgroundTasks,
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> RunResponse:
    """Work one named claim without creating a dispatch or evaluation."""
    start_run(claim_id, request.thread_id)
    background_tasks.add_task(
        agent_service.work_named_claim, claim_id, request.thread_id
    )
    return RunResponse(claim_id=claim_id, status="in_progress")


@app.post("/api/dispatches", operation_id="start_dispatch", status_code=202)
def start_dispatch(
    request: DispatchRequest,
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> RunRecord:
    """Ask the agent service to start a dispatch with N claim agents."""
    try:
        response = agent_service.start_dispatch(request.concurrency)
    except httpx.HTTPError as error:
        raise service_error(error) from error
    return RunRecord(**response.json())


@app.get(
    "/api/dispatches/current",
    operation_id="get_current_dispatch",
    dependencies=[Depends(current_handler)],
)
def get_current_dispatch() -> RunRecord | None:
    """Return the latest dispatch and its record-built progress."""
    run = current_dispatch()
    return RunRecord(**run) if run else None


@app.post("/api/evaluations", operation_id="start_evaluation", status_code=202)
def start_evaluation(
    request: EvaluationRequest,
    _handler: Annotated[dict[str, Any], Depends(current_handler)],
) -> RunRecord:
    """Ask the agent service to evaluate a labelled set with N agents."""
    try:
        response = agent_service.start_evaluation(
            request.concurrency, request.evaluation_set, request.tiny
        )
    except httpx.HTTPError as error:
        raise service_error(error) from error
    return RunRecord(**response.json())


@app.get(
    "/api/evaluations",
    operation_id="list_evaluations",
    dependencies=[Depends(current_handler)],
)
def get_evaluations() -> list[EvaluationRecord]:
    """List evaluations newest first with their scoreboard measures."""
    return [EvaluationRecord(**run) for run in list_evaluations()]


@app.get(
    "/api/evaluations/{run_id}",
    operation_id="get_evaluation",
    dependencies=[Depends(current_handler)],
)
def get_evaluation(run_id: int) -> EvaluationRecord:
    """Return one evaluation's measures and claim comparisons."""
    return EvaluationRecord(**evaluation_record(run_id))


@app.post(
    "/api/internal/evaluations",
    operation_id="create_evaluation",
    status_code=201,
    dependencies=[Depends(agent_key)],
)
def internal_create_evaluation(request: CreateEvaluationRequest) -> RunRecord:
    """Create an evaluation and its isolated claim copies for the service."""
    return RunRecord(**create_evaluation(request))


@app.post(
    "/api/internal/dispatches",
    operation_id="create_dispatch",
    status_code=201,
    dependencies=[Depends(agent_key)],
)
def internal_create_dispatch(request: CreateRunRequest) -> RunRecord:
    """Create the agent service's dispatch and select its claims."""
    return RunRecord(**create_dispatch(request))


@app.post(
    "/api/internal/runs/stop-active",
    operation_id="stop_active_runs",
    dependencies=[Depends(agent_key)],
)
def internal_stop_active_runs() -> StoppedRuns:
    """Mark runs left in progress by a stopped agent service as stopped."""
    return StoppedRuns(stopped=stop_active_runs())


@app.get(
    "/api/internal/runs/{run_id}",
    operation_id="get_run",
    dependencies=[Depends(agent_key)],
)
def internal_get_run(run_id: int) -> RunRecord:
    """Return one run for the agent service."""
    return RunRecord(**get_run_or_404(run_id))


@app.post(
    "/api/internal/runs/{run_id}/budget",
    operation_id="check_claim_run_budget",
    dependencies=[Depends(agent_key)],
)
def internal_check_claim_budget(run_id: int) -> dict[str, str]:
    """Refuse a new claim model call after its round stops or exhausts budget."""
    check_run(run_id, spending=True)
    return {"status": "ok"}


def get_run_or_404(run_id: int) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@app.post(
    "/api/internal/runs/{run_id}/claims/{claim_id}/begin",
    operation_id="begin_run_claim",
    dependencies=[Depends(agent_key)],
)
def internal_begin_work(run_id: int, claim_id: str) -> WorkContext:
    """Begin or resume one selected claim on its own durable thread."""
    return WorkContext(**begin_work(run_id, claim_id))


@app.post(
    "/api/internal/runs/{run_id}/claims/{claim_id}/complete",
    operation_id="complete_run_claim",
    dependencies=[Depends(agent_key)],
)
def internal_complete_work(
    run_id: int, claim_id: str, request: CompleteWorkRequest
) -> WorkOutcome:
    """Mark a selected claim done after checking its backend state."""
    return WorkOutcome(**complete_work(run_id, claim_id, request.result))


@app.post(
    "/api/internal/runs/{run_id}/claims/{claim_id}/fail",
    operation_id="fail_run_claim",
    dependencies=[Depends(agent_key)],
)
def internal_fail_work(
    run_id: int, claim_id: str, request: FailureRequest
) -> WorkOutcome:
    """Fail a claim and return whether the same dispatch may retry it."""
    return WorkOutcome(**fail_work(run_id, claim_id, request.error))


@app.post(
    "/api/internal/runs/{run_id}/claims/{claim_id}/leave",
    operation_id="leave_run_claim",
    dependencies=[Depends(agent_key)],
)
def internal_leave_work(
    run_id: int, claim_id: str, request: FailureRequest
) -> WorkOutcome:
    """Leave a rate-limited or timed-out claim for the next dispatch."""
    return WorkOutcome(**leave_work(run_id, claim_id, request.error))


@app.post(
    "/api/internal/runs/{run_id}/stop",
    operation_id="stop_run",
    dependencies=[Depends(agent_key)],
)
def internal_stop_run(run_id: int, request: FailureRequest) -> RunRecord:
    """Stop a run after an agent-service orchestration error."""
    stop_run(run_id, request.error)
    return RunRecord(**get_run_or_404(run_id))


@app.post(
    "/api/internal/runs/{run_id}/finish",
    operation_id="finish_run",
    dependencies=[Depends(agent_key)],
)
def internal_finish_run(run_id: int) -> RunRecord:
    """Finish a run with a summary and totals built from its records."""
    record = finish_run(run_id)
    if record["kind"] == "evaluation":
        save_evaluation_scores(run_id)
    return RunRecord(**record)


@app.post(
    "/api/claims/{claim_id}/model-calls",
    operation_id="record_model_call",
    dependencies=[Depends(agent_key)],
)
def model_call(claim_id: str, request: ModelCallRequest) -> ClaimBundle:
    """Record the exact OpenRouter usage reported for one model call."""
    check_run(request.run_id)
    if not record_model_call(claim_id, request.model_dump()):
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim(claim_id)


@app.post(
    "/api/claims/{claim_id}/work-periods",
    operation_id="record_work_period",
    dependencies=[Depends(agent_key)],
)
def work_period(claim_id: str, request: WorkPeriodRequest) -> ClaimBundle:
    """Record one period of active agent work on a claim."""
    check_run(request.run_id)
    if not record_work_period(claim_id, request.model_dump()):
        raise HTTPException(status_code=404, detail="Claim not found")
    return claim(claim_id)


@app.post(
    "/api/claims/{claim_id}/activity",
    operation_id="record_activity",
    dependencies=[Depends(agent_key)],
)
def activity(claim_id: str, request: ActivityRequest) -> ClaimBundle:
    """Record an agent activity entry for a claim."""
    if get_claim_bundle(claim_id) is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    add_activity(claim_id, request.message)
    return claim(claim_id)


@app.post(
    "/api/claims/{claim_id}/steps",
    operation_id="record_claim_step",
    dependencies=[Depends(agent_key)],
)
def claim_step(claim_id: str, request: ActivityStepRequest) -> ClaimBundle:
    """Record one typed agent step in a claim's chronological activity feed."""
    if get_claim_bundle(claim_id) is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    add_activity(
        claim_id,
        request.message,
        kind=request.kind,
        details=request.details.model_dump(),
    )
    return claim(claim_id)


@app.get(
    "/api/agent/instructions",
    operation_id="get_agent_instructions",
    dependencies=[Depends(current_handler)],
)
def agent_instructions() -> Instructions:
    """Return the current claim agent's read-only instructions."""
    return Instructions(claim=agent_configuration()["instructions"])


@app.get(
    "/api/handlers",
    operation_id="list_handlers",
    dependencies=[Depends(current_handler)],
)
def handlers() -> list[dict[str, Any]]:
    """List the claims handlers."""
    return list_handlers()


@app.api_route(
    "/api/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
def unknown_api_route(path: str) -> JSONResponse:
    """Return a JSON not-found response for unmatched API routes."""
    return JSONResponse(status_code=404, content={"detail": "Not Found"})


register_dashboard_fallback(app, DASHBOARD_DIST)
handler = Mangum(app, lifespan="off")
