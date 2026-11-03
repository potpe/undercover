from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent import main
from backend.main import app


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies in place of a real model."""

    def bind_tools(self, tools, **kwargs):
        return self


def use_backend(monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))


def test_read_claim_and_policy_wording_use_the_backend(client, monkeypatch):
    use_backend(monkeypatch)

    bundle = main.read_claim.invoke({"claim_id": "CLM-1001"})
    wording = main.read_policy_wording.invoke({"claim_id": "CLM-1001"})

    assert bundle["claim"]["claim_id"] == "CLM-1001"
    assert bundle["policy"]["policy_id"] == "POL-2001"
    assert wording["version"] == "2026.1"
    messages = [
        entry["message"]
        for entry in client.get("/api/claims/CLM-1001").json()["activity"]
    ]
    assert messages == [
        "Read claim, policy and customer claim history",
        "Read policy wording 2026.1",
    ]


def test_run_approves_and_pays_a_small_claim(client, monkeypatch):
    use_backend(monkeypatch)
    replies = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "read_claim", "args": {"claim_id": "CLM-1001"}, "id": "1"}
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_policy_wording",
                    "args": {"claim_id": "CLM-1001"},
                    "id": "2",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_claim_decision",
                    "args": {
                        "claim_id": "CLM-1001",
                        "decision": "approve",
                        "payout": 420.0,
                        "reason": (
                            "The airline canceled the flight and the delay exceeds "
                            "Premium's waiting period. The documented meals, hotel and "
                            "local transport are eligible extra costs."
                        ),
                        "policy_section": (
                            "Section 3: Trip Delay; "
                            "Section 10: How we work out your payment"
                        ),
                    },
                    "id": "3",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "pay_claim",
                    "args": {"claim_id": "CLM-1001", "amount": 420.0},
                    "id": "4",
                }
            ],
        ),
        AIMessage(content="Claim CLM-1001 approved for $420.00."),
    ]
    monkeypatch.setattr(main, "model", lambda: StubModel(messages=iter(replies)))

    result = main.run("CLM-1001", "thread-1")

    assert result == "Claim CLM-1001 approved for $420.00."
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["claim"]["status"] == "decided"
    assert bundle["decision"]["payout"] == 420.0
    assert bundle["paid_amount"] == 420.0
    assert [entry["message"] for entry in bundle["activity"]] == [
        "Read claim, policy and customer claim history",
        "Read policy wording 2026.1",
        "Decision recorded: approve for $420.00",
        "Payment recorded: $420.00",
    ]


def test_run_leaves_an_over_limit_approval_for_sign_off(client, monkeypatch):
    use_backend(monkeypatch)
    replies = [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "read_claim", "args": {"claim_id": "CLM-1003"}, "id": "1"}
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "read_policy_wording",
                    "args": {"claim_id": "CLM-1003"},
                    "id": "2",
                }
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "record_claim_decision",
                    "args": {
                        "claim_id": "CLM-1003",
                        "decision": "approve",
                        "payout": 3750.0,
                        "reason": (
                            "The sudden wrist fracture required emergency treatment "
                            "during the covered trip. The $3,800.00 bill less the "
                            "$50.00 Premium deductible gives a payout of $3,750.00."
                        ),
                        "policy_section": (
                            "Section 7: Emergency Medical; "
                            "Section 10: How we work out your payment"
                        ),
                    },
                    "id": "3",
                }
            ],
        ),
        AIMessage(content="Claim CLM-1003 approved pending handler sign-off."),
    ]
    monkeypatch.setattr(main, "model", lambda: StubModel(messages=iter(replies)))

    result = main.run("CLM-1003", "thread-2")

    assert result == "Claim CLM-1003 approved pending handler sign-off."
    bundle = client.get("/api/claims/CLM-1003").json()
    assert bundle["claim"]["status"] == "waiting_for_sign_off"
    assert bundle["decision"]["payout"] == 3750.0
    assert bundle["paid_amount"] is None
    blocked = client.post("/api/claims/CLM-1003/payment", json={"amount": 3750.0})
    assert blocked.status_code == 409


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert main.model().model_name == "openai/gpt-6-luna"
    assert main.model().reasoning_effort == "low"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert main.model().model_name == "openrouter/free"
    assert main.model().reasoning_effort is None
