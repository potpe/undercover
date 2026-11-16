"""Direct API client for bookkeeping and non-model claim operations."""

import json
import os
from typing import Any

import httpx
from pydantic import TypeAdapter

from backend.models import ActivityStepRequest

ACTIVITY_STEP_ADAPTER = TypeAdapter(ActivityStepRequest)


def backend() -> httpx.Client:
    """Build an API-key client using the current process environment."""
    key = os.getenv("UNDERCOVER_API_KEY")
    headers = {"X-API-Key": key} if key else {}
    return httpx.Client(
        base_url=os.getenv("UNDERCOVER_API", "http://localhost:8000"),
        timeout=120,
        headers=headers,
    )


def canonical_claim(bundle: dict[str, Any]) -> str:
    """Serialize stable claim, policy, customer and resolved coverage evidence."""
    claim = dict(bundle["claim"])
    claim.pop("status", None)
    evidence = {
        "claim": claim,
        "policy": bundle["policy"],
        "customer": bundle["customer"],
        "plan": bundle["plan"],
        "add_ons": bundle["add_ons"],
    }
    return json.dumps(
        evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def get_claim(claim_id: str) -> dict[str, Any]:
    """Read a complete claim bundle through the direct app API."""
    with backend() as client:
        response = client.get(f"/api/claims/{claim_id}")
    response.raise_for_status()
    return response.json()


def log_activity(claim_id: str, message: str) -> None:
    """Record non-model bookkeeping through the direct app API."""
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/activity", json={"message": message}
        )
    response.raise_for_status()


def log_step(claim_id: str, kind: str, message: str, details: dict[str, Any]) -> None:
    """Record a typed claim-agent step through the direct app API."""
    step = ACTIVITY_STEP_ADAPTER.validate_python(
        {"kind": kind, "message": message, "details": details}
    )
    with backend() as client:
        response = client.post(
            f"/api/claims/{claim_id}/steps",
            json=step.model_dump(mode="json"),
        )
    response.raise_for_status()


def hold_gateway_refusal(claim_id: str, reason: str) -> dict[str, Any]:
    """Request the backend's Gateway-refusal sign-off hold."""
    with backend() as client:
        response = client.post(
            f"/api/internal/claims/{claim_id}/gateway-hold",
            json={"reason": reason},
        )
    response.raise_for_status()
    return response.json()
