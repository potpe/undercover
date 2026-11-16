from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from langchain_core.tools import ToolException
from langgraph.store.base import BaseStore
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData
from pydantic import ValidationError

from agent import api, tools
from agent.claim_context import ClaimContext
from agent.direct import DirectRoute

CLAIM_CONTEXT = ClaimContext(
    claim_id="CLM-1001",
    policy_id="POL-2001",
    customer_id="CUS-3001",
    configuration_version=1,
    run_kind="dispatch",
)


class FakeDirect:
    def __init__(self, context_result=None, operation_result=None, error=None):
        self.context_result = context_result
        self.operation_result = operation_result
        self.error = error
        self.calls = []

    def invoke_context_tool(self, operation, arguments):
        self.calls.append((operation, arguments))
        if self.error:
            raise self.error
        return self.context_result

    def get_policy(self, **arguments):
        self.calls.append(("get_policy", arguments))
        if self.error:
            raise self.error
        return self.operation_result


class FakeStore:
    def __init__(self, result=None, error=None):
        self.result = result or []
        self.error = error

    def search(self, namespace_prefix, *, query=None, limit=10, **_kwargs):
        if self.error:
            raise self.error
        return self.result


def make_route(logged, *, selected_gateway=False, direct=None, remote=None, store=None):
    return tools.ModelToolRoute(
        claim_context=CLAIM_CONTEXT,
        direct=cast(DirectRoute, direct or FakeDirect()),
        selected_gateway=selected_gateway,
        remote=remote or {},
        memory_store=cast(BaseStore, store) if store is not None else None,
    )


def step_logger(logged):
    def log_step(claim_id, kind, message, details):
        logged.append(
            {
                "claim_id": claim_id,
                "kind": kind,
                "message": message,
                "details": details,
            }
        )

    return log_step


def assert_tool_event(event, name, arguments):
    assert event["kind"] == "tool"
    assert event["claim_id"] == "CLM-1001"
    assert event["details"] == {
        "name": name,
        "input": arguments,
    }


def source(file, owner):
    return {
        "file": file,
        "system": "Claims Operations",
        "owner": owner,
        "reviewed": "2026-01-01",
    }


def test_api_log_step_posts_typed_body_to_claim_steps(monkeypatch):
    observed = {}

    class Response:
        def raise_for_status(self):
            observed["raised"] = True

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, path, *, json):
            observed.update(path=path, body=json)
            return Response()

    monkeypatch.setattr(api, "backend", Client)
    details = {"name": "get_policy", "input": {}}
    api.log_step("CLM-1001", "tool", "Calling tool get_policy", details)
    assert observed == {
        "path": "/api/claims/CLM-1001/steps",
        "body": {
            "kind": "tool",
            "message": "Calling tool get_policy",
            "details": details,
        },
        "raised": True,
    }


def test_api_log_step_validates_kind_and_details_before_post(monkeypatch):
    monkeypatch.setattr(
        api,
        "backend",
        lambda: pytest.fail("invalid activity steps must not be posted"),
    )

    with pytest.raises(ValidationError):
        api.log_step("CLM-1001", "unknown", "Bad step", {})
    with pytest.raises(ValidationError):
        api.log_step(
            "CLM-1001",
            "lookup",
            "Bad source",
            {
                "operation": "search_documents",
                "input": {},
                "sources": [{"file": "policy.md"}],
            },
        )


def test_api_log_step_persists_with_backend(client, monkeypatch):
    class ClientAdapter:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, path, *, json):
            return client.post(path, json=json)

    monkeypatch.setattr(api, "backend", ClientAdapter)
    details = {"name": "get_policy", "input": {}}

    api.log_step("CLM-1001", "tool", "Calling tool get_policy", details)

    bundle = client.get("/api/claims/CLM-1001").json()
    activity = bundle["activity"][-1]
    assert activity["kind"] == "tool"
    assert activity["message"] == "Calling tool get_policy"
    assert activity["details"] == details


def test_context_lookup_step_is_accepted_by_backend_and_answer_is_unchanged(
    client, monkeypatch
):
    class ClientAdapter:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def post(self, path, *, json):
            return client.post(path, json=json)

    monkeypatch.setattr(api, "backend", ClientAdapter)
    normalized_sources = [
        source("policy.md", "Policy"),
        source("manual.md", "Claims"),
    ]
    answer = {
        "sections": [
            {
                "text": "Policy wording",
                "source": {**normalized_sources[0], "document": "policy_wording"},
            },
            {
                "text": "Claims manual",
                "source": {**normalized_sources[1], "document": "claims_manual"},
            },
        ]
    }
    result = {"answer": answer, "sources": normalized_sources}
    route = make_route(
        [],
        direct=FakeDirect(context_result=result),
    )

    assert route.invoke("search_documents", {"query": "baggage"}) is result

    activity = client.get("/api/claims/CLM-1001").json()["activity"]
    assert [entry["kind"] for entry in activity[-2:]] == ["tool", "lookup"]
    assert activity[-1]["details"] == {
        "operation": "search_documents",
        "input": {"query": "baggage"},
        "sources": normalized_sources,
    }
    assert all(
        set(entry) == {"file", "system", "owner", "reviewed"}
        for entry in activity[-1]["details"]["sources"]
    )


def test_direct_app_tool_is_logged_before_execution(monkeypatch):
    logged = []
    monkeypatch.setattr(tools, "log_step", step_logger(logged))
    direct = FakeDirect(operation_result={"policy": "sample"})
    route = make_route(logged, direct=direct)

    result = route.invoke("get_policy", {})

    assert result == {"policy": "sample"}
    assert_tool_event(logged[0], "get_policy", {})
    assert logged[0]["message"] == "Calling tool get_policy"
    assert direct.calls == [("get_policy", {})]
    assert [event["kind"] for event in logged] == ["tool"]


def test_gateway_tool_log_uses_remote_name_and_input_before_call(monkeypatch):
    logged = []
    called = []
    monkeypatch.setattr(tools, "log_step", step_logger(logged))

    def invoke(_tool, arguments):
        called.append((len(logged), arguments))
        return {"policy": "sample"}

    monkeypatch.setattr(tools, "invoke_gateway_tool", invoke)
    route = make_route(
        logged,
        selected_gateway=True,
        remote={"undercover-app___get_policy": object()},
    )

    assert route.invoke("get_policy", {}) == {"policy": "sample"}
    assert_tool_event(logged[0], "undercover-app___get_policy", {})
    assert called == [(1, {"policy_id": "POL-2001"})]


def test_context_lookup_preserves_answer_and_all_flat_sources(monkeypatch):
    logged = []
    monkeypatch.setattr(tools, "log_step", step_logger(logged))
    answer = {
        "results": [
            {"text": "policy wording", "source": source("policy.md", "Policy")},
            {"text": "manual", "source": source("manual.md", "Claims")},
        ]
    }
    sources = [source("policy.md", "Policy"), source("manual.md", "Claims")]
    direct = FakeDirect(
        context_result={
            "answer": answer,
            "sources": [*sources, {"file": "incomplete.md"}],
        }
    )
    route = make_route(logged, direct=direct)
    arguments = {"query": "covered baggage"}

    result = route.invoke("search_documents", arguments)

    returned_sources = [*sources, {"file": "incomplete.md"}]
    assert result == {"answer": answer, "sources": returned_sources}
    assert_tool_event(logged[0], "search_documents", arguments)
    assert logged[1] == {
        "claim_id": "CLM-1001",
        "kind": "lookup",
        "message": "Looked up context with search_documents",
        "details": {
            "operation": "search_documents",
            "input": arguments,
            "sources": sources,
        },
    }


def test_gateway_context_lookup_records_prefixed_tool_and_sources(monkeypatch):
    logged = []
    monkeypatch.setattr(tools, "log_step", step_logger(logged))
    arguments = {"term": "deductible"}
    sources = [source("plans.yaml", "Product")]
    result = {"answer": {"term": "deductible"}, "sources": sources}
    monkeypatch.setattr(tools, "invoke_gateway_tool", lambda *_args: result)
    route = make_route(
        logged,
        selected_gateway=True,
        remote={"undercover-context___lookup_term": object()},
    )

    assert route.invoke("lookup_term", arguments) == result
    assert_tool_event(logged[0], "undercover-context___lookup_term", arguments)
    assert logged[1]["kind"] == "lookup"
    assert logged[1]["details"]["sources"] == sources


def test_memory_search_records_query_and_results_and_stays_local(monkeypatch):
    logged = []
    monkeypatch.setattr(tools, "log_step", step_logger(logged))
    store = FakeStore()
    route = make_route(logged, selected_gateway=True, store=store)
    monkeypatch.setattr(tools, "search_customer_memory", lambda *_args: ["prior claim"])

    assert route.invoke("search_memory", {"query": "baggage"}) == ["prior claim"]
    assert_tool_event(logged[0], "search_memory", {"query": "baggage"})
    assert logged[1] == {
        "claim_id": "CLM-1001",
        "kind": "memory_search",
        "message": "Searched customer memory for baggage",
        "details": {"query": "baggage", "results": ["prior claim"]},
    }


@pytest.mark.parametrize(
    ("operation", "source_name", "reason"),
    [
        ("get_policy", "app", "policy unavailable"),
        ("lookup_term", "context", "unknown term"),
        ("search_memory", "memory", "Customer memory search was refused: down"),
    ],
)
def test_direct_refusals_are_typed_once_with_original_wording(
    monkeypatch, operation, source_name, reason
):
    logged = []
    monkeypatch.setattr(tools, "log_step", step_logger(logged))
    if operation == "get_policy":

        class AppRefusal(Exception):
            response = SimpleNamespace(is_error=True, text="policy unavailable")

        direct = FakeDirect(error=AppRefusal())
        route = make_route(logged, direct=direct)
    elif operation == "lookup_term":
        route = make_route(
            logged, direct=FakeDirect(error=ToolException("unknown term"))
        )
    else:
        route = make_route(logged, store=FakeStore())
        monkeypatch.setattr(
            tools,
            "search_customer_memory",
            lambda *_args: (_ for _ in ()).throw(
                ToolException("Customer memory search was refused: down")
            ),
        )

    with pytest.raises(ToolException):
        route.invoke(operation, {"query": "query"} if operation != "get_policy" else {})

    assert logged[0]["kind"] == "tool"
    assert len([event for event in logged if event["kind"] == "refusal"]) == 1
    refusal = logged[-1]
    assert refusal["message"] == f"{source_name.title()} refusal: {reason}"
    assert refusal["details"] == {
        "source": source_name,
        "reason": reason,
        "operation": operation,
    }


def test_gateway_transport_refusal_is_typed_as_gateway(monkeypatch):
    logged = []
    monkeypatch.setattr(tools, "log_step", step_logger(logged))

    def fail(*_args):
        raise McpError(ErrorData(code=-32000, message="Gateway unavailable"))

    monkeypatch.setattr(tools, "invoke_gateway_tool", fail)
    route = make_route(
        logged,
        selected_gateway=True,
        remote={"undercover-app___get_policy": object()},
    )

    with pytest.raises(ToolException, match="Gateway unavailable"):
        route.invoke("get_policy", {})

    assert [event["kind"] for event in logged] == ["tool", "refusal"]
    assert logged[-1]["message"] == "Gateway refusal: Gateway unavailable"
    assert logged[-1]["details"] == {
        "source": "gateway",
        "reason": "Gateway unavailable",
        "operation": "get_policy",
    }


def test_context_transport_error_is_not_logged_as_refusal(monkeypatch):
    logged = []
    monkeypatch.setattr(tools, "log_step", step_logger(logged))
    direct = FakeDirect(error=httpx.ConnectError("context offline"))
    route = make_route(logged, direct=direct)

    with pytest.raises(httpx.ConnectError, match="context offline"):
        route.invoke("lookup_term", {"term": "benefit"})

    assert [event["kind"] for event in logged] == ["tool"]
