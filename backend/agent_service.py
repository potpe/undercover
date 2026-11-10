"""HTTP boundary between the UnderCover backend and agent service."""

import os
from contextlib import suppress
from typing import Any

import httpx


def agent_post(
    path: str, body: dict[str, Any] | None = None, timeout: float = 10
) -> httpx.Response:
    base_url = os.getenv("UNDERCOVER_AGENT", "http://127.0.0.1:8002")
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        return client.post(path, json=body)


def start_dispatch(concurrency: int) -> httpx.Response:
    response = agent_post("/dispatches", {"concurrency": concurrency})
    response.raise_for_status()
    return response


def start_evaluation(
    concurrency: int, evaluation_set: str, tiny: bool
) -> httpx.Response:
    response = agent_post(
        "/evaluations",
        {"concurrency": concurrency, "evaluation_set": evaluation_set, "tiny": tiny},
    )
    response.raise_for_status()
    return response


def notify_resume(claim_id: str) -> None:
    with suppress(httpx.HTTPError):
        agent_post(f"/claims/{claim_id}/resume").raise_for_status()


def work_named_claim(claim_id: str, thread_id: str) -> None:
    with suppress(httpx.HTTPError):
        agent_post(
            f"/claims/{claim_id}/work",
            {"thread_id": thread_id, "run_id": None, "run_kind": "dispatch"},
            timeout=120,
        ).raise_for_status()
