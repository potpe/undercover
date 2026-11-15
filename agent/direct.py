"""Direct route for model-facing app operations and local context MCP."""

import os
from typing import Any

from agent.api import backend, canonical_claim, get_claim, log_activity
from agent.claim_context import ClaimContext


class DirectRoute:
    """Claim-scoped access to app operations through the API-key backend."""

    def __init__(self, claim_context: ClaimContext) -> None:
        self.claim_context = claim_context
        self.claim_id = claim_context.claim_id

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

    def search_documents(self, document: str, query: str) -> Any:
        with backend() as client:
            response = client.post(
                f"/api/claims/{self.claim_id}/document-search",
                json={"document": document, "query": query},
            )
        response.raise_for_status()
        return response.json()

    def read_section(self, document: str, section_id: str) -> Any:
        with backend() as client:
            response = client.post(
                f"/api/claims/{self.claim_id}/document-section",
                json={"document": document, "section_id": section_id},
            )
        response.raise_for_status()
        return response.json()

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
