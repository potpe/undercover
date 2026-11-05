from typing import Any, Literal

from pydantic import BaseModel, Field

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


class DecisionRequest(BaseModel):
    decision: DecisionKind
    payout: float = Field(ge=0)
    reason: str = Field(min_length=1)
    policy_section: str = Field(min_length=1)


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0)


class SignOffRequest(BaseModel):
    handler_id: str = Field(min_length=1)


class RunRequest(BaseModel):
    thread_id: str = Field(min_length=1)


class RunResponse(BaseModel):
    run_id: int
    claim_id: str
    status: Literal["in_progress"]


class QueueRunRequest(BaseModel):
    thread_id: str = Field(min_length=1)
    claim_cap: int = Field(ge=1, le=25)


class RunPlanRequest(BaseModel):
    claim_ids: list[str] = Field(min_length=1)
    model_name: str = Field(min_length=1)


class FinishRunRequest(BaseModel):
    summary: str = Field(min_length=1)


class WorkRequest(BaseModel):
    claim_id: str = Field(min_length=1)


class WorkOutcome(BaseModel):
    run_id: int
    claim_id: str
    status: Literal["retry", "done", "left_for_handler"]
    attempts: int
    result: str


class RunTodo(BaseModel):
    claim_id: str
    position: int
    status: Literal["to_do", "in_progress", "done", "left_for_handler"]
    attempts: int
    result: str | None
    error: str | None


class AgentRun(BaseModel):
    run_id: int
    thread_id: str
    claim_cap: int
    target_claim_id: str | None
    model_name: str | None
    status: Literal["in_progress", "completed", "failed"]
    summary: str | None
    created_at: str
    completed_at: str | None
    todos: list[RunTodo]


class ActivityRequest(BaseModel):
    message: str = Field(min_length=1)


class Instructions(BaseModel):
    queue: str
    claim: str


class DocumentSearchRequest(BaseModel):
    document: Literal["policy_wording", "claims_manual"]
    query: str = Field(min_length=1)


class DocumentSectionRequest(BaseModel):
    document: Literal["policy_wording", "claims_manual"]
    section_id: str = Field(min_length=1)


class DocumentSectionSummary(BaseModel):
    document: Literal["policy_wording", "claims_manual"]
    section_id: str
    title: str
    snippet: str


class DocumentSectionContent(BaseModel):
    document: Literal["policy_wording", "claims_manual"]
    section_id: str
    title: str
    content: str


class DocumentRead(BaseModel):
    document: Literal["policy_wording", "claims_manual"]
    section_id: str
    title: str


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
    documents_read: list[DocumentRead]
