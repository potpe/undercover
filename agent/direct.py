"""Direct route for model-facing app operations and local context MCP."""

import os
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from agent.api import backend, canonical_claim, get_claim, log_activity
from agent.claim_context import ClaimContext
from agent.gateway import invoke_gateway_tool
from agent.mcp_sync import run_mcp


class DirectRoute:
    """Claim-scoped app API and unsigned context MCP access."""

    def __init__(self, claim_context: ClaimContext) -> None:
        self.claim_context = claim_context
        self.claim_id = claim_context.claim_id
        self.context_url = claim_context.context_url or context_url()
        self._context_tools: dict[str, Any] | None = None

    def get_claim(self) -> str:
        result = canonical_claim(get_claim(self.claim_id))
        log_activity(self.claim_id, "Read claim, policy and customer claim history")
        return result

    def get_policy(self) -> Any:
        with backend() as client:
            response = client.get(f"/api/policies/{self.claim_context.policy_id}")
        response.raise_for_status()
        return response.json()

    def get_claim_history(self) -> Any:
        with backend() as client:
            response = client.get(
                f"/api/customers/{self.claim_context.customer_id}/history"
            )
        response.raise_for_status()
        return response.json()

    def invoke_context_tool(self, operation: str, arguments: dict[str, Any]) -> Any:
        if self._context_tools is None:
            client = MultiServerMCPClient(
                {
                    "undercover-context": {
                        "transport": "streamable_http",
                        "url": self.context_url,
                    }
                },
                tool_name_prefix=False,
                handle_tool_errors=False,
            )
            self._context_tools = {
                tool.name: tool for tool in run_mcp(client.get_tools())
            }
        tool = self._context_tools.get(operation)
        if tool is None:
            raise RuntimeError(f"Context service is missing operation {operation}")
        return invoke_gateway_tool(tool, arguments)

    def calculate_payout(
        self, benefit: str, lines: list[dict[str, Any]], paid_by_others: float
    ) -> Any:
        with backend() as client:
            response = client.post(
                f"/api/claims/{self.claim_id}/payout",
                json={
                    "benefit": benefit,
                    "lines": lines,
                    "paid_by_others": paid_by_others,
                },
            )
        response.raise_for_status()
        return response.json()

    def record_decision(self, payload: dict[str, Any]) -> Any:
        with backend() as client:
            response = client.post(
                f"/api/claims/{self.claim_id}/decision", json=payload
            )
        response.raise_for_status()
        return response.json()


def context_url() -> str:
    """Return the local context MCP URL after configuration is loaded."""
    return os.getenv("UNDERCOVER_CONTEXT", "http://127.0.0.1:8006/mcp")
