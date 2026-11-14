"""The improver's own backend client; claim tools do not import this module."""

from typing import Any

from agent.main import backend


def request(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    with backend() as client:
        response = client.request(method, path, json=body)
    response.raise_for_status()
    return response.json()


def read_round(round_id: int) -> dict:
    return request("GET", f"/api/internal/rounds/{round_id}")


def read_results(round_id: int) -> dict:
    return request("GET", f"/api/internal/rounds/{round_id}/results")


def read_recipe(round_id: int) -> dict:
    return request("GET", f"/api/internal/rounds/{round_id}/recipe")


def check_budget(round_id: int) -> dict:
    return request("POST", f"/api/internal/rounds/{round_id}/check")


def record_usage(round_id: int, values: dict) -> dict:
    return request("POST", f"/api/internal/rounds/{round_id}/model-calls", values)


def record_proposal(round_id: int, payload: dict) -> dict:
    return request(
        "POST", f"/api/internal/rounds/{round_id}/proposal", {"payload": payload}
    )


def apply_proposal(round_id: int, kind: str) -> dict:
    return request("POST", f"/api/internal/rounds/{round_id}/apply/{kind}")


def record_verdict(round_id: int, failure: str | None) -> dict:
    return request(
        "POST", f"/api/internal/rounds/{round_id}/verdict", {"failure": failure}
    )


def finish_round(round_id: int, reason: str) -> dict:
    return request("POST", f"/api/internal/rounds/{round_id}/finish", {"error": reason})
