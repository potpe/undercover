"""Customer-scoped AgentCore memory helpers for claim graphs."""

import json
import logging
import os
from typing import Any

import boto3
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import ToolException
from langgraph.store.base import BaseStore
from langgraph_checkpoint_aws import AgentCoreMemoryStore

from agent.claim_context import ClaimContext

logger = logging.getLogger(__name__)


def build_memory_store(context: ClaimContext) -> BaseStore | None:
    """Build the optional AgentCore store when memory is enabled."""
    memory_id = os.getenv("UNDERCOVER_MEMORY_ID")
    if not memory_id or context.run_kind == "evaluation":
        return None

    return AgentCoreMemoryStore(
        memory_id=memory_id,
        region_name=boto3.Session().region_name,
    )


def _item_namespaces(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    paths: list[str] = []
    for namespace in value:
        if isinstance(namespace, str):
            paths.append("/" + namespace.strip("/"))
        elif isinstance(namespace, (list, tuple)) and all(
            isinstance(label, str) for label in namespace
        ):
            paths.append("/" + "/".join(namespace))
    return paths


def _belongs_to_customer(path: str, customer_id: str) -> bool:
    customer_path = f"/customers/{customer_id}"
    return path == customer_path or path.startswith(customer_path + "/")


def search_customer_memory(
    store: BaseStore, context: ClaimContext, query: str
) -> list[str]:
    """Search only this customer's prior claims and hide this claim's memories."""
    customer_prefix = ("customers", context.customer_id)
    current_claim_path = f"/customers/{context.customer_id}/claims/{context.claim_id}"
    try:
        results = store.search(customer_prefix, query=query, limit=5)
        messages: list[str] = []
        for item in results:
            value = item.value
            namespaces = _item_namespaces(value.get("namespaces"))
            if not namespaces or any(
                not _belongs_to_customer(path, context.customer_id)
                or path == current_claim_path
                or path.startswith(current_claim_path + "/")
                for path in namespaces
            ):
                continue
            content = value.get("content")
            if isinstance(content, str):
                messages.append(content)
        return messages
    except Exception as error:
        raise ToolException(f"Customer memory search was refused: {error}") from error


def save_claim_memory(
    store: BaseStore, context: ClaimContext, bundle: dict[str, Any]
) -> list[str] | None:
    """Save the accepted decision's minimal summary and outcome."""
    decision = bundle.get("decision")
    if (
        context.run_kind == "evaluation"
        or bundle.get("evaluation_run_id") is not None
        or not isinstance(decision, dict)
        or decision.get("decision") not in {"approve", "decline", "refer"}
        or decision.get("decided_by") == "Security check"
    ):
        return None

    claim = bundle.get("claim", {})
    if not isinstance(claim, dict):
        return None
    summary_fields = {
        field: claim[field]
        for field in (
            "summary",
            "claim_type",
            "incident_date",
            "incident_location",
            "items",
        )
        if field in claim
    }
    decision_fields = {
        field: decision[field]
        for field in ("decision", "payout", "section", "reason")
        if field in decision
    }
    summary_json = json.dumps(summary_fields)
    decision_json = json.dumps(decision_fields)
    human_message = HumanMessage(content=summary_json)
    ai_message = AIMessage(content=decision_json)
    serialized = [summary_json, decision_json]

    try:
        namespace = (context.customer_id, context.claim_id)
        store.put(namespace, "claim_summary", {"message": human_message})
        store.put(namespace, "claim_decision", {"message": ai_message})
    except Exception:
        logger.warning("Could not save customer memory for claim %s", context.claim_id)
        return None
    return serialized
