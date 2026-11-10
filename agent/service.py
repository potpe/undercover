"""The UnderCover Agent's separate web service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from threading import Thread
from typing import Literal

import httpx
from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel

from agent.main import backend, current_bundle
from agent.main import run as claim_run
from agent.queue import run as queue_run
from backend.runs import fail_queue_run


class ClaimWorkRequest(BaseModel):
    thread_id: str


class ClaimWorkResponse(BaseModel):
    result: str


class AcceptedResponse(BaseModel):
    status: Literal["accepted"]


def report_failure(claim_id: str, error: str) -> None:
    with backend() as client:
        response = client.post(f"/api/claims/{claim_id}/failure", json={"error": error})
    response.raise_for_status()


def resume_claim(claim_id: str) -> str:
    bundle = current_bundle(claim_id)
    thread_id = bundle["agent_thread_id"]
    if not thread_id:
        raise RuntimeError("Claim has no saved agent thread")
    try:
        return claim_run(claim_id, thread_id)
    except Exception as error:
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
    recover_signed_off_claims()
    with backend() as client:
        response = client.get("/api/runs/current")
    response.raise_for_status()
    run = response.json()
    if run and run["status"] == "in_progress":
        run_stored_run(run["run_id"])


def run_stored_run(run_id: int) -> str:
    recover_signed_off_claims()
    with backend() as client:
        response = client.get(f"/api/runs/{run_id}")
    response.raise_for_status()
    run = response.json()
    try:
        result = queue_run(run_id, run["thread_id"])
        with backend() as client:
            current = client.get(f"/api/runs/{run_id}")
        current.raise_for_status()
        if current.json()["status"] == "in_progress":
            raise RuntimeError("Queue agent finished without completing the run")
        return result
    except Exception as error:
        fail_queue_run(run_id, str(error))
        return str(error)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    def recover() -> None:
        with suppress(httpx.HTTPError):
            recover_work()

    recovery = Thread(target=recover)
    recovery.start()
    try:
        yield
    finally:
        recovery.join()


app = FastAPI(title="UnderCover Agent", lifespan=lifespan)


@app.get("/health", operation_id="agent_health")
def health() -> dict[str, str]:
    """Check that the agent service is running."""
    return {"status": "ok"}


@app.post("/claims/{claim_id}/work", operation_id="work_claim")
def work(claim_id: str, request: ClaimWorkRequest) -> ClaimWorkResponse:
    """Work one claim on its supplied durable thread."""
    return ClaimWorkResponse(result=claim_run(claim_id, request.thread_id))


@app.post("/claims/{claim_id}/resume", operation_id="resume_claim", status_code=202)
def resume(claim_id: str, background_tasks: BackgroundTasks) -> AcceptedResponse:
    """Resume a signed-off claim in the background."""
    background_tasks.add_task(resume_claim, claim_id)
    return AcceptedResponse(status="accepted")


@app.post("/runs/{run_id}", operation_id="run_queue", status_code=202)
def run(run_id: int, background_tasks: BackgroundTasks) -> AcceptedResponse:
    """Run or recover a stored queue run in the background."""
    background_tasks.add_task(run_stored_run, run_id)
    return AcceptedResponse(status="accepted")
