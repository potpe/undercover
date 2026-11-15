"""FastMCP contract and sequential Lambda Function URL tests."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from context.server import build_server, handler

EXPECTED_TOOLS = {
    "lookup_term",
    "describe_fields",
    "get_plan",
    "get_claim_rules",
    "search_reference_data",
    "search_documents",
    "read_section",
}


def test_exact_read_only_tool_contract() -> None:
    def list_tools_in_worker() -> Any:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(build_server().list_tools())
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    with ThreadPoolExecutor(max_workers=1) as executor:
        tools = executor.submit(list_tools_in_worker).result()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    for tool in tools:
        assert tool.description
        assert "\n" not in tool.description
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.inputSchema["type"] == "object"
        assert set(tool.inputSchema["properties"]) == set(
            tool.inputSchema.get("required", [])
        )


def test_dns_rebinding_protection_is_disabled_only_for_lambda() -> None:
    local = build_server().settings.transport_security
    lambda_settings = build_server(lambda_transport=True).settings.transport_security
    assert local is not None
    assert local.enable_dns_rebinding_protection is True
    assert lambda_settings is not None
    assert lambda_settings.enable_dns_rebinding_protection is False


def _function_url_event(request: dict[str, Any], request_id: str) -> dict[str, Any]:
    return {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/mcp",
        "rawQueryString": "",
        "headers": {
            "accept": "application/json, text/event-stream",
            "content-type": "application/json",
            "host": "abc123.lambda-url.us-east-1.on.aws",
        },
        "requestContext": {
            "accountId": "123456789012",
            "apiId": "abc123",
            "domainName": "abc123.lambda-url.us-east-1.on.aws",
            "domainPrefix": "abc123",
            "http": {
                "method": "POST",
                "path": "/mcp",
                "protocol": "HTTP/1.1",
                "sourceIp": "203.0.113.1",
                "userAgent": "local-test",
            },
            "requestId": request_id,
            "routeKey": "$default",
            "stage": "$default",
            "time": "15/Nov/2026:00:00:00 +0000",
            "timeEpoch": 1794700800000,
        },
        "body": json.dumps(request),
        "isBase64Encoded": False,
    }


def _invoke(request: dict[str, Any], request_id: str) -> dict[str, Any]:
    return handler(_function_url_event(request, request_id), {})


def test_two_sequential_function_url_events_succeed() -> None:
    listed = _invoke(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {},
        },
        "context-list",
    )
    assert listed["statusCode"] == 200
    list_body = json.loads(listed["body"])
    assert {tool["name"] for tool in list_body["result"]["tools"]} == EXPECTED_TOOLS

    called = _invoke(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "lookup_term",
                "arguments": {"term": "Carrier"},
            },
        },
        "context-call",
    )
    assert called["statusCode"] == 200
    call_body = json.loads(called["body"])
    structured = call_body["result"]["structuredContent"]
    assert structured["answer"]["found"] is True
    assert structured["sources"][0]["file"] == "glossary.yaml"
