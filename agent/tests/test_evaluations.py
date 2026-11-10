import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import dispatcher, main, service
from backend import main as backend_main
from backend.models import DecisionRequest


class StructuredStub:
    def __init__(self, payout):
        self.payout = payout

    def invoke(self, messages):
        return {
            "raw": AIMessage(content=""),
            "parsed": DecisionRequest(
                decision="approve",
                payout=self.payout,
                reason="The evidence supports approval under the cited policy wording.",
                policy_section="Section 3: Trip Delay; Section 10: Payment",
            ),
            "parsing_error": None,
        }


class StubModel:
    model_name = "stub-model"
    reasoning_effort = "low"

    def __init__(self, payout):
        self.payout = payout

    def invoke(self, messages):
        return AIMessage(content="The claim is covered")

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        return StructuredStub(self.payout)


@pytest.mark.parametrize("payout", [420, 3750])
def test_evaluation_uses_claim_graph_without_changing_queue_or_retaining_progress(
    client, monkeypatch, payout
):
    monkeypatch.setattr(main, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(service, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(dispatcher, "backend", lambda: TestClient(backend_main.app))
    monkeypatch.setattr(main, "model", lambda: StubModel(payout))
    before = client.get("/api/claims").json()
    created = client.post(
        "/api/internal/evaluations",
        json={
            "concurrency": 5,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "evaluation_set": "evaluation",
            "tiny": True,
        },
    )
    run_id = created.json()["run_id"]

    assert service.execute_stored_run(run_id) == "completed"

    assert client.get("/api/claims").json() == before
    run = client.get(f"/api/internal/runs/{run_id}").json()
    assert len(run["claims"]) == 5
    assert {claim["status"] for claim in run["claims"]} == {"done"}
    for claim in run["claims"]:
        bundle = client.get(f"/api/claims/{claim['claim_id']}").json()
        assert bundle["decision"]["payout"] == payout
        if payout == 3750:
            assert bundle["claim"]["status"] == "waiting_for_sign_off"
            assert bundle["paid_amount"] is None
        else:
            assert bundle["claim"]["status"] == "decided"
            assert bundle["paid_amount"] == 420
        with main.claim_checkpointer() as checkpointer:
            saved = checkpointer.get_tuple(
                {"configurable": {"thread_id": bundle["agent_thread_id"]}}
            )
        assert saved is None
