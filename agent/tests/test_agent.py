from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from agent import main
from backend.main import app
from backend.models import DecisionKind, DecisionRequest


class StructuredStub:
    def __init__(self, decision):
        self.decision = decision

    def invoke(self, messages):
        return self.decision


class StubModel:
    """A chat model that gives scripted replies and a structured decision."""

    def __init__(self, messages, model_name="stub-model", structured_decision=None):
        self.messages = iter(messages)
        self.model_name = model_name
        self.structured_decision = structured_decision

    def invoke(self, messages):
        return next(self.messages)

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema):
        return StructuredStub(self.structured_decision)


def use_backend(monkeypatch):
    monkeypatch.setattr(main, "backend", lambda: TestClient(app))


def tool_call(call_id, name, args):
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": call_id}]
    )


def expense(description, amount, kind="expense", **options):
    return {
        "description": description,
        "amount": amount,
        "quantity": options.get("quantity", 1),
        "kind": kind,
        "nights": options.get("nights", 0),
        "hours": options.get("hours", 0),
    }


def decision(payout, kind: DecisionKind = "approve"):
    return DecisionRequest(
        decision=kind,
        payout=payout,
        reason=(
            "The evidence supports this decision under the cited wording. "
            "The payout follows the backend calculation."
        ),
        policy_section="Section 3: Trip Delay; Section 10: Payment",
    )


def saved_state(model, thread_id):
    with main.claim_checkpointer() as checkpointer:
        graph = main.build_claim_graph(model, "Claim instructions", checkpointer)
        return graph.get_state({"configurable": {"thread_id": thread_id}})


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
    assert not hasattr(main, "pay_claim")


def test_graph_approves_and_pays_a_small_claim(client, monkeypatch):
    use_backend(monkeypatch)
    replies = [
        tool_call("1", "read_claim", {"claim_id": "CLM-1001"}),
        tool_call(
            "2",
            "read_document_section",
            {
                "claim_id": "CLM-1001",
                "document": "policy_wording",
                "section_id": "3",
            },
        ),
        tool_call(
            "3",
            "calculate_payout",
            {
                "claim_id": "CLM-1001",
                "benefit": "trip_delay",
                "lines": [
                    expense("Hotel", 245, "hotel", nights=1),
                    expense("Meals", 95),
                    expense("Local transport", 80),
                ],
                "paid_by_others": 0.0,
            },
        ),
        AIMessage(content="The claim is covered."),
    ]
    monkeypatch.setattr(
        main,
        "model",
        lambda: StubModel(
            messages=iter(replies),
            model_name="openrouter/free",
            structured_decision=decision(420),
        ),
    )

    result = main.run("CLM-1001", "thread-1")

    assert result == "CLM-1001: approve $420.00; payment recorded"
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["claim"]["status"] == "decided"
    assert bundle["decision"]["payout"] == 420.0
    assert bundle["paid_amount"] == 420.0
    messages = [entry["message"] for entry in bundle["activity"]]
    assert messages[0] == "Using model openrouter/free"
    assert "Decision prepared: approve for $420.00" in messages
    assert messages[-2:] == [
        "Decision recorded: approve for $420.00",
        "Payment recorded: $420.00",
    ]

    with main.claim_checkpointer() as checkpointer:
        graph = main.build_claim_graph(
            StubModel(messages=iter([])), "Claim instructions", checkpointer
        )
        history = list(
            graph.get_state_history({"configurable": {"thread_id": "thread-1"}})
        )
        record_snapshot = next(
            snapshot for snapshot in history if snapshot.next == ("record_decision",)
        )
        pay_snapshot = next(
            snapshot for snapshot in history if snapshot.next == ("pay",)
        )
        graph.invoke(None, record_snapshot.config, durability="sync")
        graph.invoke(None, pay_snapshot.config, durability="sync")

    replayed = client.get("/api/claims/CLM-1001").json()
    assert replayed["paid_at"] == bundle["paid_at"]
    assert (
        len(
            [
                entry
                for entry in replayed["activity"]
                if entry["message"] == "Decision recorded: approve for $420.00"
            ]
        )
        == 1
    )
    assert (
        len(
            [
                entry
                for entry in replayed["activity"]
                if entry["message"] == "Payment recorded: $420.00"
            ]
        )
        == 1
    )


def test_graph_interrupts_and_resumes_an_over_limit_claim(client, monkeypatch):
    use_backend(monkeypatch)
    replies = [
        tool_call("1", "read_claim", {"claim_id": "CLM-1003"}),
        tool_call(
            "2",
            "calculate_payout",
            {
                "claim_id": "CLM-1003",
                "benefit": "emergency_medical",
                "lines": [expense("Hospital bill", 3800)],
                "paid_by_others": 0.0,
            },
        ),
        AIMessage(content="Emergency treatment is covered."),
    ]

    def stub_model():
        return StubModel(messages=iter(replies), structured_decision=decision(3750))

    monkeypatch.setattr(main, "model", stub_model)

    held = main.run("CLM-1003", "thread-held")

    assert held == "CLM-1003: approve $3,750.00; waiting for sign-off"
    bundle = client.get("/api/claims/CLM-1003").json()
    assert bundle["claim"]["status"] == "waiting_for_sign_off"
    assert bundle["paid_amount"] is None
    state = saved_state(stub_model(), "thread-held")
    assert "approval" in state.next
    assert state.interrupts[0].value["payout"] == 3750

    client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    paid = main.run("CLM-1003", "thread-held")

    assert paid == "CLM-1003: approve $3,750.00; payment recorded"
    bundle = client.get("/api/claims/CLM-1003").json()
    assert bundle["claim"]["status"] == "decided"
    assert bundle["paid_amount"] == 3750
    assert bundle["signed_off_by"] == "HND-01"
    assert bundle["signed_off_at"] is not None
    assert bundle["paid_at"] is not None
    decisions = [
        entry
        for entry in bundle["activity"]
        if entry["message"] == "Decision recorded: approve for $3,750.00"
    ]
    payments = [
        entry
        for entry in bundle["activity"]
        if entry["message"] == "Payment recorded: $3,750.00"
    ]
    assert len(decisions) == 1
    assert len(payments) == 1

    repeated = main.run("CLM-1003", "thread-held")
    assert repeated == paid
    assert client.get("/api/claims/CLM-1003").json() == bundle


def test_signoff_at_new_interrupt_resumes_payment_in_the_same_run(client, monkeypatch):
    use_backend(monkeypatch)
    calls = []

    class FakeGraph:
        def get_state(self, config):
            return SimpleNamespace(
                created_at="2026-11-10T00:00:00Z",
                next=("approval",),
                interrupts=(),
                values={"result": "waiting"},
            )

        def invoke(self, inputs, config, durability):
            calls.append(inputs)
            if len(calls) == 1:
                return {"__interrupt__": [object()], "result": "waiting"}
            assert inputs.resume == {"handler_id": "HND-01"}
            return {"result": "CLM-1003: approve $3,750.00; payment recorded"}

    graph = FakeGraph()
    monkeypatch.setattr(main, "model", lambda: StubModel(messages=iter([])))
    monkeypatch.setattr(main, "build_claim_graph", lambda *args: graph)
    monkeypatch.setattr(
        main, "current_bundle", lambda claim_id: {"signed_off_by": "HND-01"}
    )

    result = main.run("CLM-1003", "approval-boundary")

    assert result == "CLM-1003: approve $3,750.00; payment recorded"
    assert len(calls) == 2
    assert calls[0] is None


def test_tool_refusal_returns_to_the_model(client, monkeypatch):
    use_backend(monkeypatch)
    replies = [
        tool_call(
            "1",
            "calculate_payout",
            {
                "claim_id": "CLM-1001",
                "benefit": "trip_delay",
                "lines": [expense("Hotel", 245, "hotel", quantity=0)],
                "paid_by_others": 0.0,
            },
        ),
        AIMessage(content="The evidence does not support an approval."),
    ]
    monkeypatch.setattr(
        main,
        "model",
        lambda: StubModel(
            messages=iter(replies), structured_decision=decision(0, "decline")
        ),
    )

    result = main.run("CLM-1001", "refusal")

    assert result == "CLM-1001: decline $0.00"
    assert client.get("/api/claims/CLM-1001").json()["decision"]["decision"] == (
        "decline"
    )


def test_graph_stops_at_30_model_calls(client, monkeypatch):
    use_backend(monkeypatch)
    replies = [
        tool_call(str(index), "read_claim", {"claim_id": "CLM-1001"})
        for index in range(30)
    ]
    monkeypatch.setattr(
        main,
        "model",
        lambda: StubModel(messages=iter(replies), structured_decision=decision(420)),
    )

    main.run("CLM-1001", "limited")

    assert client.get("/api/claims/CLM-1001").json()["decision"] is None
    state = saved_state(StubModel(messages=iter([])), "limited")
    assert len(state.values["messages"]) == 61


def test_each_claim_thread_has_only_its_own_message(client, monkeypatch):
    use_backend(monkeypatch)
    for claim_id in ("CLM-1001", "CLM-1002"):
        monkeypatch.setattr(
            main,
            "model",
            lambda: StubModel(
                messages=iter([AIMessage(content="Not covered")]),
                structured_decision=decision(0, "decline"),
            ),
        )
        main.run(claim_id, f"fresh-{claim_id}")

    first = saved_state(StubModel(messages=iter([])), "fresh-CLM-1001")
    second = saved_state(StubModel(messages=iter([])), "fresh-CLM-1002")
    first_messages = first.values["messages"]
    second_messages = second.values["messages"]
    assert first_messages[0].content == "Work claim CLM-1001"
    assert second_messages[0].content == "Work claim CLM-1002"
    assert all("CLM-1002" not in message.content for message in first_messages)


def test_model_name(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.delenv("MODEL_NAME", raising=False)
    assert main.model().model_name == "deepseek/deepseek-v4.1-flash"
    assert main.model().reasoning_effort == "low"
    assert main.model().model_dump()["max_tokens"] == 8192
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    assert main.model().model_name == "openrouter/free"
    assert main.model().reasoning_effort is None
