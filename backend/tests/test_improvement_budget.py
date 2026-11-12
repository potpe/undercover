import pytest
from fastapi import HTTPException
from sqlalchemy import insert, select

from backend import judge
from backend.database import (
    connect,
    judge_grades_table,
    model_calls_table,
    runs_table,
    timestamp,
)
from backend.improvement import (
    create_round,
    get_round,
    record_improver_usage,
    stop_round,
)
from backend.improvement_budget import check_budget, estimate_evaluation
from backend.improvement_proposals import apply_proposal, decide_proposal, save_proposal


def spend(round_id, amount):
    record_improver_usage(
        round_id,
        {
            "call_id": "stub-call",
            "model_name": "stub",
            "reasoning_effort": "medium",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
            "cost": amount,
        },
    )


def evaluation_payload(round_id):
    return {
        "concurrency": 1,
        "configuration_version": 1,
        "model_name": "openai/gpt-6-luna",
        "reasoning_effort": "low",
        "round_id": round_id,
        "round_purpose": "baseline",
        "evaluation_set": "evaluation",
        "tiny": False,
    }


def test_exhausted_budget_refuses_an_evaluation_before_creation(client):
    round_id = create_round("HND-01")["round_id"]
    spend(round_id, 0.5)
    response = client.post(
        "/api/internal/evaluations", json=evaluation_payload(round_id)
    )
    assert response.status_code == 409
    assert "spending limit" in response.json()["detail"]
    assert client.get("/api/evaluations").json() == []


def test_budget_is_checked_before_each_claim_and_model_call(client):
    round_id = create_round("HND-01")["round_id"]
    run = client.post(
        "/api/internal/evaluations", json=evaluation_payload(round_id)
    ).json()
    run_id = run["run_id"]
    spend(round_id, 0.5)
    claim_id = run["claims"][0]["claim_id"]
    response = client.post(f"/api/internal/runs/{run_id}/claims/{claim_id}/begin")
    assert response.status_code == 409
    assert client.post(f"/api/internal/runs/{run_id}/budget").status_code == 409
    assert client.get(f"/api/claims/{claim_id}").json()["failed_attempts"] == 0


def test_stopped_round_refuses_further_records(client):
    round_id = create_round("HND-01")["round_id"]
    stop_round(round_id)
    with connect() as connection, pytest.raises(HTTPException):
        check_budget(connection, round_id)
    with pytest.raises(HTTPException):
        spend(round_id, 0.001)


def test_stop_during_judging_cannot_persist_grades_or_finish_run(client, monkeypatch):
    round_id = create_round("HND-01")["round_id"]
    run = client.post(
        "/api/internal/evaluations", json=evaluation_payload(round_id)
    ).json()
    original = judge.grade_item

    def stop_before_result(item):
        if item["claim_id"] == run["claims"][0]["claim_id"]:
            stop_round(round_id)
        return {"claim_id": item["claim_id"], **judge.ungraded()}

    monkeypatch.setattr(judge, "grade_item", stop_before_result)
    response = client.post(f"/api/internal/runs/{run['run_id']}/finish")
    assert response.status_code == 409
    with connect() as connection:
        assert not connection.execute(select(judge_grades_table)).first()
    assert (
        client.get(f"/api/internal/runs/{run['run_id']}").json()["status"] == "stopped"
    )
    monkeypatch.setattr(judge, "grade_item", original)
    assert (
        client.post(
            f"/api/internal/runs/{run['run_id']}/stop", json={"error": "late"}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    ("operation", "body"),
    [
        ("complete", {"result": "late"}),
        ("fail", {"error": "late"}),
        ("leave", {"error": "late"}),
        ("begin", None),
    ],
)
def test_stopped_round_refuses_all_claim_lifecycle_writes(client, operation, body):
    round_id = create_round("HND-01")["round_id"]
    run = client.post(
        "/api/internal/evaluations", json=evaluation_payload(round_id)
    ).json()
    stop_round(round_id)
    response = client.post(
        f"/api/internal/runs/{run['run_id']}/claims/{run['claims'][0]['claim_id']}/{operation}",
        json=body,
    )
    assert response.status_code == 409


def test_estimate_uses_historical_high_and_scales_new_model_prices(client):
    round_id = create_round("HND-01")["round_id"]
    record = get_round(round_id)
    with connect() as connection:
        assert estimate_evaluation(connection, record, 1, 25) == 0
        for cost in (0.002, 0.003):
            connection.execute(
                insert(runs_table).values(
                    kind="evaluation",
                    concurrency=1,
                    configuration_version=1,
                    round_id=None,
                    model_name="openai/gpt-6-luna",
                    reasoning_effort="low",
                    evaluation_set="evaluation",
                    tiny=False,
                    status="completed",
                    scores={"cost_per_claim": cost},
                    created_at=timestamp(),
                )
            )
        assert estimate_evaluation(connection, record, 1, 25) == pytest.approx(0.075)
    pending = save_proposal(
        round_id,
        "configuration",
        {
            "instructions": "Check coverage",
            "model": "google/gemini-3.5-flash-lite",
            "reasoning_effort": "low",
            "reason": "Test model",
        },
    )
    decide_proposal(
        round_id,
        {
            "proposal_id": pending["proposals"][0]["proposal_id"],
            "decision": "approve",
            "reason": "",
            "handler_id": "HND-01",
        },
    )
    apply_proposal(round_id, "configuration")
    with connect() as connection:
        assert estimate_evaluation(connection, record, 2, 25) == pytest.approx(0.375)
    spend(round_id, 0.2)
    response = client.post(
        "/api/internal/evaluations",
        json={
            **evaluation_payload(round_id),
            "configuration_version": 2,
            "model_name": "google/gemini-3.5-flash-lite",
            "round_purpose": "candidate",
        },
    )
    assert response.status_code == 409
    assert "estimate" in response.json()["detail"]


def test_round_total_includes_improver_claim_and_judge_ledgers_once(client):
    round_id = create_round("HND-01")["round_id"]
    run = client.post(
        "/api/internal/evaluations", json=evaluation_payload(round_id)
    ).json()
    spend(round_id, 0.1)
    spend(round_id, 0.1)
    with connect() as connection:
        connection.execute(
            insert(model_calls_table).values(
                claim_id=run["claims"][0]["claim_id"],
                run_id=run["run_id"],
                model_name="stub",
                reasoning_effort="default",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost=0.2,
                created_at=timestamp(),
            )
        )
        connection.execute(
            insert(judge_grades_table).values(
                run_id=run["run_id"],
                claim_id=run["claims"][0]["claim_id"],
                grade=2,
                letter="B",
                same_grounds=1,
                same_section=1,
                judge_cost=0.003,
            )
        )
    assert get_round(round_id)["cost"] == pytest.approx(0.303)
