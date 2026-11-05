import pytest

from backend import main
from backend.models import DecisionRequest, PaymentRequest
from backend.workflow import record_decision, record_payment


@pytest.fixture(autouse=True)
def defer_queue_agent(monkeypatch):
    monkeypatch.setattr(main, "execute_queue_run", lambda run_id, thread_id: None)


def planned_run(client, claim_id="CLM-1001"):
    run = client.post("/api/runs", json={"thread_id": "work", "claim_cap": 1}).json()
    client.post(
        f"/api/runs/{run['run_id']}/plan",
        json={"claim_ids": [claim_id], "model_name": "stub-model"},
    )
    return run["run_id"]


def work(client, run_id, claim_id="CLM-1001"):
    return client.post(f"/api/runs/{run_id}/work", json={"claim_id": claim_id})


def approve(claim_id, payout=420.0):
    record_decision(
        claim_id,
        DecisionRequest(
            decision="approve",
            payout=payout,
            reason="The loss is covered. The calculation produces this payout.",
            policy_section="Section 3: Trip Delay; Section 10: Payment",
        ),
    )


def test_work_completes_one_claim_and_repeats_without_another_agent(
    client, monkeypatch
):
    run_id = planned_run(client)
    calls = []

    def runner(claim_id, thread_id):
        calls.append(thread_id)
        approve(claim_id)
        record_payment(claim_id, PaymentRequest(amount=420.0))
        return "CLM-1001: approve $420.00"

    monkeypatch.setattr(main, "agent_run", runner)
    response = work(client, run_id)

    assert response.status_code == 200
    assert response.json()["status"] == "done"
    assert response.json()["result"] == "CLM-1001: approve $420.00"
    assert response.json()["attempts"] == 0
    assert client.get("/api/claims/CLM-1001").json()["claim"]["status"] == "decided"

    repeated = work(client, run_id)
    assert repeated.json()["result"] == "CLM-1001: approve $420.00"
    assert calls == ["1:CLM-1001:attempt-1"]


def test_over_limit_approval_is_done_but_unpaid(client, monkeypatch):
    run_id = planned_run(client, "CLM-1003")

    def runner(claim_id, thread_id):
        approve(claim_id, 3750.0)
        return "CLM-1003: approve $3,750.00; sign-off required"

    monkeypatch.setattr(main, "agent_run", runner)

    response = work(client, run_id, "CLM-1003")
    assert response.json()["status"] == "done"
    bundle = client.get("/api/claims/CLM-1003").json()
    assert bundle["claim"]["status"] == "waiting_for_sign_off"
    assert bundle["paid_amount"] is None
    assert (
        client.post("/api/claims/CLM-1003/payment", json={"amount": 3750.0}).status_code
        == 409
    )


def test_first_failure_returns_to_queue_and_fresh_retry_succeeds(client, monkeypatch):
    run_id = planned_run(client)
    threads = []

    def runner(claim_id, thread_id):
        threads.append(thread_id)
        if len(threads) == 1:
            raise RuntimeError("Provider timed out")
        record_decision(
            claim_id,
            DecisionRequest(
                decision="decline",
                payout=0,
                reason="The policy excludes this loss. The cited section applies.",
                policy_section="Section 9: General exclusions",
            ),
        )
        return "CLM-1001: decline $0.00"

    monkeypatch.setattr(main, "agent_run", runner)

    first = work(client, run_id)
    assert first.status_code == 200
    assert first.json()["status"] == "retry"
    assert first.json()["attempts"] == 1
    assert first.json()["result"] == "Provider timed out"
    first_bundle = client.get("/api/claims/CLM-1001").json()
    assert first_bundle["claim"]["status"] == "waiting"
    assert first_bundle["decision"] is None
    assert client.get(f"/api/runs/{run_id}").json()["todos"][0]["status"] == "to_do"

    second = work(client, run_id)
    assert second.json()["status"] == "done"
    assert threads == ["1:CLM-1001:attempt-1", "1:CLM-1001:attempt-2"]
    assert client.get("/api/claims/CLM-1001").json()["claim"]["status"] == "decided"


def test_two_failures_leave_claim_for_handler_without_third_attempt(
    client, monkeypatch
):
    run_id = planned_run(client)
    calls = []

    def runner(claim_id, thread_id):
        calls.append(thread_id)
        raise RuntimeError(f"Provider failure {len(calls)}")

    monkeypatch.setattr(main, "agent_run", runner)

    assert work(client, run_id).json()["status"] == "retry"
    second = work(client, run_id)
    assert second.json()["status"] == "left_for_handler"
    assert second.json()["attempts"] == 2
    assert second.json()["result"] == "Provider failure 2"
    assert client.get("/api/claims/CLM-1001").json()["claim"]["status"] == (
        "left_for_handler"
    )
    todo = client.get(f"/api/runs/{run_id}").json()["todos"][0]
    assert todo["status"] == "left_for_handler"
    assert todo["error"] == "Provider failure 2"

    third = work(client, run_id)
    assert third.json()["status"] == "left_for_handler"
    assert len(calls) == 2
    assert client.get("/api/claims/CLM-1001").json()["paid_amount"] is None


def test_failure_does_not_change_another_claim(client, monkeypatch):
    run = client.post("/api/runs", json={"thread_id": "work", "claim_cap": 2}).json()
    run_id = run["run_id"]
    client.post(
        f"/api/runs/{run_id}/plan",
        json={
            "claim_ids": ["CLM-1001", "CLM-1002"],
            "model_name": "stub-model",
        },
    )
    monkeypatch.setattr(
        main,
        "agent_run",
        lambda claim_id, thread_id: (_ for _ in ()).throw(RuntimeError("Offline")),
    )

    work(client, run_id)

    other = client.get("/api/claims/CLM-1002").json()
    assert other["claim"]["status"] == "waiting"
    assert other["activity"] == []
    todos = client.get(f"/api/runs/{run_id}").json()["todos"]
    assert todos[1]["status"] == "to_do"
    assert todos[1]["attempts"] == 0


def test_interruption_after_decision_recovers_payment_without_duplicate(
    client, monkeypatch
):
    run_id = planned_run(client)
    calls = []

    def runner(claim_id, thread_id):
        calls.append(thread_id)
        approve(claim_id)
        raise RuntimeError("Connection lost after decision")

    monkeypatch.setattr(main, "agent_run", runner)

    response = work(client, run_id)
    assert response.json()["status"] == "done"
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["claim"]["status"] == "decided"
    assert bundle["paid_amount"] == 420.0
    assert (
        len(
            [
                a
                for a in bundle["activity"]
                if a["message"] == "Payment recorded: $420.00"
            ]
        )
        == 1
    )

    work(client, run_id)
    assert len(calls) == 1


def test_normal_return_without_decision_is_a_retryable_failure(client, monkeypatch):
    run_id = planned_run(client)
    monkeypatch.setattr(main, "agent_run", lambda claim_id, thread_id: "No decision")

    response = work(client, run_id)

    assert response.json()["status"] == "retry"
    assert response.json()["result"] == "Agent finished without recording a decision"
    assert client.get("/api/claims/CLM-1001").json()["claim"]["status"] == "waiting"
