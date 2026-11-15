"""Signed AgentCore Gateway route."""

import json
import os
from typing import Any
from uuid import uuid4

import boto3
import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from langchain_core.messages import ToolCall, ToolMessage
from langchain_core.tools import ToolException
from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.mcp_sync import run_mcp

APP_TOOL_PREFIX = "undercover-app___"


class SigV4HTTPAuth(httpx.Auth):
    """Sign every outgoing Gateway HTTP request with frozen CLI credentials."""

    requires_request_body = True

    def __init__(self, credentials: Any, region: str) -> None:
        self.credentials = credentials
        self.region = region

    def auth_flow(self, request: httpx.Request):
        credentials = self.credentials
        freeze = getattr(credentials, "get_frozen_credentials", None)
        if freeze is not None:
            credentials = freeze()
        signed = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content,
            headers=dict(request.headers),
        )
        SigV4Auth(credentials, "bedrock-agentcore", self.region).add_auth(signed)
        for key, value in signed.headers.items():
            request.headers[key] = value
        yield request


def signed_auth() -> SigV4HTTPAuth:
    """Create a signer from the active AWS CLI session."""
    session = boto3.Session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS CLI credentials are unavailable")
    if not session.region_name:
        raise RuntimeError("AWS CLI region is unavailable")
    return SigV4HTTPAuth(credentials, session.region_name)


def gateway_tools() -> list[Any]:
    """Load stateless Gateway MCP tools with errors preserved."""
    url = os.getenv("UNDERCOVER_GATEWAY_URL")
    if not url:
        return []
    client = MultiServerMCPClient(
        {
            "undercover-app": {
                "transport": "streamable_http",
                "url": url,
                "auth": signed_auth(),
            }
        },
        tool_name_prefix=False,
        handle_tool_errors=False,
    )
    return run_mcp(client.get_tools())


def invoke_gateway_tool(tool: Any, arguments: dict[str, Any]) -> Any:
    """Invoke one loaded Gateway tool synchronously."""
    call: ToolCall = {
        "name": tool.name,
        "args": arguments,
        "id": str(uuid4()),
        "type": "tool_call",
    }
    result = run_mcp(tool.ainvoke(call))
    if not isinstance(result, ToolMessage):
        raise TypeError("Gateway tools must return a LangChain ToolMessage")
    return _normalize_tool_message(result)


class GatewayToolMessageRefusal(ToolException):
    """An application refusal returned as an errored Gateway ToolMessage."""

    def __init__(self, content: Any) -> None:
        self.tool_content = content
        super().__init__(str(content))


def _normalize_tool_message(message: ToolMessage) -> Any:
    if message.status == "error":
        raise GatewayToolMessageRefusal(message.content)
    if isinstance(message.artifact, dict):
        structured_content = message.artifact.get("structured_content")
        if structured_content is not None:
            return structured_content
    return _parse_tool_content(message.content)


def _parse_tool_content(content: Any) -> Any:
    if isinstance(content, dict):
        text = content.get("text")
        return _parse_json_text(text) if isinstance(text, str) else content
    if isinstance(content, list):
        texts = [
            item["text"]
            for item in content
            if isinstance(item, dict) and isinstance(item.get("text"), str)
        ]
        if texts:
            return _parse_json_text("\n".join(texts))
        return content
    if isinstance(content, str):
        return _parse_json_text(content)
    return content


def _parse_json_text(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def is_gateway_tool_refusal(error: BaseException) -> bool:
    """Recognize MCP isError ToolExceptions without swallowing transport errors."""
    return isinstance(error, ToolException) and hasattr(error, "tool_content")


def pay_gateway(claim_id: str, amount: float) -> Any:
    """Record payment through the app's Gateway tool."""
    tool = next(
        item for item in gateway_tools() if item.name == f"{APP_TOOL_PREFIX}pay_claim"
    )
    return invoke_gateway_tool(tool, {"claim_id": claim_id, "amount": amount})
