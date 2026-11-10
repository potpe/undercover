import httpx
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from sqlalchemy import update

from agent import queue as queue_agent
from agent import service
from backend import main
from backend.database import claims_table, connect, run_todos_table
from backend.main import app
from backend.models import DecisionRequest
from backend.runs import save_plan
from backend.workflow import record_decision


class StubModel(GenericFakeChatModel):
    model_name: str = "openai/gpt-6-luna"

    def bind_tools(self, tools, **kwargs):
        return self


def call(call_id, name, args=None):
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args or {}, "id": call_id}],
    )


def settle(claim_id, thread_id):
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


def use_queue_stub(client, monkeypatch, plan_ids, work_ids, early_finish=False):
    left_for_handler = len(work_ids) - len(set(work_ids))
    done = len(plan_ids) - left_for_handler
    summary = (
        f"{len(plan_ids)} claims handled: {done} done, "
        f"{left_for_handler} left for handler."
    )
    messages = [
        call("list", "list_claims"),
        call("plan", "plan_todos", {"claim_ids": plan_ids}),
    ]
    for index, claim_id in enumerate(work_ids):
        if early_finish and index == 1:
            messages.append(call("early", "finish_run", {"summary": "Too early"}))
        messages.append(call(f"work-{index}", "work_claim", {"claim_id": claim_id}))
    messages.extend(
        [
            call("check", "check_todos"),
            call(
                "finish",
                "finish_run",
                {"summary": summary},
            ),
            AIMessage(content="Run complete"),
        ]
    )
    monkeypatch.setattr(queue_agent, "backend", lambda: TestClient(app))
    monkeypatch.setattr(
        queue_agent, "model", lambda: StubModel(messages=iter(messages))
    )
    monkeypatch.setattr(main, "run_claim_via_service", settle)

    def start_queue(run_id):
        thread_id = client.get(f"/api/runs/{run_id}").json()["thread_id"]
        queue_agent.run(run_id, thread_id)

    monkeypatch.setattr(main, "notify_agent_run", start_queue)


def test_list_claims_exposes_the_run_cap_and_target(client, monkeypatch):
    created = client.post("/api/runs", json={"thread_id": "cap", "claim_cap": 5})
    run_id = created.json()["run_id"]
    monkeypatch.setattr(queue_agent, "backend", lambda: TestClient(app))

    result = queue_agent.queue_tools(run_id, "stub-model")[0].invoke({})

    assert result["claim_cap"] == 5
    assert result["target_claim_id"] is None
    assert len(result["claims"]) == 25


def test_queue_agent_works_five_in_its_chosen_order(client, monkeypatch):
    ids = ["CLM-1005", "CLM-1002", "CLM-1004", "CLM-1001", "CLM-1003"]
    use_queue_stub(client, monkeypatch, ids, ids)

    response = client.post("/api/runs", json={"thread_id": "five", "claim_cap": 5})

    assert response.status_code == 202
    run = client.get("/api/runs/current").json()
    assert run["status"] == "completed"
    assert run["model_name"] == "openai/gpt-6-luna"
    assert run["summary"] == "5 claims handled: 5 done, 0 left for handler."
    assert [todo["claim_id"] for todo in run["todos"]] == ids
    assert {todo["status"] for todo in run["todos"]} == {"done"}
    claims = client.get("/api/claims").json()
    assert len([claim for claim in claims if claim["status"] == "waiting"]) == 20


def test_cap_25_processes_the_whole_queue(client, monkeypatch):
    ids = [f"CLM-{number}" for number in range(1025, 1000, -1)]
    use_queue_stub(client, monkeypatch, ids, ids)

    client.post("/api/runs", json={"thread_id": "all", "claim_cap": 25})

    run = client.get("/api/runs/current").json()
    assert run["status"] == "completed"
    assert len(run["todos"]) == 25
    assert [todo["claim_id"] for todo in run["todos"]] == ids
    assert all(
        claim["status"] == "decided" for claim in client.get("/api/claims").json()
    )


def test_named_run_uses_one_fresh_claim_item(client, monkeypatch):
    use_queue_stub(client, monkeypatch, ["CLM-1002"], ["CLM-1002"])

    response = client.post("/api/claims/CLM-1002/run", json={"thread_id": "named"})

    assert response.status_code == 202
    run = client.get("/api/runs/current").json()
    assert run["target_claim_id"] == "CLM-1002"
    assert len(run["todos"]) == 1
    assert run["todos"][0]["status"] == "done"
    assert client.get("/api/claims/CLM-1002").json()["claim"]["status"] == "decided"
    assert client.get("/api/claims/CLM-1001").json()["claim"]["status"] == "waiting"
    assert (
        client.post("/api/claims/CLM-1002/run", json={"thread_id": "again"}).status_code
        == 409
    )


def test_failed_claim_does_not_stop_the_remaining_claims(client, monkeypatch):
    ids = ["CLM-1001", "CLM-1002"]
    use_queue_stub(
        client,
        monkeypatch,
        ids,
        ["CLM-1001", "CLM-1001", "CLM-1002"],
    )
    calls = []

    def fail_first(claim_id, thread_id):
        calls.append(thread_id)
        if claim_id == "CLM-1001":
            raise RuntimeError("Provider unavailable")
        return settle(claim_id, thread_id)

    monkeypatch.setattr(main, "run_claim_via_service", fail_first)

    client.post("/api/runs", json={"thread_id": "mixed", "claim_cap": 2})

    run = client.get("/api/runs/current").json()
    assert run["status"] == "completed"
    assert run["summary"] == "2 claims handled: 1 done, 1 left for handler."
    assert run["todos"][0]["status"] == "left_for_handler"
    assert run["todos"][0]["attempts"] == 2
    assert run["todos"][1]["status"] == "done"
    assert client.get("/api/claims/CLM-1002").json()["claim"]["status"] == "decided"
    assert len(calls) == 3
    assert (
        client.post(
            "/api/claims/CLM-1001/run", json={"thread_id": "handler"}
        ).status_code
        == 409
    )


def test_failed_queue_run_releases_an_in_progress_claim(client, monkeypatch):
    def fail_queue(run_id, thread_id):
        save_plan(run_id, ["CLM-1001"], "stub-model")
        with connect() as connection:
            connection.execute(
                update(claims_table)
                .where(claims_table.c.claim_id == "CLM-1001")
                .values(status="in_progress")
            )
            connection.execute(
                update(run_todos_table)
                .where(run_todos_table.c.run_id == run_id)
                .values(status="in_progress")
            )
        raise RuntimeError("Queue provider failed")

    monkeypatch.setattr(service, "backend", lambda: TestClient(app))
    monkeypatch.setattr(service, "queue_run", fail_queue)
    monkeypatch.setattr(main, "notify_agent_run", service.run_stored_run)

    client.post("/api/runs", json={"thread_id": "failed", "claim_cap": 1})

    run = client.get("/api/runs/current").json()
    claim = client.get("/api/claims/CLM-1001").json()
    assert run["status"] == "failed"
    assert run["summary"] == "Queue provider failed"
    assert claim["claim"]["status"] == "waiting"
    assert run["todos"][0]["status"] == "to_do"


def test_finish_rejects_a_missed_claim_then_allows_completion(client, monkeypatch):
    ids = ["CLM-1001", "CLM-1002"]
    use_queue_stub(client, monkeypatch, ids, ids, early_finish=True)

    client.post("/api/runs", json={"thread_id": "missed", "claim_cap": 2})

    run = client.get("/api/runs/current").json()
    assert run["status"] == "completed"
    assert [todo["status"] for todo in run["todos"]] == ["done", "done"]
    waiting_outside_cap = client.get("/api/claims/CLM-1003").json()["claim"]["status"]
    assert waiting_outside_cap == "waiting"


def test_queue_agent_can_correct_a_rejected_plan(client, monkeypatch):
    messages = [
        call("list", "list_claims"),
        call("bad-plan", "plan_todos", {"claim_ids": ["CLM-1001", "CLM-1002"]}),
        call("good-plan", "plan_todos", {"claim_ids": ["CLM-1001"]}),
        call("work", "work_claim", {"claim_id": "CLM-1001"}),
        call("check", "check_todos"),
        call(
            "finish",
            "finish_run",
            {"summary": "1 claim handled: 1 done, 0 left for handler."},
        ),
        AIMessage(content="Run complete"),
    ]
    monkeypatch.setattr(queue_agent, "backend", lambda: TestClient(app))
    monkeypatch.setattr(
        queue_agent, "model", lambda: StubModel(messages=iter(messages))
    )
    monkeypatch.setattr(main, "run_claim_via_service", settle)

    def start_queue(run_id):
        thread_id = client.get(f"/api/runs/{run_id}").json()["thread_id"]
        queue_agent.run(run_id, thread_id)

    monkeypatch.setattr(main, "notify_agent_run", start_queue)

    client.post("/api/runs", json={"thread_id": "corrected", "claim_cap": 1})

    run = client.get("/api/runs/current").json()
    assert run["status"] == "completed"
    assert len(run["todos"]) == 1
    assert run["todos"][0]["status"] == "done"


def test_work_timeout_is_nonfatal_to_the_queue(monkeypatch):
    class TimeoutClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, path, **kwargs):
            raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(queue_agent, "backend", TimeoutClient)

    result = queue_agent.queue_tools(1, "stub-model")[2].invoke(
        {"claim_id": "CLM-1001"}
    )

    assert result["status"] == "in_progress"
    assert result["claim_id"] == "CLM-1001"
    assert "check_todos" in result["result"]


def test_duplicate_finish_does_not_change_the_run(client, monkeypatch):
    use_queue_stub(client, monkeypatch, ["CLM-1001"], ["CLM-1001"])
    created = client.post("/api/runs", json={"thread_id": "one", "claim_cap": 1})
    run_id = created.json()["run_id"]
    completed = client.get(f"/api/runs/{run_id}").json()

    duplicate = client.post(f"/api/runs/{run_id}/finish", json={"summary": "Duplicate"})

    assert duplicate.status_code == 409
    assert client.get(f"/api/runs/{run_id}").json() == completed
