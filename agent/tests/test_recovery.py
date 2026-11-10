import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import update

from agent import main, queue, service
from backend import main as backend_main
from backend.database import claims_table, connect
from backend.models import DecisionRequest
from backend.runs import complete_work, finish_run, work_claim
from backend.workflow import record_decision


class StructuredStub:
    def __init__(self, result):
        self.result = result

    def invoke(self, messages):
        return {
            "raw": AIMessage(content=""),
            "parsed": self.result,
            "parsing_error": None,
        }


class StubModel:
    model_name = "stub-model"
    reasoning_effort = "low"

    def __init__(self, replies, result=None):
        self.replies = iter(replies)
        self.result = result

    def invoke(self, messages):
        reply = next(self.replies)
        if isinstance(reply, BaseException):
            raise reply
        return reply

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        return StructuredStub(self.result)


def tool_call(call_id, name, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


def payout_call():
    return tool_call(
        "payout",
        "calculate_payout",
        {
            "claim_id": "CLM-1001",
            "benefit": "trip_delay",
            "lines": [
                {
                    "description": "Hotel",
                    "amount": 245,
                    "quantity": 1,
                    "kind": "hotel",
                    "nights": 1,
                    "hours": 0,
                },
                {
                    "description": "Meals",
                    "amount": 95,
                    "quantity": 1,
                    "kind": "expense",
                    "nights": 0,
                    "hours": 0,
                },
                {
                    "description": "Taxis",
                    "amount": 80,
                    "quantity": 1,
                    "kind": "expense",
                    "nights": 0,
                    "hours": 0,
                },
            ],
            "paid_by_others": 0,
        },
    )


def approval():
    return DecisionRequest(
        decision="approve",
        payout=420,
        reason="The delay is covered. The backend calculation gives this payout.",
        policy_section="Section 3: Trip Delay; Section 10: Payment",
    )


class QueueStub(GenericFakeChatModel):
    model_name: str = "stub-queue"

    def bind_tools(self, tools, **kwargs):
        return self


def test_interrupted_claim_resumes_same_step_thread_and_attempt(client, monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(service, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(backend_main, "run_claim_via_service", main.run)
    created = client.post("/api/runs", json={"thread_id": "restart", "claim_cap": 1})
    run_id = created.json()["run_id"]
    client.post(
        f"/api/runs/{run_id}/plan",
        json={"claim_ids": ["CLM-1001"], "model_name": "stub-model"},
    )

    interrupted_model = StubModel(
        [
            tool_call("read", "read_claim", {"claim_id": "CLM-1001"}),
            KeyboardInterrupt(),
        ],
        approval(),
    )
    monkeypatch.setattr(main, "model", lambda: interrupted_model)
    with pytest.raises(KeyboardInterrupt):
        work_claim(run_id, "CLM-1001", main.run)

    stopped = client.get("/api/claims/CLM-1001").json()
    thread_id = stopped["agent_thread_id"]
    assert stopped["claim"]["status"] == "in_progress"
    assert stopped["decision"] is None
    assert client.get(f"/api/runs/{run_id}").json()["todos"][0]["attempts"] == 0

    resumed_model = StubModel(
        [payout_call(), AIMessage(content="The claim is covered")], approval()
    )
    monkeypatch.setattr(main, "model", lambda: resumed_model)
    result = service.resume_claim("CLM-1001")

    assert result == "CLM-1001: approve $420.00; payment recorded"
    resumed = client.get("/api/claims/CLM-1001").json()
    assert resumed["agent_thread_id"] == thread_id
    assert resumed["claim"]["status"] == "decided"
    assert resumed["paid_amount"] == 420
    messages = [entry["message"] for entry in resumed["activity"]]
    assert messages.count("Read claim, policy and customer claim history") == 1
    assert messages.count("Decision recorded: approve for $420.00") == 1
    assert messages.count("Payment recorded: $420.00") == 1
    assert client.get(f"/api/runs/{run_id}").json()["todos"][0]["attempts"] == 0


def test_stopped_queue_run_finishes_with_same_plan_and_no_duplicate_work(
    client, monkeypatch
):
    monkeypatch.setattr(main, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(service, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(queue, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(backend_main, "run_claim_via_service", main.run)
    created = client.post("/api/runs", json={"thread_id": "stopped", "claim_cap": 2})
    run_id = created.json()["run_id"]
    client.post(
        f"/api/runs/{run_id}/plan",
        json={
            "claim_ids": ["CLM-1001", "CLM-1002"],
            "model_name": "stub-model",
        },
    )
    interrupted_model = StubModel(
        [
            tool_call("read", "read_claim", {"claim_id": "CLM-1001"}),
            KeyboardInterrupt(),
        ],
        approval(),
    )
    monkeypatch.setattr(main, "model", lambda: interrupted_model)
    with pytest.raises(KeyboardInterrupt):
        work_claim(run_id, "CLM-1001", main.run)

    resumed_model = StubModel(
        [payout_call(), AIMessage(content="The claim is covered")], approval()
    )
    monkeypatch.setattr(main, "model", lambda: resumed_model)

    def work_next(claim_id, thread_id):
        if claim_id == "CLM-1001":
            return main.run(claim_id, thread_id)
        record_decision(
            claim_id,
            DecisionRequest(
                decision="decline",
                payout=0,
                reason="The wording excludes this loss. The cited section applies.",
                policy_section="Section 9: General exclusions",
            ),
        )
        return f"{claim_id}: decline $0.00"

    monkeypatch.setattr(backend_main, "run_claim_via_service", work_next)
    queue_messages = [
        tool_call("check", "check_todos", {}),
        tool_call("work-1", "work_claim", {"claim_id": "CLM-1001"}),
        tool_call("work-2", "work_claim", {"claim_id": "CLM-1002"}),
        tool_call("check-again", "check_todos", {}),
        tool_call(
            "finish",
            "finish_run",
            {"summary": "2 claims handled: 2 done, 0 left for handler."},
        ),
        AIMessage(content="Run complete"),
    ]
    monkeypatch.setattr(
        queue, "model", lambda: QueueStub(messages=iter(queue_messages))
    )
    monkeypatch.setattr(backend_main, "notify_agent_run", service.run_stored_run)

    resumed = client.post("/api/runs", json={"thread_id": "next", "claim_cap": 5})

    assert resumed.json()["run_id"] == run_id
    run = client.get(f"/api/runs/{run_id}").json()
    assert run["thread_id"] == "stopped"
    assert run["claim_cap"] == 2
    assert run["status"] == "completed", (run["summary"], run["todos"])
    assert [todo["status"] for todo in run["todos"]] == ["done", "done"]
    assert [todo["attempts"] for todo in run["todos"]] == [0, 0]
    first = client.get("/api/claims/CLM-1001").json()
    messages = [entry["message"] for entry in first["activity"]]
    assert messages.count("Read claim, policy and customer claim history") == 1
    assert messages.count("Decision recorded: approve for $420.00") == 1
    assert messages.count("Payment recorded: $420.00") == 1
    assert (
        len(
            [
                claim
                for claim in client.get("/api/claims").json()
                if claim["status"] == "waiting"
            ]
        )
        == 23
    )


def test_signoff_saved_while_agent_is_down_is_recovered(client, monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(service, "backend", lambda: TestClient(backend_main.app))
    replies = [
        tool_call("read", "read_claim", {"claim_id": "CLM-1003"}),
        tool_call(
            "payout",
            "calculate_payout",
            {
                "claim_id": "CLM-1003",
                "benefit": "emergency_medical",
                "lines": [
                    {
                        "description": "Hospital bill",
                        "amount": 3800,
                        "quantity": 1,
                        "kind": "expense",
                        "nights": 0,
                        "hours": 0,
                    }
                ],
                "paid_by_others": 0,
            },
        ),
        AIMessage(content="Emergency treatment is covered"),
    ]
    result = DecisionRequest(
        decision="approve",
        payout=3750,
        reason="The injury is covered. The deductible produces this payout.",
        policy_section="Section 7: Emergency Medical; Section 10: Payment",
    )
    monkeypatch.setattr(main, "model", lambda: StubModel(replies, result))
    held = main.run("CLM-1003", "offline-held")
    assert held.endswith("waiting for sign-off")
    with connect() as connection:
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == "CLM-1003")
            .values(agent_thread_id="offline-held")
        )

    signed = client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    assert signed.json()["claim"]["status"] == "in_progress"
    assert signed.json()["paid_amount"] is None

    service.recover_signed_off_claims()

    paid = client.get("/api/claims/CLM-1003").json()
    assert paid["claim"]["status"] == "decided"
    assert paid["paid_amount"] == 3750
    assert paid["agent_thread_id"] == "offline-held"
    activity = [entry["message"] for entry in paid["activity"]]
    assert activity.count("Decision recorded: approve for $3,750.00") == 1
    assert activity.count("Payment recorded: $3,750.00") == 1


def test_resume_failure_does_not_reopen_a_paid_claim(client, monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(service, "backend", lambda: TestClient(backend_main.app))
    created = client.post("/api/runs", json={"thread_id": "paid", "claim_cap": 1})
    run_id = created.json()["run_id"]
    client.post(
        f"/api/runs/{run_id}/plan",
        json={"claim_ids": ["CLM-1001"], "model_name": "stub-model"},
    )
    record_decision("CLM-1001", approval())
    main.save_payment("CLM-1001", 420)
    with connect() as connection:
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == "CLM-1001")
            .values(agent_thread_id="paid-thread")
        )
    complete_work(run_id, "CLM-1001", "CLM-1001: approve $420.00; payment recorded")
    finish_run(run_id, "1 claim done")
    original = client.get("/api/claims/CLM-1001").json()
    original_run = client.get(f"/api/runs/{run_id}").json()

    def fail_after_payment(claim_id, thread_id):
        raise RuntimeError("Checkpoint response lost after payment")

    monkeypatch.setattr(service, "claim_run", fail_after_payment)
    result = service.resume_claim("CLM-1001")

    assert result == "CLM-1001: approve $420.00; payment recorded"
    assert client.get("/api/claims/CLM-1001").json() == original
    assert client.get(f"/api/runs/{run_id}").json() == original_run


def test_startup_recovery_retries_until_backend_is_ready(monkeypatch):
    calls = []

    def recover_with_initial_outage():
        calls.append("recover")
        if len(calls) == 1:
            raise httpx.ConnectError("Backend not ready")

    monkeypatch.setattr(service, "recover_work", recover_with_initial_outage)
    monkeypatch.setattr("agent.service.time.sleep", lambda seconds: None)

    with TestClient(service.app):
        pass

    assert calls == ["recover", "recover"]


def test_service_startup_recovers_saved_work(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "recover_work", lambda: calls.append("recovered"))

    with TestClient(service.app):
        pass

    assert calls == ["recovered"]
