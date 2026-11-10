import json
from pathlib import Path

import httpx
from sqlalchemy import update

from backend import agent_service
from backend.database import claims_table, connect
from backend.models import DecisionKind, DecisionRequest, PaymentRequest
from backend.workflow import record_decision, record_payment


def create_dispatch(
    client, concurrency=3, model="openai/gpt-6-luna", effort: str | None = "low"
):
    return client.post(
        "/api/internal/dispatches",
        json={
            "concurrency": concurrency,
            "model_name": model,
            "reasoning_effort": effort,
        },
    )


def begin(client, run_id, claim_id):
    return client.post(f"/api/internal/runs/{run_id}/claims/{claim_id}/begin")


def complete(client, run_id, claim_id, result=None):
    return client.post(
        f"/api/internal/runs/{run_id}/claims/{claim_id}/complete",
        json={"result": result or f"{claim_id} done"},
    )


def decide(claim_id, kind: DecisionKind = "approve", payout=420):
    record_decision(
        claim_id,
        DecisionRequest(
            decision=kind,
            payout=payout,
            reason=(
                "The evidence supports this decision under the cited policy wording."
            ),
            policy_section="Section 3: Trip Delay; Section 10: Payment",
        ),
    )


def test_dispatch_selects_all_eligible_claims_oldest_first(client):
    response = create_dispatch(client, concurrency=3)

    assert response.status_code == 201
    run = response.json()
    expected = [
        claim["claim_id"]
        for claim in sorted(
            json.loads(Path("data/claims.json").read_text()),
            key=lambda claim: (claim["submitted_on"], claim["claim_id"]),
        )
    ]
    assert run["kind"] == "dispatch"
    assert run["status"] == "in_progress"
    assert run["concurrency"] == 3
    assert run["model_name"] == "openai/gpt-6-luna"
    assert run["reasoning_effort"] == "low"
    assert [claim["claim_id"] for claim in run["claims"]] == expected
    assert len(run["claims"]) == 25
    assert client.get("/api/dispatches/current").json()["run_id"] == run["run_id"]


def test_dispatch_excludes_settled_held_handler_and_evaluation_claims(client):
    with connect() as connection:
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == "CLM-1001")
            .values(status="decided")
        )
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == "CLM-1002")
            .values(status="waiting_for_sign_off")
        )
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == "CLM-1003")
            .values(status="left_for_handler")
        )
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == "CLM-1004")
            .values(status="in_progress", agent_thread_id="saved-thread")
        )
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == "CLM-1005")
            .values(evaluation_run_id=99)
        )

    run = create_dispatch(client).json()

    assert "CLM-1004" in [claim["claim_id"] for claim in run["claims"]]
    assert len(run["claims"]) == 21
    assert {
        "CLM-1001",
        "CLM-1002",
        "CLM-1003",
        "CLM-1005",
    }.isdisjoint(claim["claim_id"] for claim in run["claims"])


def test_second_active_run_is_refused_and_override_effort_is_recorded(client):
    first = create_dispatch(client, concurrency=1)
    second = create_dispatch(client, concurrency=25)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {"detail": "Another dispatch or evaluation is running"}

    stopped = client.post("/api/internal/runs/stop-active")
    assert stopped.json()["stopped"] == 1
    override = create_dispatch(client, model="openrouter/free", effort=None).json()
    assert override["model_name"] == "openrouter/free"
    assert override["reasoning_effort"] is None


def test_begin_resumes_saved_thread_and_new_failure_gets_a_fresh_thread(client):
    run_id = create_dispatch(client).json()["run_id"]

    started = begin(client, run_id, "CLM-1001").json()
    failed = client.post(
        f"/api/internal/runs/{run_id}/claims/CLM-1001/fail",
        json={"error": "Provider response was invalid"},
    ).json()
    waiting = client.get("/api/claims/CLM-1001").json()
    retried = begin(client, run_id, "CLM-1001").json()

    assert started["thread_id"] == f"{run_id}:CLM-1001:attempt-1"
    assert failed["status"] == "retry"
    assert failed["attempts"] == 1
    assert waiting["claim"]["status"] == "waiting"
    assert retried["thread_id"] == f"{run_id}:CLM-1001:attempt-2"
    assert retried["attempts"] == 1

    client.post(
        f"/api/internal/runs/{run_id}/claims/CLM-1001/fail",
        json={"error": "Provider response was invalid again"},
    )
    left = client.get("/api/claims/CLM-1001").json()
    assert left["claim"]["status"] == "left_for_handler"
    assert begin(client, run_id, "CLM-1001").json()["status"] == "left_for_handler"


def test_record_built_summary_and_usage_totals(client):
    with connect() as connection:
        connection.execute(
            update(claims_table)
            .where(
                ~claims_table.c.claim_id.in_(
                    [
                        "CLM-1001",
                        "CLM-1002",
                        "CLM-1003",
                        "CLM-1004",
                        "CLM-1005",
                        "CLM-1006",
                    ]
                )
            )
            .values(status="decided")
        )
    run_id = create_dispatch(client).json()["run_id"]

    begin(client, run_id, "CLM-1001")
    decide("CLM-1001")
    record_payment("CLM-1001", PaymentRequest(amount=420))
    complete(client, run_id, "CLM-1001")

    begin(client, run_id, "CLM-1002")
    decide("CLM-1002", "decline", 0)
    complete(client, run_id, "CLM-1002")

    begin(client, run_id, "CLM-1003")
    decide("CLM-1003", payout=3750)
    complete(client, run_id, "CLM-1003")

    begin(client, run_id, "CLM-1004")
    decide("CLM-1004", "refer", 0)
    complete(client, run_id, "CLM-1004")

    begin(client, run_id, "CLM-1005")
    for error in ("First invalid response", "Second invalid response"):
        client.post(
            f"/api/internal/runs/{run_id}/claims/CLM-1005/fail",
            json={"error": error},
        )

    begin(client, run_id, "CLM-1006")
    client.post(
        f"/api/internal/runs/{run_id}/claims/CLM-1006/leave",
        json={"error": "Rate limit retries exhausted"},
    )
    client.post(
        "/api/claims/CLM-1001/model-calls",
        json={
            "run_id": run_id,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "total_tokens": 120,
            "cost": 0.125,
        },
    )
    client.post(
        "/api/claims/CLM-1001/work-periods",
        json={
            "run_id": run_id,
            "thread_id": "thread-1",
            "started_at": "2026-11-10T12:00:00Z",
            "finished_at": "2026-11-10T12:00:02Z",
            "seconds": 2,
        },
    )

    finished = client.post(f"/api/internal/runs/{run_id}/finish")
    assert finished.status_code == 200, finished.json()
    run = finished.json()
    assert run["status"] == "completed"
    assert run["summary"]["approved"] == 2
    assert run["summary"]["declined"] == 1
    assert run["summary"]["referred"] == 1
    assert run["summary"]["waiting_for_sign_off"] == [
        {"claim_id": "CLM-1003", "payout": 3750.0}
    ]
    assert run["summary"]["left_for_handler"] == ["CLM-1005"]
    assert run["summary"]["in_progress"] == ["CLM-1006"]
    assert run["model_cost"] == 0.125
    assert run["active_seconds"] == 2.0
    assert run["wall_seconds"] >= 0


def test_public_start_contacts_service_before_creating_any_record(client, monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append(args)
        raise httpx.ConnectError("Agent offline")

    monkeypatch.setattr(agent_service, "agent_post", unavailable)

    response = client.post("/api/dispatches", json={"concurrency": 3})

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "The agent service is not running or could not start"
    )
    assert calls
    assert client.get("/api/dispatches/current").json() is None


def test_stopping_active_run_preserves_claim_threads_and_attempts(client):
    run_id = create_dispatch(client).json()["run_id"]
    started = begin(client, run_id, "CLM-1001").json()

    stopped = client.post("/api/internal/runs/stop-active")

    assert stopped.json()["stopped"] == 1
    run = client.get(f"/api/internal/runs/{run_id}").json()
    claim = client.get("/api/claims/CLM-1001").json()
    assert run["status"] == "stopped"
    assert run["stopped_at"] is not None
    assert claim["claim"]["status"] == "in_progress"
    assert claim["agent_thread_id"] == started["thread_id"]
    assert run["claims"][0]["attempts"] == 0
