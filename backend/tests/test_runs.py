import pytest
from sqlalchemy import update

from backend import main
from backend.database import claims_table, connect, run_todos_table


@pytest.fixture(autouse=True)
def defer_queue_agent(monkeypatch):
    monkeypatch.setattr(main, "execute_queue_run", lambda run_id, thread_id: None)


def create_run(client, cap=5, thread_id="thread-queue"):
    return client.post("/api/runs", json={"thread_id": thread_id, "claim_cap": cap})


def plan(client, run_id, claim_ids, model="deepseek/deepseek-v4.1-flash"):
    return client.post(
        f"/api/runs/{run_id}/plan",
        json={"claim_ids": claim_ids, "model_name": model},
    )


def test_run_starts_empty_and_plan_preserves_agent_order(client):
    created = create_run(client)
    assert created.status_code == 202
    run = created.json()
    assert run["thread_id"] == "thread-queue"
    assert run["claim_cap"] == 5
    assert run["target_claim_id"] is None
    assert run["status"] == "in_progress"
    assert run["todos"] == []

    response = plan(
        client,
        run["run_id"],
        ["CLM-1005", "CLM-1002", "CLM-1004", "CLM-1001", "CLM-1003"],
    )

    assert response.status_code == 200
    todos = response.json()["todos"]
    assert [todo["claim_id"] for todo in todos] == [
        "CLM-1005",
        "CLM-1002",
        "CLM-1004",
        "CLM-1001",
        "CLM-1003",
    ]
    assert [todo["position"] for todo in todos] == [1, 2, 3, 4, 5]
    assert {todo["status"] for todo in todos} == {"to_do"}
    assert all(todo["attempts"] == 0 for todo in todos)
    assert all(todo["result"] is None and todo["error"] is None for todo in todos)
    assert response.json()["model_name"] == "deepseek/deepseek-v4.1-flash"


def test_plan_rejects_over_cap_duplicates_and_underfill(client):
    run_id = create_run(client).json()["run_id"]

    over_cap = plan(client, run_id, [f"CLM-{number}" for number in range(1001, 1008)])
    assert over_cap.status_code == 409
    assert over_cap.json() == {"detail": "Plan exceeds the run's claim cap"}

    duplicates = plan(client, run_id, ["CLM-1001", "CLM-1001"])
    assert duplicates.status_code == 409
    assert duplicates.json() == {"detail": "Plan contains duplicate claims"}

    underfilled = plan(client, run_id, ["CLM-1001", "CLM-1002"])
    assert underfilled.status_code == 409
    assert underfilled.json() == {"detail": "Plan does not fill the run's claim cap"}


def test_plan_rejects_unknown_and_nonwaiting_claims(client):
    run_id = create_run(client, cap=1).json()["run_id"]
    unknown = plan(client, run_id, ["CLM-9999"])
    assert unknown.status_code == 404

    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "decline",
            "payout": 0,
            "reason": "The wording excludes this loss. The cited section applies.",
            "policy_section": "Section 9: General exclusions",
        },
    )
    decided = plan(client, run_id, ["CLM-1001"])
    assert decided.status_code == 409
    assert decided.json() == {"detail": "Plan includes a claim that is not waiting"}


def test_second_plan_is_rejected(client):
    run_id = create_run(client, cap=1).json()["run_id"]
    assert plan(client, run_id, ["CLM-1001"]).status_code == 200

    second = plan(client, run_id, ["CLM-1002"])

    assert second.status_code == 409
    assert second.json() == {"detail": "Run already has a plan"}


def test_cap_boundaries_and_smaller_available_queue(client):
    assert create_run(client, cap=0).status_code == 422
    assert create_run(client, cap=26).status_code == 422

    run_id = create_run(client, cap=25).json()["run_id"]
    response = plan(client, run_id, [f"CLM-{n}" for n in range(1001, 1026)])
    assert response.status_code == 200
    assert len(response.json()["todos"]) == 25


def test_plan_uses_only_available_waiting_claims(client):
    with connect() as connection:
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id.not_in(["CLM-1001", "CLM-1002"]))
            .values(status="decided")
        )
    run_id = create_run(client, cap=5).json()["run_id"]

    response = plan(client, run_id, ["CLM-1002", "CLM-1001"])

    assert response.status_code == 200
    assert len(response.json()["todos"]) == 2


def test_named_claim_run_has_a_fixed_cap_one_target(client, monkeypatch):
    monkeypatch.setattr(main, "agent_run", lambda claim_id, thread_id: "No decision")

    response = client.post("/api/claims/CLM-1001/run", json={"thread_id": "single"})
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    current = client.get("/api/runs/current").json()
    assert current["claim_cap"] == 1
    assert current["target_claim_id"] == "CLM-1001"

    wrong_claim = plan(client, run_id, ["CLM-1002"])
    assert wrong_claim.status_code == 409
    assert wrong_claim.json() == {"detail": "Plan does not match the named claim"}


def test_current_run_keeps_latest_completed_summary(client):
    run_id = create_run(client, cap=1).json()["run_id"]
    plan(client, run_id, ["CLM-1001"])
    with connect() as connection:
        connection.execute(
            update(run_todos_table)
            .where(run_todos_table.c.run_id == run_id)
            .values(status="done")
        )
    unfinished = client.post(
        f"/api/runs/{run_id}/finish", json={"summary": "One claim completed."}
    )
    assert unfinished.status_code == 200
    assert unfinished.json()["status"] == "completed"

    current = client.get("/api/runs/current")
    specific = client.get(f"/api/runs/{run_id}")
    assert current.json()["run_id"] == run_id
    assert current.json()["summary"] == "One claim completed."
    assert current.json()["completed_at"]
    assert specific.json()["todos"][0]["status"] == "done"


def test_current_run_is_null_before_the_first_run(client):
    response = client.get("/api/runs/current")

    assert response.status_code == 200
    assert response.json() is None
