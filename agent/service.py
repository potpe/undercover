"""The UnderCover Agent's separate web service and claim-work harness."""

import contextvars
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from http import HTTPStatus
from threading import Lock
from typing import Any, Literal

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command
from pydantic import BaseModel

from agent import dispatcher, improver, improver_api
from agent.checkpoints import (
    clear_claim_thread,
    close_checkpoint_pool,
    improver_checkpointer,
)
from agent.main import backend, current_bundle
from agent.main import run_with_context as claim_run
from agent.tracing import trace_round
from backend.models import (
    DispatchRequest,
    InternalProposalDecision,
    RoundRecord,
    RunRecord,
    StartRoundRequest,
)

STARTUP_ATTEMPTS = 10
_claim_locks: dict[str, Lock] = {}
_run_slot = Lock()
_active_work = {"name": "another run"}


def acquire_slot(name: str) -> None:
    if not _run_slot.acquire(blocking=False):
        raise HTTPException(
            status_code=409, detail=f"{_active_work['name']} is running"
        )
    _active_work["name"] = name


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


def get_backend(path: str) -> dict:
    with backend() as client:
        response = client.get(path)
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
        return contextvars.Context().run(
            claim_run, claim_id, thread_id, run_id, run_kind
        )


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
        try:
            context = post_backend(
                f"/api/internal/runs/{run_id}/claims/{claim_id}/begin"
            )
        except httpx.HTTPStatusError as error:
            if error.response.status_code == HTTPStatus.CONFLICT and item.get(
                "round_id"
            ):
                return {
                    "status": "stopped",
                    "claim_id": claim_id,
                    "error": error.response.json()["detail"],
                }
            raise
        if context["status"] in ("done", "left_for_handler"):
            clear_evaluation_progress(item, context["thread_id"])
            return context
        result = contextvars.Context().run(
            claim_run,
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
    if (
        item.get("round_id")
        and isinstance(error, httpx.HTTPStatusError)
        and error.response.status_code == HTTPStatus.CONFLICT
    ):
        return {"status": "stopped", "claim_id": item["claim_id"], "error": str(error)}
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
    waiting = post_backend("/api/internal/rounds/recover")
    for record in waiting["rounds"]:
        recover_round_decision(record)
    recover_signed_off_claims()


def recovery_command(proposal: dict) -> Command:
    decision = {"type": "approve" if proposal["status"] == "approved" else "reject"}
    if decision["type"] == "reject":
        decision["message"] = proposal["decision_reason"]
    return Command(resume={"decisions": [decision]})


def recover_round_decision(record: dict) -> None:
    proposal = record["proposals"][-1]
    if proposal["status"] not in ("approved", "rejected"):
        return
    with improver_checkpointer() as saver:
        recipe = improver_api.read_recipe(record["round_id"])
        graph = improver.make_improver(
            record, recipe["recipe"], saver, evaluate_candidate
        )
        snapshot = graph.get_state({"configurable": {"thread_id": record["thread_id"]}})
        actions = [
            action
            for task in snapshot.tasks
            for interrupt in task.interrupts
            for action in interrupt.value["action_requests"]
        ]
    if len(actions) != 1 or actions[0]["args"] != proposal["payload"]:
        post_backend(f"/api/internal/rounds/{record['round_id']}/stop")
        return
    acquire_slot(f"improver round {record['round_id']}")
    round_in_background(record["round_id"], recovery_command(proposal))


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
        if run["round_id"] is not None:
            try:
                improver_api.check_budget(run["round_id"])
            except httpx.HTTPStatusError:
                post_backend(
                    f"/api/internal/runs/{run_id}/stop",
                    {"error": "Round stopped or spending limit reached"},
                )
                return "stopped"
        finished = post_backend(f"/api/internal/runs/{run_id}/finish")
        return finished["status"]
    except Exception as error:
        if (
            run["round_id"] is None
            or improver_api.read_round(run["round_id"])["status"] == "open"
        ):
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
    acquire_slot("dispatch or evaluation")
    try:
        configuration = get_backend("/api/agent/configuration/current")
        payload = request.model_dump()
        payload.update(
            configuration_version=configuration["version"],
            model_name=configuration["model_name"],
            reasoning_effort=configuration["reasoning_effort"],
            round_id=None,
            round_purpose=None,
        )
        run = post_backend(path, payload)
        _active_work["name"] = f"{run['kind']} {run['run_id']}"
        background_tasks.add_task(run_in_background, run["run_id"])
        return run
    except Exception:
        _run_slot.release()
        raise


def evaluate_round_set(
    round_id: int, version: int, evaluation_set: str, purpose: str
) -> str:
    record = improver_api.check_budget(round_id)
    configuration = get_backend(f"/api/agent/configuration/{version}")
    run = post_backend(
        "/api/internal/evaluations",
        {
            "concurrency": record["settings"]["evaluation_concurrency"],
            "configuration_version": version,
            "model_name": configuration["model_name"],
            "reasoning_effort": configuration["reasoning_effort"],
            "round_id": round_id,
            "round_purpose": purpose,
            "evaluation_set": evaluation_set,
            "tiny": False,
        },
    )
    return contextvars.Context().run(execute_stored_run, run["run_id"])


def baseline(round_id: int) -> None:
    record = improver_api.read_round(round_id)
    counts = improver_api.read_results(round_id)["qualifying_counts"]
    progress = min(1, counts["evaluation"]) + min(2, counts["held_out"])
    for evaluation_set, required in (("evaluation", 1), ("held_out", 2)):
        for _ in range(max(0, required - counts[evaluation_set])):
            progress += 1
            post_backend(
                f"/api/internal/rounds/{round_id}/stage",
                {"stage": "baseline", "progress": progress},
            )
            if (
                evaluate_round_set(
                    round_id, record["starting_version"], evaluation_set, "baseline"
                )
                != "completed"
            ):
                raise RuntimeError("Baseline evaluations were stopped")
    post_backend(
        f"/api/internal/rounds/{round_id}/stage", {"stage": "working", "progress": 3}
    )


def evaluate_candidate(round_id: int) -> dict:
    record = improver_api.read_round(round_id)
    if record["verdict"] is not None:
        return record
    if record["candidate_version"] is None:
        return {"error": "Approve a configuration proposal first"}
    try:
        for progress, name in enumerate(
            ("evaluation", "held_out", "held_out"), start=1
        ):
            post_backend(
                f"/api/internal/rounds/{round_id}/stage",
                {"stage": "evaluating", "progress": progress},
            )
            if (
                evaluate_round_set(
                    round_id, record["candidate_version"], name, "candidate"
                )
                != "completed"
            ):
                return improver_api.record_verdict(
                    round_id, "Candidate evaluations were stopped"
                )
        return improver_api.record_verdict(round_id, None)
    except httpx.HTTPError as error:
        return improver_api.record_verdict(round_id, str(error))


def invoke_improver(round_id: int, command: Command | None) -> None:
    record = improver_api.read_round(round_id)
    recipe = improver_api.read_recipe(round_id)
    with improver_checkpointer() as saver, trace_round(round_id) as callbacks:
        graph = improver.make_improver(
            record, recipe["recipe"], saver, evaluate_candidate
        )
        config: RunnableConfig = {
            "configurable": {"thread_id": record["thread_id"]},
            "callbacks": callbacks,
            "metadata": {"langfuse_session_id": f"Improvement {round_id}"},
            "recursion_limit": 200,
        }
        inputs: Any = (
            command
            if command is not None
            else {
                "messages": [
                    {
                        "role": "user",
                        "content": "Please carry out one round of your recipe.",
                    }
                ]
            }
        )
        for _ in range(improver.MAX_MODEL_CALLS):
            result = graph.invoke(inputs, config, durability="sync")
            if "__interrupt__" not in result:
                improver_api.finish_round(
                    round_id, "The improver finished without a further proposal"
                )
                return
            actions = result["__interrupt__"][0].value["action_requests"]
            if len(actions) != 1:
                inputs = Command(
                    resume={
                        "decisions": [
                            {
                                "type": "reject",
                                "message": "Propose one change at a time",
                            }
                            for _ in actions
                        ]
                    }
                )
                continue
            try:
                improver_api.record_proposal(round_id, actions[0]["args"])
                return
            except httpx.HTTPStatusError as error:
                if error.response.status_code != HTTPStatus.UNPROCESSABLE_ENTITY:
                    raise
                inputs = Command(
                    resume={
                        "decisions": [
                            {
                                "type": "reject",
                                "message": error.response.json()["detail"],
                            }
                        ]
                    }
                )
        improver_api.finish_round(round_id, "Model call limit reached")


def round_in_background(round_id: int, command: Command | None = None) -> None:
    try:
        record = improver_api.read_round(round_id)
        if record["stage"] == "baseline":
            baseline(round_id)
        invoke_improver(round_id, command)
    except Exception as error:
        if improver_api.read_round(round_id)["status"] == "open":
            improver_api.finish_round(round_id, str(error))
    finally:
        _run_slot.release()


def start_round(
    request: StartRoundRequest, background_tasks: BackgroundTasks
) -> RoundRecord:
    """Create and start one improvement round in the background."""
    acquire_slot("improver round")
    try:
        record = post_backend("/api/internal/rounds", request.model_dump())
        _active_work["name"] = f"improver round {record['round_id']}"
        background_tasks.add_task(round_in_background, record["round_id"])
        return RoundRecord(**record)
    except httpx.HTTPStatusError as error:
        _run_slot.release()
        raise HTTPException(
            status_code=error.response.status_code,
            detail=error.response.json()["detail"],
        ) from error
    except Exception:
        _run_slot.release()
        raise


def resume_round(
    round_id: int, request: InternalProposalDecision, background_tasks: BackgroundTasks
) -> RoundRecord:
    """Record an accepted handler decision before resuming the improver."""
    acquire_slot(f"improver round {round_id}")
    try:
        record = post_backend(
            f"/api/internal/rounds/{round_id}/decision", request.model_dump()
        )
        decision: dict = {"type": request.decision}
        if request.decision == "reject":
            decision["message"] = request.reason
        command = Command(resume={"decisions": [decision]})
        background_tasks.add_task(round_in_background, round_id, command)
        return RoundRecord(**record)
    except httpx.HTTPStatusError as error:
        _run_slot.release()
        raise HTTPException(
            status_code=error.response.status_code,
            detail=error.response.json()["detail"],
        ) from error
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

app.post("/rounds", operation_id="run_improvement_round", status_code=202)(start_round)
app.post(
    "/rounds/{round_id}/decision",
    operation_id="resume_improvement_round",
    status_code=202,
)(resume_round)


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
