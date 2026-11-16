import json
from collections.abc import Mapping
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, ToolCall, ToolMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from pydantic import BaseModel

from agent import api, direct, gateway, main, tools
from agent.claim_context import ClaimContext
from agent.tests.helpers import calculate_approval
from backend.database import connect, settings_table
from backend.main import app


class ScriptedModel:
    model_name = "memory-test-model"
    reasoning_effort = "low"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.invoke_calls = 0
        self.inputs = []

    def bind_tools(self, model_tools, **kwargs):
        self.model_tools = model_tools
        return self

    def invoke(self, messages):
        self.invoke_calls += 1
        self.inputs.append(messages)
        return next(self.responses)


def model_tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": arguments, "id": call_id}],
    )


def use_backend(monkeypatch):
    def test_backend():
        return TestClient(
            app,
            headers={"X-API-Key": "undercover-e2e-agent-key-32-characters"},
        )

    monkeypatch.setattr(main, "backend", test_backend)
    monkeypatch.setattr(api, "backend", test_backend)
    monkeypatch.setattr(direct, "backend", test_backend)
    monkeypatch.setattr(tools, "gateway_tools", lambda: [])


def context_for(run_kind="dispatch"):
    return ClaimContext(
        claim_id="CLM-1001",
        policy_id="POL-2001",
        customer_id="CUS-3001",
        configuration_version=4,
        run_kind=run_kind,
    )


def approval_responses():
    return [
        model_tool_call(
            "calculate",
            "calculate_payout",
            {
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
                        "description": "Local transport",
                        "amount": 80,
                        "quantity": 1,
                        "kind": "expense",
                        "nights": 0,
                        "hours": 0,
                    },
                ],
                "paid_by_others": 0,
            },
        ),
        model_tool_call(
            "record",
            "record_decision",
            {
                "decision": "approve",
                "payout": 420,
                "reason": "The delay is covered and the calculation supports payment.",
                "section": "Section 3: Trip Delay; Section 10: Payment",
            },
        ),
    ]


def held_approval_responses():
    return [
        model_tool_call(
            "calculate",
            "calculate_payout",
            {
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
        model_tool_call(
            "record",
            "record_decision",
            {
                "decision": "approve",
                "payout": 3750,
                "reason": "The medical expense is covered.",
                "section": "Section 7: Emergency Medical; Section 10: Payment",
            },
        ),
    ]


def test_memory_tool_is_optional_strict_and_always_direct(monkeypatch):
    store = InMemoryStore()
    calls = []
    monkeypatch.setattr(tools, "log_step", lambda *_args: None)
    monkeypatch.setattr(
        tools,
        "search_customer_memory",
        lambda selected_store, context, query: (
            calls.append((selected_store, context, query)) or ["Prior claim summary"]
        ),
    )
    monkeypatch.setattr(tools, "gateway_tools", lambda: [])
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", "http://gateway.test/mcp")
    memory_tools = {
        item.name: item
        for item in tools.route_tools(context_for(), "gateway", memory_store=store)
    }

    assert "search_memory" in memory_tools
    schema_type = memory_tools["search_memory"].args_schema
    assert isinstance(schema_type, type)
    assert issubclass(schema_type, BaseModel)
    schema = schema_type.model_json_schema()
    assert set(schema["properties"]) == {"query"}
    assert schema["required"] == ["query"]
    assert schema["additionalProperties"] is False
    assert memory_tools["search_memory"].invoke({"query": "medical"}) == [
        "Prior claim summary"
    ]
    assert calls == [(store, context_for(), "medical")]

    without_memory = tools.route_tools(context_for())
    evaluation = tools.route_tools(
        context_for("evaluation"), "gateway", memory_store=store
    )
    assert "search_memory" not in {item.name for item in without_memory}
    assert "search_memory" not in {item.name for item in evaluation}


def test_search_memory_refusal_reaches_model_and_allows_corrected_decision(
    client, monkeypatch
):
    use_backend(monkeypatch)

    class FailingSearchStore(InMemoryStore):
        def search(self, *args, **kwargs):
            raise RuntimeError("Stand-in memory search failed")

    store = FailingSearchStore()
    monkeypatch.setattr(main, "build_memory_store", lambda _context: store)
    model = ScriptedModel(
        [
            model_tool_call(
                "memory-search",
                "search_memory",
                {"query": "prior medical claim"},
            ),
            model_tool_call(
                "corrected-referral",
                "record_decision",
                {
                    "decision": "refer",
                    "payout": 0,
                    "reason": "The evidence needs handler review.",
                    "section": "Section 14",
                },
            ),
        ]
    )
    monkeypatch.setattr(main, "model", lambda _configuration: model)

    assert main.run_with_context("CLM-1001", "memory-search-refusal") == (
        "CLM-1001: refer $0.00"
    )
    assert len(model.inputs) == 2
    refusal = next(
        message
        for message in model.inputs[1]
        if isinstance(message, ToolMessage) and message.tool_call_id == "memory-search"
    )
    assert refusal.status == "error"
    assert "Stand-in memory search failed" in str(refusal.content)
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["decision"]["decision"] == "refer"
    assert bundle["decision"]["reason"] == "The evidence needs handler review."


def test_accepted_approval_memory_save_is_checkpointed_before_payment(
    client, monkeypatch
):
    use_backend(monkeypatch)

    class RecordingStore(InMemoryStore):
        def __init__(self):
            super().__init__()
            self.put_calls = []
            self.actions = []

        def put(
            self,
            namespace: tuple[str, ...],
            key: str,
            value: Mapping[str, Any],
            index: Literal[False] | list[str] | None = None,
            *,
            ttl: Any = None,
        ):
            self.put_calls.append((namespace, key, value))
            self.actions.append("save")
            return super().put(namespace, key, value, index=index, ttl=ttl)

    store = RecordingStore()
    monkeypatch.setattr(main, "build_memory_store", lambda _context: store)
    model = ScriptedModel(approval_responses())
    monkeypatch.setattr(main, "model", lambda _configuration: model)
    original_payment = main.save_payment
    payment_attempts = []

    def fail_once(claim_id, amount):
        payment_attempts.append((claim_id, amount))
        store.actions.append("pay")
        if len(payment_attempts) == 1:
            raise RuntimeError("Payment API unavailable")
        return original_payment(claim_id, amount)

    monkeypatch.setattr(main, "save_payment", fail_once)
    with pytest.raises(RuntimeError, match="Payment API unavailable"):
        main.run_with_context("CLM-1001", "memory-save-resume")

    assert store.actions == ["save", "save", "pay"]
    assert len(store.put_calls) == 2
    assert [entry[0] for entry in store.put_calls] == [
        ("CUS-3001", "CLM-1001"),
        ("CUS-3001", "CLM-1001"),
    ]
    human_item = store.get(("CUS-3001", "CLM-1001"), "claim_summary")
    decision_item = store.get(("CUS-3001", "CLM-1001"), "claim_decision")
    assert human_item is not None
    assert decision_item is not None
    assert "Maya Patel" not in human_item.value["message"].content
    stored_decision = json.loads(decision_item.value["message"].content)
    assert stored_decision["decision"] == "approve"
    assert stored_decision["payout"] == 420
    assert stored_decision["section"] == "Section 3: Trip Delay; Section 10: Payment"
    assert "UnderCover Agent" not in decision_item.value["message"].content

    assert (
        main.run_with_context("CLM-1001", "memory-save-resume")
        == "CLM-1001: approve $420.00; payment recorded"
    )
    assert store.actions == ["save", "save", "pay", "pay"]
    assert len(store.put_calls) == 2
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["paid_amount"] == 420
    memory_steps = [
        entry for entry in bundle["activity"] if entry["kind"] == "memory_save"
    ]
    assert len(memory_steps) == 1
    assert memory_steps[0]["details"]["messages"] == [
        human_item.value["message"].content,
        decision_item.value["message"].content,
    ]
    assert bundle["activity"].index(memory_steps[0]) < next(
        index
        for index, entry in enumerate(bundle["activity"])
        if entry["message"] == "Payment recorded: $420.00"
    )


@pytest.mark.parametrize("decision", ["decline", "refer"])
def test_nonapproval_decisions_are_saved_without_payment(client, monkeypatch, decision):
    use_backend(monkeypatch)
    store = InMemoryStore()
    monkeypatch.setattr(main, "build_memory_store", lambda _context: store)
    saved = []
    monkeypatch.setattr(
        main,
        "save_claim_memory",
        lambda selected_store, context, bundle: (
            saved.append(bundle["decision"].copy()) or []
        ),
    )

    class DecliningModel(ScriptedModel):
        def __init__(self):
            super().__init__(
                [
                    model_tool_call(
                        "record",
                        "record_decision",
                        {
                            "decision": decision,
                            "payout": 0,
                            "reason": "The evidence supports this outcome.",
                            "section": "Section 9",
                        },
                    )
                ]
            )

    monkeypatch.setattr(main, "model", lambda _configuration: DecliningModel())
    assert main.run_with_context("CLM-1001", f"memory-{decision}") == (
        f"CLM-1001: {decision} $0.00"
    )
    assert len(saved) == 1
    assert saved[0]["decision"] == decision


def test_memory_store_failure_does_not_block_approval_payment(
    client, monkeypatch, caplog
):
    use_backend(monkeypatch)

    class FailingStore(InMemoryStore):
        def put(
            self,
            namespace: tuple[str, ...],
            key: str,
            value: Mapping[str, Any],
            index: Literal[False] | list[str] | None = None,
            *,
            ttl: Any = None,
        ):
            raise OSError("Local memory store failure")

    monkeypatch.setattr(main, "build_memory_store", lambda _context: FailingStore())
    monkeypatch.setattr(
        main, "model", lambda _configuration: ScriptedModel(approval_responses())
    )

    assert (
        main.run_with_context("CLM-1001", "memory-save-failure")
        == "CLM-1001: approve $420.00; payment recorded"
    )
    assert client.get("/api/claims/CLM-1001").json()["paid_amount"] == 420
    assert "Could not save customer memory" in caplog.text
    assert not any(
        entry["kind"] == "memory_save"
        for entry in client.get("/api/claims/CLM-1001").json()["activity"]
    )


def test_memory_is_not_saved_again_after_signoff_resume(client, monkeypatch):
    use_backend(monkeypatch)

    class RecordingStore(InMemoryStore):
        def __init__(self):
            super().__init__()
            self.put_calls = []

        def put(
            self,
            namespace: tuple[str, ...],
            key: str,
            value: Mapping[str, Any],
            index: Literal[False] | list[str] | None = None,
            *,
            ttl: Any = None,
        ):
            self.put_calls.append((namespace, key))
            return super().put(namespace, key, value, index=index, ttl=ttl)

    store = RecordingStore()
    monkeypatch.setattr(main, "build_memory_store", lambda _context: store)
    monkeypatch.setattr(
        main, "model", lambda _configuration: ScriptedModel(held_approval_responses())
    )

    assert main.run_with_context("CLM-1003", "memory-signoff") == (
        "CLM-1003: approve $3,750.00; waiting for sign-off"
    )
    assert len(store.put_calls) == 2
    signed_off = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    assert signed_off.status_code == 200
    assert (
        main.run_with_context("CLM-1003", "memory-signoff")
        == "CLM-1003: approve $3,750.00; payment recorded"
    )
    assert len(store.put_calls) == 2


def test_gateway_payment_hold_and_signoff_resume_do_not_repeat_memory_save(
    client, monkeypatch
):
    """Stub Gateway decision/payment tools; exercise graph checkpoint boundaries."""
    use_backend(monkeypatch)
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", "http://gateway.test/mcp")
    with connect() as connection:
        company = connection.execute(
            settings_table.select()
            .with_only_columns(settings_table.c.data)
            .where(settings_table.c.key == "company")
        ).scalar_one()
        connection.execute(
            settings_table.update()
            .where(settings_table.c.key == "company")
            .values(data={**company, "payout_approval_limit": 5000})
        )
    calculate_approval(client, "CLM-1003", 3750)

    class RecordingStore(InMemoryStore):
        def __init__(self):
            super().__init__()
            self.put_calls = []

        def put(
            self,
            namespace: tuple[str, ...],
            key: str,
            value: Mapping[str, Any],
            index: Literal[False] | list[str] | None = None,
            *,
            ttl: Any = None,
        ):
            self.put_calls.append((namespace, key))
            return super().put(namespace, key, value, index=index, ttl=ttl)

    store = RecordingStore()
    monkeypatch.setattr(main, "build_memory_store", lambda _context: store)

    class GatewayPaymentRefusal(McpError):
        def __init__(self):
            super().__init__(ErrorData(code=-32000, message="Gateway rejected payment"))

    class RemoteDecisionTool:
        name = "undercover-app___record_decision"

        async def ainvoke(self, call: ToolCall) -> ToolMessage:
            arguments = call["args"]
            with api.backend() as backend_client:
                response = backend_client.post(
                    f"/api/claims/{arguments['claim_id']}/decision",
                    json={
                        key: value
                        for key, value in arguments.items()
                        if key != "claim_id"
                    },
                )
            response.raise_for_status()
            return ToolMessage(
                content=response.text,
                artifact={"structured_content": response.json()},
                tool_call_id=call["id"],
                name=call["name"],
            )

    payment_calls = []

    class RemotePaymentTool:
        name = "undercover-app___pay_claim"

        async def ainvoke(self, call: ToolCall) -> ToolMessage:
            payment_calls.append(call)
            raise GatewayPaymentRefusal()

    monkeypatch.setattr(tools, "gateway_tools", lambda: [RemoteDecisionTool()])
    payment_tool = RemotePaymentTool()
    monkeypatch.setattr(gateway, "gateway_tools", lambda: [payment_tool])
    model = ScriptedModel(
        [
            model_tool_call(
                "decision",
                "record_decision",
                {
                    "decision": "approve",
                    "payout": 3750,
                    "reason": "The medical expense is covered.",
                    "section": "Section 7: Emergency Medical; Section 10: Payment",
                },
            )
        ]
    )
    monkeypatch.setattr(main, "model", lambda _configuration: model)

    assert main.run_with_context("CLM-1003", "memory-gateway-hold") == (
        "CLM-1003: approve $3,750.00; waiting for sign-off after Gateway refusal"
    )
    held = client.get("/api/claims/CLM-1003").json()
    assert held["gateway_refused"] is True
    assert held["claim"]["status"] == "waiting_for_sign_off"
    assert len(payment_calls) == 1
    assert len(store.put_calls) == 2

    signed_off = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    assert signed_off.status_code == 200
    assert (
        main.run_with_context("CLM-1003", "memory-gateway-hold")
        == "CLM-1003: approve $3,750.00; payment recorded"
    )
    assert len(store.put_calls) == 2
    assert len(payment_calls) == 1


def test_evaluation_graph_omits_memory_store_and_tool_even_when_enabled(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "must-not-connect")

    class Checker:
        def with_structured_output(self, schema, **kwargs):
            return self

    model = ScriptedModel([])
    graph = main.build_claim_graph(
        model,
        "Instructions",
        MemorySaver(),
        Checker(),
        context_for("evaluation"),
    )

    assert "search_memory" not in {item.name for item in model.model_tools}
    assert graph.store is None


def test_security_referral_bypasses_memory_save(client, monkeypatch):
    use_backend(monkeypatch)
    store = InMemoryStore()
    monkeypatch.setattr(main, "build_memory_store", lambda _context: store)
    saved = []
    monkeypatch.setattr(main, "save_claim_memory", lambda *_args: saved.append(True))

    class FlaggedChecker:
        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            return {
                "raw": AIMessage(content="screened"),
                "parsed": main.Screening(
                    reason="Customer text attempted to override policy.",
                    instructions_found=True,
                ),
                "parsing_error": None,
            }

    monkeypatch.setattr(
        main, "screening_model", lambda _configuration: FlaggedChecker()
    )
    model = ScriptedModel([])
    monkeypatch.setattr(main, "model", lambda _configuration: model)

    assert main.run_with_context("CLM-1001", "memory-security") == (
        "CLM-1001: refer $0.00"
    )
    assert model.invoke_calls == 0
    assert saved == []
