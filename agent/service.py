"""The UnderCover Agent's separate web service."""

from typing import Literal

from fastapi import BackgroundTasks, FastAPI
from pydantic import BaseModel

from agent.main import backend, current_bundle
from agent.main import run as claim_run
from agent.queue import run as queue_run
from backend.runs import fail_queue_run

app = FastAPI(title="UnderCover Agent")


class ClaimWorkRequest(BaseModel):
    thread_id: str


class ClaimWorkResponse(BaseModel):
    result: str


class AcceptedResponse(BaseModel):
    status: Literal["accepted"]


def resume_claim(claim_id: str) -> str:
    bundle = current_bundle(claim_id)
    thread_id = bundle["agent_thread_id"]
    if not thread_id:
        raise RuntimeError("Claim has no saved agent thread")
    return claim_run(claim_id, thread_id)


def run_stored_run(run_id: int) -> str:
    with backend() as client:
        response = client.get(f"/api/runs/{run_id}")
    response.raise_for_status()
    run = response.json()
    try:
        return queue_run(run_id, run["thread_id"])
    except Exception as error:
        fail_queue_run(run_id, str(error))
        return str(error)


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
    """Run a stored queue run in the background."""
    background_tasks.add_task(run_stored_run, run_id)
    return AcceptedResponse(status="accepted")
