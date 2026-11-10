import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import main, service
from backend import main as backend_main
from backend.models import DecisionRequest


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

    def __init__(self, replies, result):
        self.replies = iter(replies)
        self.result = result

    def invoke(self, messages):
        return next(self.replies)

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        return StructuredStub(self.result)


def tool_call(call_id, name, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


def test_same_claim_work_is_serialized(monkeypatch):
    active = 0
    maximum = 0

    def fake_claim_run(claim_id, thread_id):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        time.sleep(0.05)
        active -= 1
        return f"{claim_id}:{thread_id}"

    monkeypatch.setattr(service, "claim_run", fake_claim_run)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda index: service.run_claim_locked("CLM-1001", f"thread-{index}"),
                range(2),
            )
        )

    assert maximum == 1
    assert results == ["CLM-1001:thread-0", "CLM-1001:thread-1"]


def test_service_openapi_is_documented(client):
    schema = TestClient(service.app).get("/openapi.json").json()
    operation_ids = []
    for path_item in schema["paths"].values():
        for operation in path_item.values():
            operation_ids.append(operation["operationId"])
            assert operation["description"]
            body = operation.get("requestBody")
            if body:
                content = body["content"]["application/json"]["schema"]
                model_name = content["$ref"].rsplit("/", 1)[-1]
                model = schema["components"]["schemas"][model_name]
                assert set(model["required"]) == set(model["properties"])
    assert len(operation_ids) == len(set(operation_ids))


def test_signoff_resumes_held_claim_through_service_and_pays_once(client, monkeypatch):
    backend_client = TestClient(backend_main.app)
    service_client = TestClient(service.app)
    monkeypatch.setattr(main, "backend", lambda: backend_client)
    monkeypatch.setattr(service, "backend", lambda: backend_client)

    replies = [
        tool_call("1", "read_claim", {"claim_id": "CLM-1003"}),
        tool_call(
            "2",
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
        AIMessage(content="The emergency treatment is covered."),
    ]
    result = DecisionRequest(
        decision="approve",
        payout=3750,
        reason="The injury is covered. The bill less the deductible gives this payout.",
        policy_section="Section 7: Emergency Medical; Section 10: Payment",
    )
    monkeypatch.setattr(main, "model", lambda: StubModel(replies, result))

    created = client.post("/api/runs", json={"thread_id": "service", "claim_cap": 1})
    run_id = created.json()["run_id"]
    client.post(
        f"/api/runs/{run_id}/plan",
        json={"claim_ids": ["CLM-1003"], "model_name": "stub-model"},
    )

    def work_via_service(claim_id, thread_id, work_run_id=None):
        response = service_client.post(
            f"/claims/{claim_id}/work",
            json={
                "thread_id": thread_id,
                "run_id": run_id if work_run_id is None else work_run_id,
            },
        )
        response.raise_for_status()
        return response.json()["result"]

    monkeypatch.setattr(backend_main, "run_claim_via_service", work_via_service)
    worked = client.post(f"/api/runs/{run_id}/work", json={"claim_id": "CLM-1003"})
    assert worked.json()["status"] == "done", worked.json()["result"]
    held = client.get("/api/claims/CLM-1003").json()
    thread_id = held["agent_thread_id"]
    assert held["claim"]["status"] == "waiting_for_sign_off"
    assert held["paid_amount"] is None

    def notify_resume(claim_id):
        response = service_client.post(f"/claims/{claim_id}/resume")
        response.raise_for_status()

    monkeypatch.setattr(backend_main, "notify_agent_resume", notify_resume)
    signed = client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    assert signed.status_code == 200

    paid = client.get("/api/claims/CLM-1003").json()
    assert paid["claim"]["status"] == "decided"
    assert paid["paid_amount"] == 3750
    assert paid["signed_off_by_name"] == "Dana Whitfield"
    assert paid["signed_off_at"] is not None
    assert paid["paid_at"] is not None
    assert paid["agent_thread_id"] == thread_id
    assert client.get(f"/api/runs/{run_id}").json()["todos"][0]["attempts"] == 0
    activity = [entry["message"] for entry in paid["activity"]]
    assert activity.count("Decision recorded: approve for $3,750.00") == 1
    assert activity.count("Payment recorded: $3,750.00") == 1

    service_client.post("/claims/CLM-1003/resume")
    assert client.get("/api/claims/CLM-1003").json() == paid
