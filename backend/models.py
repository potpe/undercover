from typing import Any, Literal

from pydantic import BaseModel

ClaimStatus = Literal["waiting", "in_progress", "waiting_for_sign_off", "decided"]
DecisionKind = Literal["approve", "decline", "refer"]


class ClaimSummary(BaseModel):
    claim_id: str
    customer_name: str
    claim_type: str
    summary: str
    amount_claimed: float
    status: ClaimStatus
    decision: DecisionKind | None
    payout: float | None


class Decision(BaseModel):
    decision: DecisionKind
    payout: float
    reason: str
    policy_section: str


class Activity(BaseModel):
    id: int
    message: str
    created_at: str


class ClaimBundle(BaseModel):
    claim: dict[str, Any]
    policy: dict[str, Any]
    customer: dict[str, Any]
    plan: dict[str, Any]
    add_ons: list[dict[str, Any]]
    decision: Decision | None
    paid_amount: float | None
    signed_off_by: str | None
    activity: list[Activity]


class PolicyWording(BaseModel):
    version: str
    content: str
