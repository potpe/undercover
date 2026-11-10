import inspect

from agent.main import run
from backend import agent_service
from backend.workflow import start_run


def test_agent_instructions_are_served_read_only(client):
    response = client.get("/api/agent/instructions")

    assert response.status_code == 200
    assert set(response.json()) == {"claim"}
    instructions = response.json()["claim"]
    assert instructions.startswith("You are the UnderCover claim agent.")
    assert "two or three sentences" in instructions
    assert "policy wording" in instructions
    assert "openai/gpt-6-luna" not in instructions


def test_named_run_schedules_only_that_claim(client, monkeypatch):
    calls = []

    def schedule_spy(claim_id, thread_id):
        calls.append((claim_id, thread_id))

    monkeypatch.setattr(agent_service, "work_named_claim", schedule_spy)

    response = client.post("/api/claims/CLM-1001/run", json={"thread_id": "thread-api"})

    assert response.status_code == 202
    assert response.json() == {"claim_id": "CLM-1001", "status": "in_progress"}
    assert calls == [("CLM-1001", "thread-api")]
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["agent_thread_id"] == "thread-api"
    assert bundle["work_run_id"] is None


def test_duplicate_named_run_is_rejected_while_in_progress(client):
    start_run("CLM-1001")

    second = client.post("/api/claims/CLM-1001/run", json={"thread_id": "two"})

    assert second.status_code == 409
    assert second.json() == {"detail": "Claim is not waiting to run"}


def test_run_requires_a_thread_id(client):
    response = client.post("/api/claims/CLM-1001/run", json={})

    assert response.status_code == 422


def test_claim_agent_entry_point_has_two_public_arguments():
    assert list(inspect.signature(run).parameters) == ["claim_id", "thread_id"]
