"""The UnderCover Agent's separate web service and claim-work harness."""

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from threading import Lock
from typing import Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from pydantic import BaseModel

from agent import dispatcher
from agent.checkpoints import clear_claim_thread, close_checkpoint_pool
from agent.main import backend, current_bundle, model_settings
from agent.main import run_with_context as claim_run
from backend.models import DispatchRequest, RunRecord

STARTUP_ATTEMPTS = 10
_claim_locks: dict[str, Lock] = {}
_run_slot = Lock()


class ClaimWorkRequest(BaseModel):
    thread_id: str
    run_id: int | None
    run_kind: Literal["dispatch", "evaluation"]


class ClaimWorkResponse(BaseModel):
    result: str


class EvaluationRequest(DispatchRequest):
    evaluation_set: Literal["evaluation", "held_out"]
    tiny: bool


class AcceptedResponse(BaseModel):
    status: Literal["accepted"]


def post_backend(path: str, body=None) -> dict:
    with backend() as client:
        response = client.post(path, json=body)
    response.raise_for_status()
    return response.json()


def run_claim_locked(
    claim_id: str,
    thread_id: str,
    run_id: int | None = None,
    run_kind: str = "dispatch",
) -> str:
    lock = _claim_locks.setdefault(claim_id, Lock())
    with lock:
        return claim_run(claim_id, thread_id, run_id, run_kind)


def clear_evaluation_progress(item: dispatcher.WorkItem, thread_id: str | None) -> None:
    if item["run_kind"] == "evaluation" and thread_id:
        clear_claim_thread(thread_id)


def work_claim(item: dispatcher.WorkItem) -> dict:
    """Begin, run and settle one claim; transient model errors escape unchanged."""
    claim_id = item["claim_id"]
    run_id = item["run_id"]
    lock = _claim_locks.setdefault(claim_id, Lock())
    if not lock.acquire(blocking=False):
        return post_backend(
            f"/api/internal/runs/{run_id}/claims/{claim_id}/leave",
            {"error": "Claim is already resuming after sign-off"},
        )
    try:
        context = post_backend(f"/api/internal/runs/{run_id}/claims/{claim_id}/begin")
        if context["status"] in ("done", "left_for_handler"):
            clear_evaluation_progress(item, context["thread_id"])
            return context
        result = claim_run(
            claim_id,
            context["thread_id"],
            run_id,
            item["run_kind"],
        )
        outcome = post_backend(
            f"/api/internal/runs/{run_id}/claims/{claim_id}/complete",
            {"result": result},
        )
        clear_evaluation_progress(item, context["thread_id"])
        return outcome
    finally:
        lock.release()


def handle_work_error(item: dispatcher.WorkItem, error: Exception) -> dict:
    claim_id = item["claim_id"]
    run_id = item["run_id"]
    operation = (
        "leave"
        if isinstance(
            error, (dispatcher.ModelRateLimitError, dispatcher.ModelTimeoutError)
        )
        else "fail"
    )
    outcome = post_backend(
        f"/api/internal/runs/{run_id}/claims/{claim_id}/{operation}",
        {"error": str(error) or error.__class__.__name__},
    )
    if outcome["status"] in ("done", "left_for_handler"):
        clear_evaluation_progress(item, current_bundle(claim_id)["agent_thread_id"])
    return outcome


def report_failure(claim_id: str, error: str) -> None:
    post_backend(f"/api/claims/{claim_id}/failure", {"error": error})


def resume_claim(claim_id: str) -> str:
    bundle = current_bundle(claim_id)
    thread_id = bundle["agent_thread_id"]
    if not thread_id:
        raise RuntimeError("Claim has no saved agent thread")
    try:
        return run_claim_locked(claim_id, thread_id, bundle["work_run_id"])
    except Exception as error:
        bundle = current_bundle(claim_id)
        if bundle["claim"]["status"] in ("decided", "waiting_for_sign_off"):
            decision = bundle["decision"]
            result = f"{claim_id}: {decision['decision']} ${decision['payout']:,.2f}"
            if bundle["paid_amount"] is not None:
                result += "; payment recorded"
            return result
        report_failure(claim_id, str(error))
        return str(error)


def recover_signed_off_claims() -> None:
    with backend() as client:
        response = client.get("/api/claims")
    response.raise_for_status()
    for claim in response.json():
        if claim["status"] == "in_progress":
            bundle = current_bundle(claim["claim_id"])
            if bundle["signed_off_by"] and bundle["agent_thread_id"]:
                resume_claim(claim["claim_id"])


def recover_work() -> None:
    post_backend("/api/internal/runs/stop-active")
    recover_signed_off_claims()


def execute_stored_run(run_id: int) -> str:
    with backend() as client:
        response = client.get(f"/api/internal/runs/{run_id}")
    response.raise_for_status()
    run = response.json()
    if run["status"] != "in_progress":
        return run["status"]
    try:
        dispatcher.run_dispatch(
            run_id=run_id,
            run_kind=run["kind"],
            concurrency=run["concurrency"],
            harness=dispatcher.Harness(work=work_claim, on_error=handle_work_error),
        )
        finished = post_backend(f"/api/internal/runs/{run_id}/finish")
        return finished["status"]
    except Exception as error:
        post_backend(f"/api/internal/runs/{run_id}/stop", {"error": str(error)})
        return str(error)


def run_in_background(run_id: int) -> None:
    try:
        execute_stored_run(run_id)
    finally:
        _run_slot.release()


def create_run(
    path: str, request: BaseModel, background_tasks: BackgroundTasks
) -> dict:
    if not _run_slot.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail="Another dispatch or evaluation is running"
        )
    try:
        model_name, reasoning_effort = model_settings()
        payload = request.model_dump()
        payload.update(model_name=model_name, reasoning_effort=reasoning_effort)
        run = post_backend(path, payload)
        background_tasks.add_task(run_in_background, run["run_id"])
        return run
    except Exception:
        _run_slot.release()
        raise


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    for attempt in range(STARTUP_ATTEMPTS):
        try:
            recover_work()
            break
        except httpx.HTTPError:
            if attempt + 1 < STARTUP_ATTEMPTS:
                time.sleep(0.5)
    try:
        yield
    finally:
        close_checkpoint_pool()


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)


@app.post("/dispatches", operation_id="dispatch_claims", status_code=202)
def start_dispatch(
    request: DispatchRequest, background_tasks: BackgroundTasks
) -> RunRecord:
    """Create and start a dispatch in the background with N agents."""
    return RunRecord(
        **create_run("/api/internal/dispatches", request, background_tasks)
    )


@app.post("/evaluations", operation_id="evaluate_claims", status_code=202)
def start_evaluation(
    request: EvaluationRequest, background_tasks: BackgroundTasks
) -> RunRecord:
    """Create and start an evaluation in the background with N agents."""
    return RunRecord(
        **create_run("/api/internal/evaluations", request, background_tasks)
    )


@app.get("/health", operation_id="agent_health")
def health() -> dict[str, str]:
    """Check that the agent service is running."""
    return {"status": "ok"}


@app.post("/claims/{claim_id}/work", operation_id="work_claim")
def work(claim_id: str, request: ClaimWorkRequest) -> ClaimWorkResponse:
    """Work one named claim on its durable thread without a run."""
    return ClaimWorkResponse(
        result=run_claim_locked(
            claim_id, request.thread_id, request.run_id, request.run_kind
        )
    )


@app.post("/claims/{claim_id}/resume", operation_id="resume_claim", status_code=202)
def resume(claim_id: str, background_tasks: BackgroundTasks) -> AcceptedResponse:
    """Resume a signed-off claim in the background."""
    background_tasks.add_task(resume_claim, claim_id)
    return AcceptedResponse(status="accepted")
