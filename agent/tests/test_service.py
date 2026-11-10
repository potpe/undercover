import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import BackgroundTasks
from fastapi.testclient import TestClient
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.messages import AIMessage
from sqlalchemy import update

from agent import dispatcher, main, service
from backend import agent_service
from backend import main as backend_main
from backend.database import claims_table, connect
from backend.models import DecisionRequest, DispatchRequest, PaymentRequest
from backend.workflow import record_decision, record_payment


def use_backend(monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(service, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(dispatcher, "backend", lambda: TestClient(backend_main.app))


def only_claims(*claim_ids):
    with connect() as connection:
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id.not_in(list(claim_ids)))
            .values(status="decided")
        )


def create_dispatch(client, concurrency=3):
    response = client.post(
        "/api/internal/dispatches",
        json={
            "concurrency": concurrency,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
        },
    )
    return response.json()["run_id"]


def approve(claim_id, payout=420):
    record_decision(
        claim_id,
        DecisionRequest(
            decision="approve",
            payout=payout,
            reason=(
                "The evidence supports approval and the backend calculated this payout."
            ),
            policy_section="Section 3: Trip Delay; Section 10: Payment",
        ),
    )
    record_payment(claim_id, PaymentRequest(amount=payout))


def test_same_claim_work_is_serialized(monkeypatch):
    active = 0
    maximum = 0

    def fake_claim_run(claim_id, thread_id, run_id, run_kind):
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


def test_service_openapi_is_documented(monkeypatch):
    monkeypatch.setattr(service, "recover_work", lambda: None)
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


def test_only_one_dispatch_or_evaluation_can_start(client, monkeypatch):
    use_backend(monkeypatch)

    first = service.start_dispatch(DispatchRequest(concurrency=3), BackgroundTasks())
    with pytest.raises(Exception, match="Another dispatch or evaluation is running"):
        service.start_evaluation(
            service.EvaluationRequest(
                concurrency=5, evaluation_set="evaluation", tiny=True
            ),
            BackgroundTasks(),
        )

    monkeypatch.setattr(service, "execute_stored_run", lambda run_id: "completed")
    service.run_in_background(first.run_id)
    client.post("/api/internal/runs/stop-active")
    second = service.start_dispatch(DispatchRequest(concurrency=1), BackgroundTasks())
    assert second.concurrency == 1

    def fail_run(run_id):
        raise RuntimeError("Dispatcher failed")

    monkeypatch.setattr(service, "execute_stored_run", fail_run)
    with pytest.raises(RuntimeError, match="Dispatcher failed"):
        service.run_in_background(second.run_id)
    client.post("/api/internal/runs/stop-active")
    monkeypatch.setattr(service, "execute_stored_run", lambda run_id: "completed")
    third = service.start_dispatch(DispatchRequest(concurrency=2), BackgroundTasks())
    service.run_in_background(third.run_id)
    assert third.concurrency == 2


def test_rate_limit_resumes_same_attempt_and_pays_once(client, monkeypatch):
    use_backend(monkeypatch)
    only_claims("CLM-1001")
    run_id = create_dispatch(client)
    threads = []

    def work(claim_id, thread_id, run_id, run_kind):
        threads.append(thread_id)
        if len(threads) == 1:
            raise ModelRateLimitError("429")
        approve(claim_id)
        return f"{claim_id}: approve $420.00; payment recorded"

    monkeypatch.setattr(service, "claim_run", work)
    monkeypatch.setattr("langgraph.pregel._retry.time.sleep", lambda seconds: None)

    assert service.execute_stored_run(run_id) == "completed"
    bundle = client.get("/api/claims/CLM-1001").json()
    assert len(set(threads)) == 1
    assert bundle["failed_attempts"] == 0
    assert bundle["paid_amount"] == 420
    activity = [entry["message"] for entry in bundle["activity"]]
    assert activity.count("Decision recorded: approve for $420.00") == 1
    assert activity.count("Payment recorded: $420.00") == 1


def test_exhausted_rate_limit_stays_in_progress_for_next_dispatch(client, monkeypatch):
    use_backend(monkeypatch)
    only_claims("CLM-1001")
    run_id = create_dispatch(client)

    def limited(claim_id, thread_id, run_id, run_kind):
        raise ModelRateLimitError("Still limited")

    monkeypatch.setattr(service, "claim_run", limited)
    monkeypatch.setattr("langgraph.pregel._retry.time.sleep", lambda seconds: None)
    assert service.execute_stored_run(run_id) == "completed"

    run = client.get(f"/api/internal/runs/{run_id}").json()
    held = client.get("/api/claims/CLM-1001").json()
    assert run["summary"]["in_progress"] == ["CLM-1001"]
    assert held["failed_attempts"] == 0
    assert held["claim"]["status"] == "in_progress"

    def recovered(claim_id, thread_id, run_id, run_kind):
        assert thread_id == held["agent_thread_id"]
        approve(claim_id)
        return f"{claim_id}: approve $420.00; payment recorded"

    monkeypatch.setattr(service, "claim_run", recovered)
    next_id = create_dispatch(client)
    assert service.execute_stored_run(next_id) == "completed"
    assert client.get("/api/claims/CLM-1001").json()["paid_amount"] == 420


def test_ordinary_error_retries_fresh_once_then_succeeds(client, monkeypatch):
    use_backend(monkeypatch)
    only_claims("CLM-1001")
    run_id = create_dispatch(client)
    threads = []

    def flaky(claim_id, thread_id, run_id, run_kind):
        threads.append(thread_id)
        if len(threads) == 1:
            raise RuntimeError("Invalid response")
        approve(claim_id)
        return f"{claim_id}: approve $420.00; payment recorded"

    monkeypatch.setattr(service, "claim_run", flaky)
    assert service.execute_stored_run(run_id) == "completed"

    bundle = client.get("/api/claims/CLM-1001").json()
    assert len(set(threads)) == 2
    assert bundle["failed_attempts"] == 1
    assert bundle["paid_amount"] == 420


def test_second_failure_leaves_one_claim_without_stopping_peer(client, monkeypatch):
    use_backend(monkeypatch)
    only_claims("CLM-1001", "CLM-1002")
    run_id = create_dispatch(client, concurrency=2)

    def work(claim_id, thread_id, run_id, run_kind):
        if claim_id == "CLM-1001":
            raise RuntimeError("Invalid response")
        record_decision(
            claim_id,
            DecisionRequest(
                decision="decline",
                payout=0,
                reason="The policy wording excludes this loss.",
                policy_section="Section 9: General exclusions",
            ),
        )
        return f"{claim_id}: decline $0.00"

    monkeypatch.setattr(service, "claim_run", work)
    assert service.execute_stored_run(run_id) == "completed"

    assert client.get("/api/claims/CLM-1001").json()["failed_attempts"] == 2
    assert client.get("/api/claims/CLM-1001").json()["claim"]["status"] == (
        "left_for_handler"
    )
    assert client.get("/api/claims/CLM-1002").json()["decision"]["decision"] == (
        "decline"
    )
    run = client.get(f"/api/internal/runs/{run_id}").json()
    assert run["summary"]["left_for_handler"] == ["CLM-1001"]
    assert run["summary"]["declined"] == 1


def test_dispatch_skips_claim_already_resuming_after_signoff(client, monkeypatch):
    use_backend(monkeypatch)
    client.post(
        "/api/claims/CLM-1003/decision",
        json={
            "decision": "approve",
            "payout": 3750,
            "reason": "The injury is covered. The deductible produces this payout.",
            "policy_section": "Section 7: Emergency Medical; Section 10: Payment",
        },
    )
    client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    only_claims("CLM-1003")
    run_id = create_dispatch(client)
    lock = service._claim_locks.setdefault("CLM-1003", service.Lock())
    lock.acquire()
    called = False

    def should_not_run(*args):
        nonlocal called
        called = True

    monkeypatch.setattr(service, "claim_run", should_not_run)
    try:
        assert service.execute_stored_run(run_id) == "completed"
    finally:
        lock.release()

    assert called is False
    run = client.get(f"/api/internal/runs/{run_id}").json()
    assert run["summary"]["in_progress"] == ["CLM-1003"]


def test_signoff_resumes_held_claim_through_service_and_pays_once(client, monkeypatch):
    use_backend(monkeypatch)
    only_claims("CLM-1003")
    run_id = create_dispatch(client)

    class StructuredStub:
        def invoke(self, messages):
            return {
                "raw": AIMessage(content=""),
                "parsed": DecisionRequest(
                    decision="approve",
                    payout=3750,
                    reason=(
                        "The injury is covered. The deductible produces this payout."
                    ),
                    policy_section="Section 7: Emergency Medical; Section 10: Payment",
                ),
                "parsing_error": None,
            }

    class StubModel:
        model_name = "stub-model"
        reasoning_effort = "low"

        def __init__(self):
            self.replies = iter(
                [
                    AIMessage(content="Emergency treatment is covered."),
                ]
            )

        def invoke(self, messages):
            return next(self.replies)

        def bind_tools(self, tools, **kwargs):
            return self

        def with_structured_output(self, schema, **kwargs):
            return StructuredStub()

    monkeypatch.setattr(main, "model", StubModel)
    assert service.execute_stored_run(run_id) == "completed"
    held = client.get("/api/claims/CLM-1003").json()
    assert held["claim"]["status"] == "waiting_for_sign_off"
    assert held["paid_amount"] is None

    monkeypatch.setattr(agent_service, "notify_resume", service.resume_claim)
    signed = client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    assert signed.status_code == 200

    paid = client.get("/api/claims/CLM-1003").json()
    assert paid["claim"]["status"] == "decided"
    assert paid["paid_amount"] == 3750
    assert paid["agent_thread_id"] == held["agent_thread_id"]
    assert paid["failed_attempts"] == 0
    activity = [entry["message"] for entry in paid["activity"]]
    assert activity.count("Decision recorded: approve for $3,750.00") == 1
    assert activity.count("Payment recorded: $3,750.00") == 1


def test_service_startup_marks_old_run_stopped(client, monkeypatch):
    use_backend(monkeypatch)
    run_id = create_dispatch(client)

    with TestClient(service.app):
        pass

    assert client.get(f"/api/internal/runs/{run_id}").json()["status"] == "stopped"
