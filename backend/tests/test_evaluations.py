from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from backend.database import (
    connect,
    handler_decisions_table,
    held_out_claims_table,
    reseed_database,
    run_claims_table,
)
from backend.evaluations import score_answer
from backend.models import DecisionRequest
from backend.workflow import record_decision


def create_evaluation(client, evaluation_set="evaluation", tiny=False, concurrency=25):
    return client.post(
        "/api/internal/evaluations",
        json={
            "concurrency": concurrency,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "evaluation_set": evaluation_set,
            "tiny": tiny,
        },
    )


def copy_id(client, run_id, source_id):
    with connect() as connection:
        return connection.execute(
            select(run_claims_table.c.claim_id).where(
                run_claims_table.c.run_id == run_id,
                run_claims_table.c.source_claim_id == source_id,
            )
        ).scalar_one()


def decide(claim_id, kind, payout):
    record_decision(
        claim_id,
        DecisionRequest(
            decision=kind,
            payout=payout,
            reason="The agent's reason is grounded in the claim and policy wording.",
            policy_section="Section 3: Trip Delay; Section 10: Payment",
        ),
    )


def test_seeding_stores_sources_and_labels_outside_the_queue(client):
    with connect() as connection:
        held_out = (
            connection.execute(select(held_out_claims_table.c.claim_id)).scalars().all()
        )
        labels = connection.execute(select(handler_decisions_table)).mappings().all()

    assert len(held_out) == 25
    assert len(labels) == 50
    assert (
        sum(
            label["data"]["tiny"]
            for label in labels
            if label["evaluation_set"] == "evaluation"
        )
        == 5
    )
    assert (
        sum(
            label["data"]["tiny"]
            for label in labels
            if label["evaluation_set"] == "held_out"
        )
        == 5
    )
    assert len(client.get("/api/claims").json()) == 25
    assert client.get("/api/evaluations").json() == []


def test_evaluation_copies_have_new_ids_and_leave_queue_unchanged(client):
    before = client.get("/api/claims").json()
    created = create_evaluation(client, tiny=True)
    run = created.json()
    copy = copy_id(client, run["run_id"], "CLM-1007")
    bundle = client.get(f"/api/claims/{copy}")

    assert created.status_code == 201
    assert len(run["claims"]) == 5
    assert copy.startswith(f"EVAL-{run['run_id']}-")
    assert copy != "CLM-1007"
    assert bundle.json()["claim"]["claim_id"] == copy
    assert bundle.json()["evaluation_run_id"] == run["run_id"]
    assert "handler_decision" not in bundle.json()
    assert "handler_payout" not in bundle.json()
    assert "The repair report" not in str(bundle.json())
    assert client.get("/api/claims").json() == before

    client.post("/api/internal/runs/stop-active")
    held = create_evaluation(client, "held_out", True, 5).json()
    assert len(held["claims"]) == 5
    assert held["evaluation_set"] == "held_out"
    assert held["concurrency"] == 5

    client.post("/api/internal/runs/stop-active")
    dispatch = client.post(
        "/api/internal/dispatches",
        json={
            "concurrency": 3,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    ).json()
    assert all(
        not claim["claim_id"].startswith("EVAL-") for claim in dispatch["claims"]
    )


def test_get_evaluation_is_newest_first_and_reseed_empties_scoreboard(client):
    first = create_evaluation(client, tiny=True).json()
    client.post("/api/internal/runs/stop-active")
    second = create_evaluation(client, "held_out", True, 5).json()

    listed = client.get("/api/evaluations").json()
    detail = client.get(f"/api/evaluations/{first['run_id']}")

    assert [run["run_id"] for run in listed] == [second["run_id"], first["run_id"]]
    assert detail.json()["run_id"] == first["run_id"]
    assert len(detail.json()["claim_comparisons"]) == 5
    assert detail.json()["claim_comparisons"][0]["agent_decision"] == "refer"
    assert detail.json()["claim_comparisons"][0]["agent_payout"] == 0
    assert (
        detail.json()["claim_comparisons"][0]["agent_reason"] == "No decision recorded"
    )

    reseed_database()
    assert client.get("/api/evaluations").json() == []


def test_signoff_is_refused_on_an_evaluation_copy(client):
    run = create_evaluation(client, tiny=True).json()
    copy = copy_id(client, run["run_id"], "CLM-1007")
    decide(copy, "approve", 3750)

    response = client.post(
        f"/api/claims/{copy}/sign-off", json={"handler_id": "HND-01"}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Evaluation claims cannot be signed off"}
    assert client.get(f"/api/claims/{copy}").json()["signed_off_by"] is None


def test_score_answer_covers_all_mistake_rules():
    handler = {"decision": "approve", "payout": 1000}
    assert score_answer({"decision": "approve", "payout": 1000}, handler) == {
        "decision_match": True,
        "right": True,
        "mistake_cost": 0,
    }
    assert (
        score_answer({"decision": "approve", "payout": 1000.5}, handler)["right"]
        is True
    )
    over = score_answer({"decision": "approve", "payout": 1002}, handler)
    assert over["right"] is False
    assert over["mistake_cost"] == 2
    assert (
        score_answer({"decision": "decline", "payout": 0}, handler)["mistake_cost"]
        == 1500
    )
    assert (
        score_answer(
            {"decision": "approve", "payout": 900}, {"decision": "decline", "payout": 0}
        )["mistake_cost"]
        == 900
    )
    assert (
        score_answer({"decision": "refer", "payout": 0}, handler)["mistake_cost"] == 50
    )
    assert score_answer(None, handler)["mistake_cost"] == 50
    assert (
        score_answer(
            {"decision": "decline", "payout": 0}, {"decision": "decline", "payout": 0}
        )["mistake_cost"]
        == 0
    )
    assert (
        score_answer(
            {"decision": "refer", "payout": 0}, {"decision": "refer", "payout": 0}
        )["right"]
        is True
    )


def test_evaluation_measures_match_hand_calculated_values(client, monkeypatch):
    run = create_evaluation(client, tiny=True).json()
    run_id = run["run_id"]
    decisions = {
        "CLM-1007": ("approve", 50),
        "CLM-1015": ("decline", 0),
        "CLM-1020": ("approve", 241),
        "CLM-1021": ("decline", 0),
    }
    for source, (kind, payout) in decisions.items():
        decide(copy_id(client, run_id, source), kind, payout)
    for index, source in enumerate(
        ("CLM-1007", "CLM-1015", "CLM-1017", "CLM-1020", "CLM-1021"), start=1
    ):
        copy = copy_id(client, run_id, source)
        client.post(
            f"/api/claims/{copy}/model-calls",
            json={
                "run_id": run_id,
                "model_name": "openai/gpt-6-luna",
                "reasoning_effort": "low",
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "cost": 0.1,
            },
        )
        client.post(
            f"/api/claims/{copy}/work-periods",
            json={
                "run_id": run_id,
                "thread_id": f"thread-{copy}",
                "started_at": "2026-11-10T12:00:00Z",
                "finished_at": f"2026-11-10T12:00:0{index}Z",
                "seconds": index * 2,
            },
        )
    monkeypatch.setattr(
        "backend.runs.timestamp",
        lambda: datetime(2026, 11, 10, 12, 1, tzinfo=UTC),
    )
    client.post(f"/api/internal/runs/{run_id}/finish")

    detail = client.get(f"/api/evaluations/{run_id}").json()
    measures = detail["measures"]
    assert measures["decision_accuracy"] == pytest.approx(0.8)
    assert measures["accuracy"] == pytest.approx(0.8)
    assert measures["cost_of_mistakes"] == 1501
    assert measures["straight_through"] == pytest.approx(0.8)
    assert measures["model_cost"] == pytest.approx(0.5)
    assert measures["model_cost_per_claim"] == pytest.approx(0.1)
    assert measures["seconds_per_claim"] == 6
    assert measures["slowest_claim"] == 10
    assert measures["seconds"] == 60
    misses = [claim for claim in detail["claim_comparisons"] if not claim["right"]]
    assert len(misses) == 1
    assert misses[0]["handler_decision"] == "approve"
    assert misses[0]["agent_decision"] == "decline"


def test_stopped_evaluation_scores_undecided_as_referral(client):
    run = create_evaluation(client, tiny=True).json()
    client.post("/api/internal/runs/stop-active")

    detail = client.get(f"/api/evaluations/{run['run_id']}").json()

    assert detail["status"] == "stopped"
    assert detail["stopped_at"] is not None
    assert detail["measures"]["straight_through"] == 0
    assert all(
        claim["agent_decision"] == "refer" for claim in detail["claim_comparisons"]
    )
