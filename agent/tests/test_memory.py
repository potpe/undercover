import json
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import ToolException
from langgraph.store.base import BaseStore, SearchItem

from agent import memory
from agent.claim_context import ClaimContext
from agent.memory import (
    build_memory_store,
    save_claim_memory,
    search_customer_memory,
)


def context(claim_id="CLM-1001", run_kind="dispatch"):
    return ClaimContext(
        claim_id=claim_id,
        policy_id="POL-1001",
        customer_id="CUS-1",
        configuration_version=1,
        run_kind=run_kind,
    )


class FakeStore:
    puts: list[tuple[tuple[str, ...], str, dict[str, Any]]]
    search_results: list[SearchItem]
    search_error: Exception | None
    put_error: Exception | None
    search_call: tuple[tuple[str, ...], str | None, int] | None

    def __init__(self):
        self.puts = []
        self.search_results = []
        self.search_error = None
        self.put_error = None
        self.search_call = None

    def put(self, namespace, key, value):
        if self.put_error:
            raise self.put_error
        self.puts.append((namespace, key, value))

    def search(self, namespace_prefix, *, query=None, limit=10, **_kwargs):
        self.search_call = (namespace_prefix, query, limit)
        if self.search_error:
            raise self.search_error
        return self.search_results


def item(namespaces, content, requested_namespace=("customers", "CUS-1")):
    return SearchItem(
        namespace=requested_namespace,
        key="memory",
        value={"namespaces": namespaces, "content": content},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def as_base_store(fake: FakeStore) -> BaseStore:
    return cast(BaseStore, fake)


def test_store_is_not_constructed_without_id_or_for_evaluation(monkeypatch):
    monkeypatch.delenv("UNDERCOVER_MEMORY_ID", raising=False)
    monkeypatch.setattr(
        memory.boto3,
        "Session",
        lambda: pytest.fail("must not create a boto session"),
    )
    monkeypatch.setattr(
        memory,
        "AgentCoreMemoryStore",
        lambda **_kwargs: pytest.fail("must not create a memory store"),
    )
    assert build_memory_store(context()) is None
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "memory-1")
    assert build_memory_store(context(run_kind="evaluation")) is None


def test_enabled_store_uses_memory_id_and_session_region(monkeypatch):
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "memory-1")
    seen = {}

    class Store:
        def __init__(self, *, memory_id, region_name):
            seen.update(memory_id=memory_id, region_name=region_name)

    class Session:
        region_name = "us-west-2"

    monkeypatch.setattr(memory, "AgentCoreMemoryStore", Store)
    monkeypatch.setattr(memory.boto3, "Session", Session)

    assert isinstance(build_memory_store(context()), Store)
    assert seen == {"memory_id": "memory-1", "region_name": "us-west-2"}


def test_search_filters_other_customers_and_current_claim_and_uses_content():
    store = FakeStore()
    store.search_results = [
        item(["/customers/CUS-1/claims/CLM-1002/"], "other claim"),
        item(["/customers/CUS-1/claims/CLM-1001/"], "current claim"),
        item(["/customers/CUS-10/claims/CLM-1002/"], "similar customer"),
        item(["/customers/CUS-1/claims/CLM-1003/"], "prior claim"),
        item(["/customers/CUS-1/claims/CLM-1004/"], "prior claim 2"),
    ]

    found = search_customer_memory(as_base_store(store), context(), "medical")

    assert found == [
        "other claim",
        "prior claim",
        "prior claim 2",
    ]
    assert store.search_call == (("customers", "CUS-1"), "medical", 5)


def test_search_rejects_a_malicious_cross_customer_result_even_with_prefix_namespace():
    store = FakeStore()
    store.search_results = [
        item(
            ["/customers/CUS-10/claims/CLM-1002/"],
            "private",
            requested_namespace=("customers", "CUS-1"),
        )
    ]
    assert search_customer_memory(as_base_store(store), context(), "query") == []


def test_search_failure_is_a_tool_refusal():
    store = FakeStore()
    store.search_error = RuntimeError("down")
    with pytest.raises(ToolException, match="memory search was refused: down"):
        search_customer_memory(as_base_store(store), context(), "query")


def decision_bundle() -> dict[str, Any]:
    return {
        "claim": {
            "summary": "Lost luggage",
            "claim_type": "baggage",
            "incident_date": "2026-01-01",
            "incident_location": "Paris",
            "items": [{"name": "Bag", "amount": 100}],
            "story": "Sensitive customer narrative",
            "documents": [{"name": "receipt"}],
            "customer_id": "CUS-1",
        },
        "decision": {
            "decision": "approve",
            "payout": 100,
            "section": "Section 3",
            "reason": "Covered loss.",
            "decided_by": "UnderCover Agent (configuration version 1)",
            "manual_version": "2026.2",
        },
    }


@pytest.mark.parametrize(
    ("decision", "payout", "section", "reason"),
    [
        ("approve", 100, "Section 3", "Covered loss."),
        ("decline", 0, "Section 8", "The loss is excluded."),
        ("refer", 0, "Section 14", "More evidence is needed."),
    ],
)
def test_save_persists_allowlisted_human_then_ai_event_pair(
    decision, payout, section, reason
):
    store = FakeStore()
    bundle = decision_bundle()
    bundle["decision"].update(
        decision=decision,
        payout=payout,
        section=section,
        reason=reason,
    )
    saved = save_claim_memory(as_base_store(store), context(), bundle)

    assert saved is not None
    assert len(store.puts) == 2
    assert [entry[0] for entry in store.puts] == [
        ("CUS-1", "CLM-1001"),
        ("CUS-1", "CLM-1001"),
    ]
    assert [entry[1] for entry in store.puts] == [
        "claim_summary",
        "claim_decision",
    ]
    human, assistant = [entry[2]["message"] for entry in store.puts]
    assert isinstance(human, HumanMessage)
    assert isinstance(assistant, AIMessage)
    assert json.loads(cast(str, human.content)) == {
        "summary": "Lost luggage",
        "claim_type": "baggage",
        "incident_date": "2026-01-01",
        "incident_location": "Paris",
        "items": [{"name": "Bag", "amount": 100}],
    }
    assert json.loads(cast(str, assistant.content)) == {
        "decision": decision,
        "payout": payout,
        "section": section,
        "reason": reason,
    }
    assert saved == [cast(str, human.content), cast(str, assistant.content)]


@pytest.mark.parametrize(
    ("claim_id", "run_kind", "maker"),
    [
        ("CLM-1001", "evaluation", "UnderCover Agent (configuration version 1)"),
        ("CLM-1001", "dispatch", "Security check"),
    ],
)
def test_save_skips_evaluation_and_security_decisions(claim_id, run_kind, maker):
    store = FakeStore()
    bundle = decision_bundle()
    bundle["decision"]["decided_by"] = maker
    assert (
        save_claim_memory(as_base_store(store), context(claim_id, run_kind), bundle)
        is None
    )
    assert store.puts == []


def test_save_skips_evaluation_copy_id_even_with_dispatch_context():
    store = FakeStore()
    bundle = decision_bundle()
    bundle["evaluation_run_id"] = 2

    assert (
        save_claim_memory(
            as_base_store(store), context("CLM-1001-E2", "dispatch"), bundle
        )
        is None
    )
    assert store.puts == []


def test_save_skips_when_there_is_no_accepted_decision():
    store = FakeStore()
    bundle = decision_bundle()
    bundle["decision"] = None
    assert save_claim_memory(as_base_store(store), context(), bundle) is None
    assert store.puts == []


def test_save_failure_is_nonfatal_and_returns_none(caplog):
    store = FakeStore()
    store.put_error = RuntimeError("offline")
    assert save_claim_memory(as_base_store(store), context(), decision_bundle()) is None
    assert "Could not save customer memory" in caplog.text
