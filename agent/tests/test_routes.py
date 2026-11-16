import asyncio
import json
import socket
from inspect import Parameter, Signature
from threading import Event, Thread
from types import SimpleNamespace
from typing import Any, TypeGuard, cast, get_args

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import BaseTool, ToolException
from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import CallToolResult, ErrorData
from pydantic import BaseModel

from agent import api, direct, gateway, tools
from agent.claim_context import ClaimContext
from agent.mcp_sync import run_mcp
from backend.main import app as backend_app
from backend.main import handler
from backend.models import Benefit, DecisionKind, PayoutLine


def test_claim_tools_are_scoped_and_decision_maker_is_injected(monkeypatch):
    monkeypatch.setattr(tools, "gateway_tools", lambda: [])
    tool_list = tools.route_tools(context_fixture("CLM-1001", 7))
    schemas = {item.name: item.args for item in tool_list}

    assert set(schemas) == {
        "get_claim",
        "get_policy",
        "get_claim_history",
        "calculate_payout",
        "record_decision",
        "lookup_term",
        "describe_fields",
        "get_plan",
        "get_claim_rules",
        "search_reference_data",
        "search_documents",
        "read_section",
    }
    assert {
        "get_claim",
        "get_policy",
        "get_claim_history",
        "calculate_payout",
        "record_decision",
        "pay_claim",
    } == tools.APP_OPERATIONS
    assert {
        "lookup_term",
        "describe_fields",
        "get_plan",
        "get_claim_rules",
        "search_reference_data",
        "search_documents",
        "read_section",
    } == tools.CONTEXT_OPERATIONS
    for name in ("get_claim", "get_policy", "get_claim_history"):
        assert schemas[name] == {}
    assert "claim_id" not in schemas["calculate_payout"]
    assert "claim_id" not in schemas["record_decision"]
    assert "decided_by" not in schemas["record_decision"]
    assert "pay_claim" not in schemas
    assert schemas["lookup_term"] == {"term": {"title": "Term", "type": "string"}}
    assert schemas["describe_fields"] == {
        "record": {"title": "Record", "type": "string"}
    }
    assert schemas["get_plan"] == {"plan": {"title": "Plan", "type": "string"}}
    assert schemas["get_claim_rules"] == {}
    assert schemas["search_reference_data"] == {
        "query": {"title": "Query", "type": "string"}
    }
    assert schemas["search_documents"] == {
        "query": {"title": "Query", "type": "string"}
    }
    assert schemas["read_section"] == {
        "document": {"title": "Document", "type": "string"},
        "number": {"title": "Number", "type": "integer"},
    }
    assert "minimum" not in schemas["record_decision"]["payout"]
    payout_tool = next(item for item in tool_list if item.name == "calculate_payout")
    payout_schema = payout_tool.args_schema
    assert is_pydantic_schema(payout_schema)
    lines_field = payout_schema.model_fields.get("lines")
    assert lines_field is not None
    line_types = get_args(lines_field.annotation)
    assert line_types
    assert isinstance(line_types[0], type)
    assert issubclass(line_types[0], PayoutLine)


def test_gateway_tool_surface_is_local_and_prefixed_remote_ids_are_injected(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(tools, "log_step", lambda *_args: None)

    async def invoke(call):
        calls.append(call["args"])
        return ToolMessage(
            content='{"payout":10}',
            artifact={"structured_content": {"payout": 10}},
            tool_call_id=call["id"],
            name=call["name"],
        )

    remote_tool = SimpleNamespace(
        name="undercover-app___calculate_payout",
        ainvoke=invoke,
    )
    monkeypatch.setattr(tools, "gateway_tools", lambda: [remote_tool])
    route_tool_list = tools.route_tools(
        context_fixture("CLM-1002", 7),
        "gateway",
    )
    payout = next(item for item in route_tool_list if item.name == "calculate_payout")

    result = payout.invoke(
        {
            "benefit": "emergency_medical",
            "lines": [],
            "paid_by_others": 0,
        }
    )

    assert result == {"payout": 10}
    assert calls == [
        {
            "claim_id": "CLM-1002",
            "benefit": "emergency_medical",
            "lines": [],
            "paid_by_others": 0,
        }
    ]


def test_sigv4_auth_signs_for_agentcore_with_frozen_credentials(monkeypatch):
    signatures = []

    class Credentials:
        access_key = "access"
        secret_key = "s" * 32
        token = "t" * 32

    class Signer:
        def __init__(self, credentials, service, region):
            signatures.append((credentials, service, region))

        def add_auth(self, request):
            request.headers["Authorization"] = "AWS4 signed"

    monkeypatch.setattr(gateway, "SigV4Auth", Signer)
    auth = gateway.SigV4HTTPAuth(Credentials(), "us-west-2")
    request = next(
        auth.auth_flow(
            __import__("httpx").Request("POST", "https://gateway.example/mcp")
        )
    )

    assert request.headers["Authorization"] == "AWS4 signed"
    assert signatures == [(auth.credentials, "bedrock-agentcore", "us-west-2")]


def test_signer_uses_the_cli_session_frozen_credentials_and_region(monkeypatch):
    frozen = object()
    used = []

    class Credentials:
        freeze_calls = 0

        def get_frozen_credentials(self):
            self.freeze_calls += 1
            return frozen

    credentials = Credentials()

    class Session:
        region_name = "eu-west-1"

        def get_credentials(self):
            return credentials

    session = Session()
    monkeypatch.setattr(gateway.boto3, "Session", lambda: session)

    class Signer:
        def __init__(self, selected, service, region):
            used.append((selected, service, region))

        def add_auth(self, request):
            request.headers["Authorization"] = "signed"

    monkeypatch.setattr(gateway, "SigV4Auth", Signer)
    auth = gateway.signed_auth()
    requests = [
        next(
            auth.auth_flow(
                __import__("httpx").Request(
                    "POST", f"https://gateway.example/mcp/{request_id}"
                )
            )
        )
        for request_id in ("first", "second")
    ]

    assert auth.credentials is credentials
    assert auth.region == "eu-west-1"
    assert credentials.freeze_calls == 2
    assert used == [
        (frozen, "bedrock-agentcore", "eu-west-1"),
        (frozen, "bedrock-agentcore", "eu-west-1"),
    ]
    assert all(request.headers["Authorization"] == "signed" for request in requests)


def test_late_environment_settings_are_read_at_client_or_graph_creation(
    monkeypatch,
):
    api_settings = {}

    def build_http_client(**kwargs):
        api_settings.update(kwargs)
        return object()

    monkeypatch.setattr(api.httpx, "Client", build_http_client)
    monkeypatch.setenv("UNDERCOVER_API", "http://backend.test")
    monkeypatch.setenv("UNDERCOVER_API_KEY", "late-loaded-key")
    api.backend()
    assert api_settings["base_url"] == "http://backend.test"
    assert api_settings["headers"] == {"X-API-Key": "late-loaded-key"}

    monkeypatch.setenv("UNDERCOVER_CONTEXT", "http://context.test/mcp")
    assert direct.context_url() == "http://context.test/mcp"
    monkeypatch.setenv("UNDERCOVER_MEMORY_ID", "memory-late")
    context = ClaimContext.from_bundle(
        "CLM-1001",
        {
            "claim": {
                "policy_id": "POL-2001",
                "customer_id": "CUS-3001",
            }
        },
        configuration_version=3,
        run_kind="dispatch",
    )
    assert context.context_url == "http://context.test/mcp"
    assert context.memory_id == "memory-late"

    remote_tool = object()
    gateway_configuration = {}

    class Client:
        def __init__(self, configuration, **options):
            gateway_configuration.update(
                configuration=configuration,
                options=options,
            )

        def get_tools(self):
            return [remote_tool]

    monkeypatch.setattr(gateway, "MultiServerMCPClient", Client)
    monkeypatch.setattr(gateway, "signed_auth", lambda: "test-auth")
    monkeypatch.setattr(gateway, "run_mcp", lambda result: result)
    monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", "http://gateway.test/mcp")
    assert gateway.gateway_tools() == [remote_tool]
    assert gateway_configuration["configuration"]["undercover-app"]["url"] == (
        "http://gateway.test/mcp"
    )


def test_mcp_bridge_then_mangum_handler_in_same_process():
    async def operation():
        await asyncio.sleep(0)
        return asyncio.get_running_loop().is_running()

    assert run_mcp(operation()) is True
    response = handler(
        {
            "version": "2.0",
            "routeKey": "GET /health",
            "rawPath": "/health",
            "rawQueryString": "",
            "headers": {"host": "localhost"},
            "requestContext": {
                "http": {
                    "method": "GET",
                    "path": "/health",
                    "sourceIp": "127.0.0.1",
                    "protocol": "HTTP/1.1",
                }
            },
            "isBase64Encoded": False,
        },
        cast(Any, None),
    )
    assert response["statusCode"] == 200


def test_canonical_claim_serialization_is_stable():
    bundle = {
        "policy": {"policy_id": "POL-2001"},
        "claim": {
            "claim_id": "CLM-1001",
            "status": "in_progress",
            "documents": [{"content": "full customer document"}],
        },
        "customer": {
            "customer_id": "CUS-3001",
            "previous_claims": [{"claim_id": "CLM-0999"}],
        },
        "plan": {"plan": "Premium", "benefits": [{"benefit": "trip_delay"}]},
        "add_ons": [{"add_on": "adventure_sports", "limit": 500}],
        "activity": [{"id": 1, "message": "read"}],
        "decision": None,
        "paid_amount": None,
        "signed_off_by": None,
    }
    first = api.canonical_claim(bundle)
    later = {
        **bundle,
        "claim": {**bundle["claim"], "status": "decided"},
        "paid_amount": 420,
        "signed_off_by": "HND-01",
        "activity": [{"id": 2, "message": "decision recorded"}],
        "decision": {"decision": "approve"},
    }
    assert api.canonical_claim(later) == first
    assert "full customer document" in first
    assert "CUS-3001" in first
    assert "Premium" in first
    assert "adventure_sports" in first


def test_context_injects_policy_and_customer_ids_for_gateway_tools(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "log_step", lambda *_args: None)

    async def capture(call):
        calls.append(call["args"])
        return ToolMessage(
            content="[]",
            artifact={"structured_content": []},
            tool_call_id=call["id"],
            name=call["name"],
        )

    remote = [
        SimpleNamespace(name="undercover-app___get_policy", ainvoke=capture),
        SimpleNamespace(name="undercover-app___get_claim_history", ainvoke=capture),
        SimpleNamespace(name="undercover-app___record_decision", ainvoke=capture),
    ]
    monkeypatch.setattr(tools, "gateway_tools", lambda: remote)
    route_tool_list = tools.route_tools(
        context_fixture("CLM-1002", 7),
        "gateway",
    )
    next(item for item in route_tool_list if item.name == "get_policy").invoke({})
    next(item for item in route_tool_list if item.name == "get_claim_history").invoke(
        {}
    )
    next(item for item in route_tool_list if item.name == "record_decision").invoke(
        {
            "decision": "decline",
            "payout": 0,
            "reason": "Not covered.",
            "section": "Section 9",
        }
    )
    assert calls == [
        {"policy_id": "POL-2002"},
        {"customer_id": "CUS-3002"},
        {
            "decision": "decline",
            "payout": 0,
            "reason": "Not covered.",
            "section": "Section 9",
            "claim_id": "CLM-1002",
            "decided_by": "UnderCover Agent (configuration version 7)",
        },
    ]


def test_direct_route_injects_configured_decision_maker(monkeypatch):
    captured = []
    monkeypatch.setattr(tools, "log_step", lambda *_args: None)

    def record_decision(_self, payload):
        captured.append(payload)
        return {"decision": payload["decision"]}

    monkeypatch.setattr(tools.DirectRoute, "record_decision", record_decision)
    tool_list = tools.route_tools(context_fixture("CLM-1001"))
    tool = next(item for item in tool_list if item.name == "record_decision")
    tool.invoke(
        {
            "decision": "decline",
            "payout": 0,
            "reason": "Not covered.",
            "section": "Section 9",
        }
    )
    assert captured == [
        {
            "decision": "decline",
            "payout": 0,
            "reason": "Not covered.",
            "section": "Section 9",
            "decided_by": "UnderCover Agent (configuration version 1)",
        }
    ]


def test_context_rejects_untrusted_ids_in_all_tool_schemas(monkeypatch):
    monkeypatch.setattr(tools, "gateway_tools", lambda: [])
    route_tool_list = tools.route_tools(context_fixture("CLM-1001"))
    for selected_tool in route_tool_list:
        selected_schema = selected_tool.args_schema
        assert is_pydantic_schema(selected_schema)
        schema = selected_schema.model_json_schema()
        assert schema.get("additionalProperties") is False
        assert set(schema.get("required", [])) == set(schema.get("properties", {}))
        assert {"claim_id", "policy_id", "customer_id"}.isdisjoint(
            schema.get("properties", {})
        )
        assert "decided_by" not in schema.get("properties", {})
        if selected_tool.name in tools.CONTEXT_OPERATIONS:
            assert selected_tool.description
            assert "\n" not in selected_tool.description


def test_decision_draft_schema_defers_section14_validation_to_backend():
    draft = tools.DecisionInput.model_validate(
        {
            "decision": "approve",
            "payout": -1,
            "reason": "",
            "section": "",
        }
    )
    assert draft.payout == -1
    assert draft.reason == ""
    assert draft.section == ""


def is_pydantic_schema(value: object) -> TypeGuard[type[BaseModel]]:
    return isinstance(value, type) and issubclass(value, BaseModel)


def streamable_test_server(claim_bundle: dict[str, Any]):
    server = FastMCP("undercover-app")
    refusal_enabled = [False]
    context_refusal_enabled = [False]
    decision_calls = []
    calculation = {}

    @server.tool(name="undercover-app___get_claim", structured_output=True)
    def get_claim(claim_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            CallToolResult(content=[], structuredContent=claim_bundle),
        )

    @server.tool(name="undercover-app___get_policy")
    def get_policy(policy_id: str) -> dict[str, str]:
        if refusal_enabled[0]:
            raise ValueError("application refused")
        return {"policy_id": policy_id}

    @server.tool(name="undercover-app___get_claim_history")
    def get_claim_history(customer_id: str) -> list[dict[str, str]]:
        return [{"customer_id": customer_id, "claim_id": "CLM-0999"}]

    @server.tool(name="undercover-app___calculate_payout")
    def calculate_payout(
        claim_id: str,
        benefit: Benefit,
        lines: list[PayoutLine],
        paid_by_others: float,
    ) -> dict[str, Any]:
        calculation.update(
            lines=[{"step": line.description, "amount": line.amount} for line in lines],
            payout=120.0,
        )
        return {
            "claim_id": claim_id,
            "benefit": benefit,
            "line_count": len(lines),
            "paid_by_others": paid_by_others,
            "payout": 120.0,
        }

    def record_decision(**arguments: Any) -> dict[str, Any]:
        arguments = {
            key: arguments[key]
            for key in (
                "claim_id",
                "decision",
                "payout",
                "reason",
                "section",
                "decided_by",
            )
        }
        decision_calls.append(arguments)
        claim_bundle["decision"] = {
            key: value for key, value in arguments.items() if key != "claim_id"
        }
        claim_bundle["decision"].update(
            calculation=calculation.copy(),
            decided_at="2026-11-15T12:00:00+00:00",
            manual_version="2026.2",
        )
        claim_bundle["claim"]["status"] = "in_progress"
        return cast(
            dict[str, Any],
            CallToolResult(content=[], structuredContent=claim_bundle),
        )

    record_decision_signature = Signature(
        parameters=[
            Parameter("claim_id", Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
            Parameter(
                "decision", Parameter.POSITIONAL_OR_KEYWORD, annotation=DecisionKind
            ),
            Parameter("payout", Parameter.POSITIONAL_OR_KEYWORD, annotation=float),
            Parameter("reason", Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
            Parameter("section", Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
            Parameter("decided_by", Parameter.POSITIONAL_OR_KEYWORD, annotation=str),
        ],
        return_annotation=dict[str, Any],
    )
    record_decision.__signature__ = record_decision_signature
    server.add_tool(
        record_decision,
        name="undercover-app___record_decision",
        structured_output=True,
    )

    @server.tool(name="undercover-app___pay_claim")
    def pay_claim(claim_id: str, amount: float) -> dict[str, Any]:
        return {"claim_id": claim_id, "paid": amount}

    add_context_tools(server, "", context_refusal_enabled)
    add_context_tools(server, "undercover-context___", context_refusal_enabled)

    return server, refusal_enabled, context_refusal_enabled, decision_calls


def add_context_tools(
    server: FastMCP, prefix: str, refusal_enabled: list[bool]
) -> None:
    source = {
        "file": "claims_manual.md",
        "system": "Claims manual",
        "owner": "Claims Operations",
        "reviewed": "2026-01-01",
    }

    def envelope(answer: Any) -> dict[str, Any]:
        return {"answer": answer, "sources": [source]}

    @server.tool(name=f"{prefix}lookup_term")
    def lookup_term(term: str) -> dict[str, Any]:
        """Look up a defined term and its provenance."""
        if refusal_enabled[0]:
            raise ValueError("context refused this term")
        return envelope({"term": term, "definition": "A stand-in definition."})

    @server.tool(name=f"{prefix}describe_fields")
    def describe_fields(record: str) -> dict[str, Any]:
        """Describe the fields in a business record."""
        return envelope({"record": record, "fields": ["claim_id", "status"]})

    @server.tool(name=f"{prefix}get_plan")
    def get_plan(plan: str) -> dict[str, Any]:
        """Get a plan's benefits, deductibles and add-ons."""
        return envelope({"plan": plan, "deductible": 50, "benefits": [], "add_ons": []})

    @server.tool(name=f"{prefix}get_claim_rules")
    def get_claim_rules() -> dict[str, Any]:
        """Get the claims rules and their provenance."""
        return envelope({"rules": ["Apply the policy wording."]})

    @server.tool(name=f"{prefix}search_reference_data")
    def search_reference_data(query: str) -> dict[str, Any]:
        """Search reference data for a query."""
        return envelope([{"query": query, "topic": "claims"}])

    @server.tool(name=f"{prefix}search_documents")
    def search_documents(query: str) -> dict[str, Any]:
        """Search policy wording and claims manual documents."""
        result_source = {
            **source,
            "document": "claims_manual",
            "section_id": "10",
            "title": "Meals",
            "snippet": f"Reference to {query}; $35 per traveler.",
        }
        return {
            "answer": [{"source": result_source}],
            "sources": [source],
        }

    @server.tool(name=f"{prefix}read_section")
    def read_section(document: str, number: int) -> dict[str, Any]:
        """Read a numbered policy wording or claims manual section."""
        return envelope(
            {
                "document": document,
                "section_id": str(number),
                "title": "Meals",
                "content": "$35 per traveler",
                "source": source,
            }
        )


def test_real_streamable_http_gateway_tools_and_refusal_distinction(
    client, monkeypatch
):
    claim_bundle = client.get("/api/claims/CLM-1001").json()
    (
        server,
        refusal_enabled,
        context_refusal_enabled,
        decision_calls,
    ) = streamable_test_server(claim_bundle)
    app = server.streamable_http_app()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    ready = Event()

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets: list[socket.socket] | None = None) -> None:
            await super().startup(sockets=sockets)
            ready.set()

    config = uvicorn.Config(app, log_level="error")
    asgi_server = ReadyServer(config)
    thread = Thread(target=asgi_server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        assert ready.wait(5), "Streamable HTTP test server did not become ready"
        monkeypatch.setenv("UNDERCOVER_GATEWAY_URL", f"http://127.0.0.1:{port}/mcp")
        monkeypatch.setenv("UNDERCOVER_CONTEXT", f"http://127.0.0.1:{port}/mcp")
        monkeypatch.setattr(gateway, "signed_auth", lambda: None)
        logged_steps = []
        monkeypatch.setattr(
            tools,
            "log_step",
            lambda claim_id, kind, message, details: logged_steps.append(
                {
                    "claim_id": claim_id,
                    "kind": kind,
                    "message": message,
                    "details": details,
                }
            ),
        )

        def test_backend() -> TestClient:
            return TestClient(
                backend_app,
                headers={"X-API-Key": "undercover-e2e-agent-key-32-characters"},
            )

        monkeypatch.setattr(api, "backend", test_backend)
        monkeypatch.setattr(direct, "backend", test_backend)
        remote = gateway.gateway_tools()
        monkeypatch.setattr(tools, "gateway_tools", lambda: remote)
        context = context_fixture("CLM-1001")
        accepted_decisions = []
        gateway_tools = tools.route_tools(
            context,
            "gateway",
            on_decision=lambda payload, bundle: accepted_decisions.append(
                (payload, bundle)
            ),
        )
        local_tools = {item.name: item for item in gateway_tools}
        direct_tools = {
            item.name: item for item in tools.route_tools(context, "direct")
        }
        assert_model_tool_schemas_match(direct_tools, local_tools)
        assert_streamable_claim_parity(direct_tools, local_tools, remote, claim_bundle)
        assert_gateway_read_tools(local_tools)
        assert_context_tool_routes(direct_tools, local_tools)
        assert_gateway_payout_tool(local_tools)
        assert_gateway_decision_tool(local_tools, accepted_decisions, decision_calls)
        assert_context_refusal_routes(
            direct_tools, local_tools, context_refusal_enabled, logged_steps
        )
        assert gateway.pay_gateway("CLM-1001", 120)["paid"] == 120
        assert_app_refusal(local_tools, refusal_enabled)
    finally:
        asgi_server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def assert_streamable_claim_parity(
    direct_tools: dict[str, BaseTool],
    gateway_tools: dict[str, BaseTool],
    remote: list[Any],
    bundle: dict[str, Any],
) -> None:
    assert gateway_tools["get_claim"].invoke({}) == direct_tools["get_claim"].invoke({})
    claim_tool = next(
        item for item in remote if item.name == "undercover-app___get_claim"
    )
    call: ToolCall = {
        "name": claim_tool.name,
        "args": {"claim_id": "CLM-1001"},
        "id": "structured-claim",
        "type": "tool_call",
    }
    result = run_mcp(claim_tool.ainvoke(call))
    assert isinstance(result, ToolMessage)
    assert result.content == []
    assert result.artifact["structured_content"] == bundle


def assert_model_tool_schemas_match(
    direct_tools: dict[str, BaseTool],
    gateway_tools: dict[str, BaseTool],
) -> None:
    assert direct_tools.keys() == gateway_tools.keys()
    assert {name: tool_schema(tool) for name, tool in direct_tools.items()} == {
        name: tool_schema(tool) for name, tool in gateway_tools.items()
    }


def tool_schema(tool: BaseTool) -> dict[str, Any]:
    schema = tool.args_schema
    if is_pydantic_schema(schema):
        return schema.model_json_schema()
    assert isinstance(schema, dict)
    return schema


def assert_gateway_read_tools(gateway_tools: dict[str, BaseTool]) -> None:
    assert gateway_tools["get_policy"].invoke({})["policy_id"] == "POL-2001"
    assert "CUS-3001" in str(gateway_tools["get_claim_history"].invoke({}))


def assert_context_tool_routes(
    direct_tools: dict[str, BaseTool],
    gateway_tools: dict[str, BaseTool],
) -> None:
    invocations = {
        "lookup_term": {"term": "benefit"},
        "describe_fields": {"record": "claim"},
        "get_plan": {"plan": "Premium"},
        "get_claim_rules": {},
        "search_reference_data": {"query": "trip delay"},
        "search_documents": {"query": "meal receipt"},
        "read_section": {"document": "claims_manual", "number": 10},
    }
    for name, arguments in invocations.items():
        direct_result = direct_tools[name].invoke(arguments)
        gateway_result = gateway_tools[name].invoke(arguments)
        assert direct_result == gateway_result
        assert set(direct_result) == {"answer", "sources"}
        assert set(direct_result["sources"][0]) == {
            "file",
            "system",
            "owner",
            "reviewed",
        }
    document_results = gateway_tools["search_documents"].invoke(
        {"query": "meal receipt"}
    )["answer"]
    assert document_results[0]["source"]["file"] == "claims_manual.md"
    assert document_results[0]["source"]["document"] == "claims_manual"
    section = gateway_tools["read_section"].invoke(
        {"document": "claims_manual", "number": 10}
    )["answer"]
    assert section == {
        "document": "claims_manual",
        "section_id": "10",
        "title": "Meals",
        "content": "$35 per traveler",
        "source": {
            "file": "claims_manual.md",
            "system": "Claims manual",
            "owner": "Claims Operations",
            "reviewed": "2026-01-01",
        },
    }


def assert_context_refusal_routes(
    direct_tools: dict[str, BaseTool],
    gateway_tools: dict[str, BaseTool],
    refusal_enabled: list[bool],
    logged_steps: list[dict[str, Any]],
) -> None:
    refusal_enabled[0] = True
    for route_tools in (direct_tools, gateway_tools):
        with pytest.raises(ToolException, match="context refused this term"):
            route_tools["lookup_term"].invoke({"term": "blocked"})
    refusals = [step for step in logged_steps if step["kind"] == "refusal"]
    assert len(refusals) == 2
    assert all(step["details"]["source"] == "context" for step in refusals)
    assert all(
        "context refused this term" in step["details"]["reason"] for step in refusals
    )
    assert all(step["details"]["operation"] == "lookup_term" for step in refusals)
    assert not any(step["details"]["source"] == "gateway" for step in refusals)


def assert_gateway_payout_tool(gateway_tools: dict[str, BaseTool]) -> None:
    response = gateway_tools["calculate_payout"].invoke(
        {
            "benefit": "trip_delay",
            "lines": [
                {
                    "description": "Hotel",
                    "amount": 120,
                    "quantity": 1,
                    "kind": "hotel",
                    "nights": 1,
                    "hours": 0,
                }
            ],
            "paid_by_others": 0,
        }
    )
    assert response["payout"] == 120


def assert_gateway_decision_tool(
    gateway_tools: dict[str, BaseTool],
    accepted_decisions: list[tuple[dict[str, Any], dict[str, Any]]],
    decision_calls: list[dict[str, Any]],
) -> None:
    tool = gateway_tools["record_decision"]
    properties = tool_schema(tool)["properties"]
    assert set(properties) == {"decision", "payout", "reason", "section"}
    result = tool.invoke(
        {
            "decision": "approve",
            "payout": 120,
            "reason": "Covered by the policy.",
            "section": "Section 3",
        }
    )
    accepted_bundle = accepted_decisions[-1][1]
    assert json.loads(result) == accepted_bundle
    assert accepted_bundle["claim"]["status"] == "in_progress"
    assert accepted_bundle["decision"]["decision"] == "approve"
    assert accepted_bundle["decision"]["decided_by"] == (
        "UnderCover Agent (configuration version 1)"
    )
    assert accepted_bundle["decision"]["section"] == "Section 3"
    assert accepted_bundle["decision"]["calculation"]["payout"] == 120
    assert accepted_bundle["decision"]["decided_at"].endswith("+00:00")
    assert accepted_bundle["decision"]["manual_version"] == "2026.2"
    assert accepted_decisions[-1][0] == {
        "decision": "approve",
        "payout": 120.0,
        "reason": "Covered by the policy.",
        "section": "Section 3",
    }
    assert decision_calls[-1] == {
        "claim_id": "CLM-1001",
        "decision": "approve",
        "payout": 120.0,
        "reason": "Covered by the policy.",
        "section": "Section 3",
        "decided_by": "UnderCover Agent (configuration version 1)",
    }


def assert_app_refusal(
    gateway_tools: dict[str, BaseTool], refusal_enabled: list[bool]
) -> None:
    refusal_enabled[0] = True
    with pytest.raises(ToolException, match="application refused") as app_error:
        gateway_tools["get_policy"].invoke({})
    assert gateway.is_gateway_tool_refusal(app_error.value)


def test_gateway_transport_error_is_not_classified_as_app_refusal(monkeypatch):
    context = context_fixture("CLM-1001")
    tool = SimpleNamespace(name="undercover-app___get_policy")

    def raise_gateway_error(*_args):
        raise McpError(ErrorData(code=-32000, message="Gateway unavailable"))

    monkeypatch.setattr(tools, "invoke_gateway_tool", raise_gateway_error)
    monkeypatch.setattr(tools, "log_step", lambda *_args: None)
    with pytest.raises(ToolException, match="Gateway unavailable") as caught:
        tools._invoke_gateway(tool, context, "get_policy", {})
    assert gateway.is_gateway_tool_refusal(caught.value) is False


def test_context_transport_failures_are_not_misreported_as_refusals(monkeypatch):
    context = context_fixture("CLM-1001")
    logged_steps = []
    monkeypatch.setattr(
        tools,
        "log_step",
        lambda claim_id, kind, message, details: logged_steps.append(
            {
                "claim_id": claim_id,
                "kind": kind,
                "message": message,
                "details": details,
            }
        ),
    )

    def fail_transport(*_args):
        raise httpx.ConnectError("context endpoint is unavailable")

    monkeypatch.setattr(tools, "invoke_gateway_tool", fail_transport)
    with pytest.raises(httpx.ConnectError, match="context endpoint is unavailable"):
        tools._invoke_gateway(
            SimpleNamespace(name="undercover-context___lookup_term"),
            context,
            "lookup_term",
            {"term": "benefit"},
        )

    monkeypatch.setattr(
        direct.DirectRoute,
        "invoke_context_tool",
        lambda *_args: fail_transport(),
    )
    monkeypatch.setattr(tools, "gateway_tools", lambda: [])
    local_tool = next(
        item for item in tools.route_tools(context) if item.name == "lookup_term"
    )
    with pytest.raises(httpx.ConnectError, match="context endpoint is unavailable"):
        local_tool.invoke({"term": "benefit"})
    assert [step["kind"] for step in logged_steps] == ["tool"]


def context_fixture(claim_id: str, configuration_version: int = 1) -> ClaimContext:
    policy_id, customer_id = {
        "CLM-1001": ("POL-2001", "CUS-3001"),
        "CLM-1002": ("POL-2002", "CUS-3002"),
    }[claim_id]
    return ClaimContext(
        claim_id,
        policy_id,
        customer_id,
        configuration_version=configuration_version,
        run_kind="dispatch",
    )
