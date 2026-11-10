from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ClaimStatus = Literal[
    "waiting", "in_progress", "waiting_for_sign_off", "decided", "left_for_handler"
]
DecisionKind = Literal["approve", "decline", "refer"]
Benefit = Literal[
    "trip_cancellation",
    "trip_interruption",
    "trip_delay",
    "baggage_delay",
    "baggage",
    "passport",
    "emergency_medical",
]
PayoutLineKind = Literal[
    "item", "electronics", "cash", "expense", "hotel", "meals_without_receipts"
]


class PayoutLine(BaseModel):
    description: str = Field(min_length=1)
    amount: float = Field(ge=0)
    quantity: int = Field(ge=1)
    kind: PayoutLineKind
    nights: int = Field(ge=0)
    hours: float = Field(ge=0)


class PayoutRequest(BaseModel):
    benefit: Benefit
    lines: list[PayoutLine]
    paid_by_others: float = Field(ge=0)


class CalculationStep(BaseModel):
    step: str
    amount: float


class PayoutResponse(BaseModel):
    calculation: list[CalculationStep]
    payout: float
    needs_sign_off: bool


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
    claim_id: str
    status: Literal["in_progress"]


class DispatchRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)


class CreateRunRequest(DispatchRequest):
    model_name: str = Field(min_length=1)
    reasoning_effort: str | None


class CompleteWorkRequest(BaseModel):
    result: str = Field(min_length=1)


class FailureRequest(BaseModel):
    error: str = Field(min_length=1)


class WorkOutcome(BaseModel):
    run_id: int
    claim_id: str
    status: Literal["retry", "done", "left_for_handler", "in_progress"]
    attempts: int
    result: str


class WorkContext(BaseModel):
    run_id: int
    claim_id: str
    run_kind: Literal["dispatch", "evaluation"]
    thread_id: str | None
    status: Literal["in_progress", "done", "left_for_handler"]
    attempts: int
    result: str | None


class HeldPayout(BaseModel):
    claim_id: str
    payout: float


class DispatchSummary(BaseModel):
    approved: int
    declined: int
    referred: int
    waiting_for_sign_off: list[HeldPayout]
    left_for_handler: list[str]
    in_progress: list[str]
    error: str | None


class RunClaim(BaseModel):
    claim_id: str
    position: int
    status: Literal["waiting", "in_progress", "done", "left_for_handler"]
    attempts: int
    result: str | None
    error: str | None
    started_at: str | None
    finished_at: str | None
    model_cost: float
    active_seconds: float


class RunRecord(BaseModel):
    run_id: int
    kind: Literal["dispatch", "evaluation"]
    concurrency: int
    status: Literal["in_progress", "completed", "stopped"]
    model_name: str
    reasoning_effort: str | None
    evaluation_set: str | None
    tiny: bool | None
    summary: DispatchSummary | None
    created_at: str
    completed_at: str | None
    stopped_at: str | None
    claims: list[RunClaim]
    model_cost: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    active_seconds: float
    wall_seconds: float


class StoppedRuns(BaseModel):
    stopped: int


class ModelCallRequest(BaseModel):
    run_id: int | None
    model_name: str = Field(min_length=1)
    reasoning_effort: str | None
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)


class WorkPeriodRequest(BaseModel):
    run_id: int | None
    thread_id: str = Field(min_length=1)
    started_at: datetime
    finished_at: datetime
    seconds: float = Field(ge=0)


class ActivityRequest(BaseModel):
    message: str = Field(min_length=1)


class Instructions(BaseModel):
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
    signed_off_by_name: str | None
    signed_off_at: str | None
    paid_at: str | None
    agent_thread_id: str | None
    work_run_id: int | None
    failed_attempts: int
    evaluation_run_id: int | None
    activity: list[Activity]
    documents_read: list[DocumentRead]
