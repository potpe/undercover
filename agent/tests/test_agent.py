from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from agent import main
from backend.main import app


class StubModel(GenericFakeChatModel):
    """A chat model that gives scripted replies in place of a real model."""

    model_name: str = "stub-model"

    def bind_tools(self, tools, **kwargs):
        return self


def use_backend(monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))


def tool_call(call_id, name, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


def test_claim_tools_use_section_level_backend_apis(client, monkeypatch):
    use_backend(monkeypatch)

    bundle = main.read_claim.invoke({"claim_id": "CLM-1001"})
    found = main.search_documents.invoke(
        {
            "claim_id": "CLM-1001",
            "document": "claims_manual",
            "query": "meals without receipts",
        }
    )
    section = main.read_document_section.invoke(
        {"claim_id": "CLM-1001", "document": "claims_manual", "section_id": "10"}
    )

    assert bundle["claim"]["claim_id"] == "CLM-1001"
    assert bundle["policy"]["policy_id"] == "POL-2001"
    assert found[0]["section_id"] == "10"
    assert "$35 per traveler" in section["content"]
    assert not hasattr(main, "read_policy_wording")


def test_run_approves_and_pays_a_small_claim(client, monkeypatch):
    use_backend(monkeypatch)
    replies = [
        tool_call("1", "read_claim", {"claim_id": "CLM-1001"}),
        tool_call(
            "2",
            "search_documents",
            {
                "claim_id": "CLM-1001",
                "document": "policy_wording",
                "query": "trip delay",
            },
        ),
        tool_call(
            "3",
            "read_document_section",
            {
                "claim_id": "CLM-1001",
                "document": "policy_wording",
                "section_id": "3",
            },
        ),
        tool_call(
            "4",
            "read_document_section",
            {
                "claim_id": "CLM-1001",
                "document": "claims_manual",
                "section_id": "4",
            },
        ),
        tool_call(
            "5",
            "record_claim_decision",
            {
                "claim_id": "CLM-1001",
                "decision": "approve",
                "payout": 420.0,
                "reason": (
                    "The airline canceled the flight and the delay exceeds Premium's "
                    "waiting period. The documented meals, hotel and local transport "
                    "are eligible extra costs."
                ),
                "policy_section": (
                    "Section 3: Trip Delay; Section 10: How we work out your payment"
                ),
            },
        ),
        tool_call("6", "pay_claim", {"claim_id": "CLM-1001", "amount": 420.0}),
        AIMessage(content="Claim CLM-1001 approved for $420.00."),
    ]
    monkeypatch.setattr(
        main,
        "model",
        lambda: StubModel(messages=iter(replies), model_name="openrouter/free"),
    )

    result = main.run("CLM-1001", "thread-1")

    assert result == "Claim CLM-1001 approved for $420.00."
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["claim"]["status"] == "decided"
    assert bundle["decision"]["payout"] == 420.0
    assert bundle["paid_amount"] == 420.0
    assert [entry["message"] for entry in bundle["activity"]] == [
        "Using model openrouter/free",
        "Read claim, policy and customer claim history",
        "Read Policy wording section 3: Trip Delay",
        "Read Claims manual section 4: Working a claim",
        "Decision recorded: approve for $420.00",
        "Payment recorded: $420.00",
    ]
    assert [read["section_id"] for read in bundle["documents_read"]] == ["3", "4"]


def test_run_leaves_an_over_limit_approval_for_sign_off(client, monkeypatch):
    use_backend(monkeypatch)
    replies = [
        tool_call("1", "read_claim", {"claim_id": "CLM-1003"}),
        tool_call(
            "2",
            "read_document_section",
            {
                "claim_id": "CLM-1003",
                "document": "policy_wording",
                "section_id": "7",
            },
        ),
        tool_call(
            "3",
            "read_document_section",
            {
                "claim_id": "CLM-1003",
                "document": "claims_manual",
                "section_id": "6",
            },
        ),
        tool_call(
            "4",
            "record_claim_decision",
            {
                "claim_id": "CLM-1003",
                "decision": "approve",
                "payout": 3750.0,
                "reason": (
                    "The sudden wrist fracture required emergency treatment during "
                    "the covered trip. The $3,800.00 bill less the $50.00 Premium "
                    "deductible gives a payout of $3,750.00."
                ),
                "policy_section": (
                    "Section 7: Emergency Medical; "
                    "Section 10: How we work out your payment"
                ),
            },
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


def test_each_claim_run_starts_with_only_its_own_message(client, monkeypatch):
    use_backend(monkeypatch)
    invocations = []

    class FreshAgent:
        def invoke(self, inputs, config):
            invocations.append(inputs["messages"])
            return {"messages": [AIMessage(content="Done")]}

    monkeypatch.setattr(main, "model", lambda: StubModel(messages=iter([])))
    monkeypatch.setattr(main, "create_agent", lambda *args, **kwargs: FreshAgent())

    main.run("CLM-1001", "fresh-1")
    main.run("CLM-1002", "fresh-2")

    assert invocations == [
        [{"role": "user", "content": "Work claim CLM-1001"}],
        [{"role": "user", "content": "Work claim CLM-1002"}],
    ]


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert main.model().model_name == "deepseek/deepseek-v4.1-flash"
    assert main.model().reasoning_effort == "low"
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert main.model().model_name == "openrouter/free"
    assert main.model().reasoning_effort is None
