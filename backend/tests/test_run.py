import inspect

from agent.main import run
from backend import main
from backend.models import DecisionRequest, PaymentRequest
from backend.workflow import record_decision, record_payment


def test_agent_instructions_are_served_read_only(client):
    response = client.get("/api/agent/instructions")

    assert response.status_code == 200
    instructions = response.json()["content"]
    assert instructions.startswith("You are the UnderCover Agent.")
    assert "two or three sentences" in instructions
    assert "policy wording" in instructions
    assert "openai/gpt-6-luna" not in instructions


def test_run_endpoint_executes_the_agent(client, monkeypatch):
    def stub_agent(claim_id, thread_id):
        assert claim_id == "CLM-1001"
        assert thread_id == "thread-api"
        record_decision(
            claim_id,
            DecisionRequest(
                decision="approve",
                payout=420.0,
                reason="The delay is covered. The documented expenses are eligible.",
                policy_section="Section 3: Trip Delay",
            ),
        )
        record_payment(claim_id, PaymentRequest(amount=420.0))
        return "Done"

    monkeypatch.setattr(main, "agent_run", stub_agent)

    response = client.post("/api/claims/CLM-1001/run", json={"thread_id": "thread-api"})

    assert response.status_code == 202
    assert response.json() == {"claim_id": "CLM-1001", "status": "in_progress"}
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["claim"]["status"] == "decided"
    assert bundle["activity"][0]["message"] == "Agent run started"
    assert (
        client.post(
            "/api/claims/CLM-1001/run", json={"thread_id": "another-thread"}
        ).status_code
        == 409
    )


def test_duplicate_run_is_rejected_while_in_progress(client, monkeypatch):
    monkeypatch.setattr(main, "agent_run", lambda claim_id, thread_id: "Done")

    first = client.post("/api/claims/CLM-1001/run", json={"thread_id": "one"})
    second = client.post("/api/claims/CLM-1001/run", json={"thread_id": "two"})

    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json() == {"detail": "Claim is not waiting to run"}


def test_failed_run_returns_an_undecided_claim_to_waiting(client, monkeypatch):
    def fail(claim_id, thread_id):
        raise RuntimeError("Model unavailable")

    monkeypatch.setattr(main, "agent_run", fail)

    response = client.post("/api/claims/CLM-1001/run", json={"thread_id": "one"})

    assert response.status_code == 202
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["claim"]["status"] == "waiting"
    assert bundle["decision"] is None
    assert bundle["activity"][-1]["message"] == "Agent run failed: Model unavailable"


def test_failed_run_preserves_a_recorded_over_limit_decision(client, monkeypatch):
    def fail_after_decision(claim_id, thread_id):
        record_decision(
            claim_id,
            DecisionRequest(
                decision="approve",
                payout=3750.0,
                reason="Treatment was necessary. The deductible produces this payout.",
                policy_section="Section 7: Emergency Medical",
            ),
        )
        raise RuntimeError("Model stopped")

    monkeypatch.setattr(main, "agent_run", fail_after_decision)

    response = client.post("/api/claims/CLM-1003/run", json={"thread_id": "one"})

    assert response.status_code == 202
    bundle = client.get("/api/claims/CLM-1003").json()
    assert bundle["claim"]["status"] == "waiting_for_sign_off"
    assert bundle["decision"]["payout"] == 3750.0


def test_run_requires_a_thread_id(client):
    response = client.post("/api/claims/CLM-1001/run", json={})

    assert response.status_code == 422


def test_backend_run_entry_point_has_two_public_arguments():
    assert list(inspect.signature(run).parameters) == ["claim_id", "thread_id"]
