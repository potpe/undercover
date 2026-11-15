import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from sqlalchemy import update

from agent import api, direct, dispatcher, main, service, tools
from agent.tests.helpers import calculate_approval
from backend import main as backend_main
from backend.database import claims_table, connect
from backend.models import DecisionRequest, PaymentRequest
from backend.workflow import record_decision, record_payment


class StubModel:
    model_name = "stub-model"
    reasoning_effort = "low"

    def __init__(self, replies):
        self.replies = iter(replies)

    def invoke(self, messages):
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def bind_tools(self, tools, **kwargs):
        return self


def tool_call(call_id, name, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


def approval(payout=420):
    return DecisionRequest(
        decision="approve",
        payout=payout,
        reason="The delay is covered. The backend calculation gives this payout.",
        section="Section 3: Trip Delay; Section 10: Payment",
        decided_by="UnderCover Agent (configuration version 1)",
    )


def decision_call(payout=420):
    return tool_call(
        "decision",
        "record_decision",
        approval(payout).model_dump(exclude={"decided_by"}),
    )


def calculation_call(payout=420, deductible=50):
    return tool_call(
        "calculation",
        "calculate_payout",
        {
            "benefit": "emergency_medical",
            "lines": [
                {
                    "description": "Covered medical expense",
                    "amount": payout + deductible,
                    "quantity": 1,
                    "kind": "expense",
                    "nights": 0,
                    "hours": 0,
                }
            ],
            "paid_by_others": 0,
        },
    )


def use_backend(monkeypatch):
    def test_backend():
        return TestClient(
            backend_main.app,
            headers={"X-API-Key": "undercover-e2e-agent-key-32-characters"},
        )

    monkeypatch.setattr(main, "backend", test_backend)
    monkeypatch.setattr(api, "backend", test_backend)
    monkeypatch.setattr(direct, "backend", test_backend)
    monkeypatch.setattr(tools, "gateway_tools", lambda: [])
    monkeypatch.setattr(
        service,
        "backend",
        lambda: TestClient(
            backend_main.app,
            headers={
                "X-API-Key": "undercover-e2e-agent-key-32-characters",
            },
        ),
    )
    monkeypatch.setattr(
        dispatcher,
        "backend",
        lambda: TestClient(
            backend_main.app,
            headers={
                "X-API-Key": "undercover-e2e-agent-key-32-characters",
            },
        ),
    )


def create_one_claim_dispatch(client, claim_id):
    with connect() as connection:
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id != claim_id)
            .values(status="decided")
        )
    response = client.post(
        "/api/internal/dispatches",
        json={
            "concurrency": 3,
            "configuration_version": None,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "round_id": None,
            "round_purpose": None,
        },
    )
    return response.json()["run_id"]


def test_interrupted_claim_resumes_same_step_thread_and_attempt(client, monkeypatch):
    use_backend(monkeypatch)
    run_id = create_one_claim_dispatch(client, "CLM-1001")
    interrupted_model = StubModel(
        [
            tool_call("read", "get_claim", {}),
            KeyboardInterrupt(),
        ]
    )
    monkeypatch.setattr(main, "model", lambda _configuration: interrupted_model)

    with pytest.raises(KeyboardInterrupt):
        service.work_claim(
            {"claim_id": "CLM-1001", "run_id": run_id, "run_kind": "dispatch"}
        )

    stopped = client.get("/api/claims/CLM-1001").json()
    thread_id = stopped["agent_thread_id"]
    assert stopped["claim"]["status"] == "in_progress"
    assert stopped["failed_attempts"] == 0
    client.post("/api/internal/runs/stop-active")

    resumed_model = StubModel([calculation_call(), decision_call()])
    monkeypatch.setattr(main, "model", lambda _configuration: resumed_model)
    next_id = create_one_claim_dispatch(client, "CLM-1001")

    assert service.execute_stored_run(next_id) == "completed"
    resumed = client.get("/api/claims/CLM-1001").json()
    assert resumed["agent_thread_id"] == thread_id
    assert resumed["claim"]["status"] == "decided"
    assert resumed["paid_amount"] == 420
    assert resumed["failed_attempts"] == 0
    messages = [entry["message"] for entry in resumed["activity"]]
    assert messages.count("Read claim, policy and customer claim history") == 1
    assert messages.count("Decision recorded: approve for $420.00") == 1
    assert messages.count("Payment recorded: $420.00") == 1


def test_signoff_saved_while_agent_is_down_is_recovered(client, monkeypatch):
    use_backend(monkeypatch)
    run_id = create_one_claim_dispatch(client, "CLM-1003")
    stub = StubModel([calculation_call(3750), decision_call(3750)])
    monkeypatch.setattr(main, "model", lambda _configuration: stub)
    assert service.execute_stored_run(run_id) == "completed"

    signed = client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    assert signed.json()["claim"]["status"] == "decided"
    assert signed.json()["paid_amount"] == 3750

    monkeypatch.setattr(
        main,
        "save_payment",
        lambda *args: (_ for _ in ()).throw(
            AssertionError("Recovered sign-off must not record payment again")
        ),
    )
    with TestClient(service.app):
        pass

    paid = client.get("/api/claims/CLM-1003").json()
    assert paid["claim"]["status"] == "decided"
    assert paid["paid_amount"] == 3750
    assert paid["failed_attempts"] == 0
    activity = [entry["message"] for entry in paid["activity"]]
    assert activity.count("Decision recorded: approve for $3,750.00") == 1
    assert activity.count("Payment recorded: $3,750.00") == 1


def test_resume_failure_does_not_reopen_a_paid_claim(client, monkeypatch):
    use_backend(monkeypatch)
    run_id = create_one_claim_dispatch(client, "CLM-1001")
    started = client.post(f"/api/internal/runs/{run_id}/claims/CLM-1001/begin").json()
    calculate_approval(client, "CLM-1001", 420)
    record_decision("CLM-1001", approval())
    record_payment("CLM-1001", PaymentRequest(amount=420))
    client.post(
        f"/api/internal/runs/{run_id}/claims/CLM-1001/complete",
        json={"result": "CLM-1001: approve $420.00; payment recorded"},
    )
    client.post(f"/api/internal/runs/{run_id}/finish")
    original = client.get("/api/claims/CLM-1001").json()
    original_run = client.get(f"/api/internal/runs/{run_id}").json()

    def fail_after_payment(*args):
        raise RuntimeError("Checkpoint response lost after payment")

    monkeypatch.setattr(service, "claim_run", fail_after_payment)
    result = service.resume_claim("CLM-1001")

    assert result == "CLM-1001: approve $420.00; payment recorded"
    assert client.get("/api/claims/CLM-1001").json() == original
    assert client.get(f"/api/internal/runs/{run_id}").json() == original_run
    assert started["thread_id"] == original["agent_thread_id"]


def test_startup_recovery_retries_until_backend_is_ready(monkeypatch):
    calls = []
    monkeypatch.setenv("UNDERCOVER_API_KEY", "undercover-e2e-agent-key-32-characters")

    def recover_with_initial_outage():
        calls.append("recover")
        if len(calls) == 1:
            raise httpx.ConnectError("Backend not ready")

    monkeypatch.setattr(service, "recover_work", recover_with_initial_outage)
    monkeypatch.setattr("agent.service.time.sleep", lambda seconds: None)

    with TestClient(service.app):
        pass

    assert calls == ["recover", "recover"]
