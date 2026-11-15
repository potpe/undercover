from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

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
    decided_by: str


class DecisionRequest(BaseModel):
    decision: DecisionKind
    payout: float = Field(ge=0)
    reason: str = Field(min_length=1)
    policy_section: str = Field(min_length=1)
    decided_by: str


class PaymentRequest(BaseModel):
    amount: float = Field(gt=0)


class GatewayHoldRequest(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Reason must not be blank")
        return value.strip()


class Handler(BaseModel):
    handler_id: str
    name: str
    role: str
    email: str
    can_approve_over_limit: bool


class ScoreFloors(BaseModel):
    accuracy: float = Field(ge=0, le=1)
    grade: float = Field(ge=0, le=3)


class MeasureSettings(BaseModel):
    accuracy: float = Field(ge=0)
    grade: float = Field(ge=0)
    cost_per_claim: float = Field(ge=0)
    seconds_per_claim: float = Field(ge=0)


class SettingsUpdate(BaseModel):
    approval_limit: float = Field(gt=0)
    objectives: ScoreFloors
    noise: MeasureSettings
    tolerance: MeasureSettings
    spending_limit: float = Field(gt=0)
    evaluation_concurrency: int = Field(ge=1, le=25)


class AllowedModel(BaseModel):
    model_name: str
    reasoning_efforts: list[str]
    input_price: float = Field(ge=0)
    output_price: float = Field(ge=0)


class PlatformSettings(SettingsUpdate):
    models_allowed: list[AllowedModel]


class SettingChange(BaseModel):
    key: str
    old_value: Any
    new_value: Any
    changed_by: str
    changed_at: str


class DiffLine(BaseModel):
    kind: Literal["context", "added", "removed"]
    text: str


class ConfigurationVersion(BaseModel):
    version: int
    instructions: str
    model_name: str
    reasoning_effort: str
    reason: str
    proposed_by: str
    approved_by: str | None
    proposed_at: str
    approved_at: str | None
    diff: list[DiffLine]
    held_out_scores: dict[str, float | None] | None


class ConfigurationHistory(BaseModel):
    current_version: int
    versions: list[ConfigurationVersion]


class RecipeVersion(BaseModel):
    version: int
    recipe: str
    reason: str
    proposed_by: str
    approved_by: str | None
    proposed_at: str
    approved_at: str | None
    diff: list[DiffLine]


class RecipeHistory(BaseModel):
    current_version: int
    versions: list[RecipeVersion]


class RoundRecord(BaseModel):
    round_id: int
    thread_id: str
    status: Literal["open", "finished", "stopped"]
    stage: str
    recipe_version: int
    settings: dict[str, Any]
    starting_version: int
    candidate_version: int | None
    held_out_before: dict[str, float | None] | None
    held_out_after: dict[str, float | None] | None
    verdict: Literal["kept", "rolled_back"] | None
    verdict_reason: str | None
    finish_reason: str | None
    created_by: str
    created_at: str
    finished_at: str | None
    stopped_at: str | None
    progress: int
    cost: float
    proposals: list[dict[str, Any]]


class StartRoundRequest(BaseModel):
    handler_id: str


class RoundStageRequest(BaseModel):
    stage: Literal["baseline", "working", "evaluating", "verdict"]
    progress: int = Field(ge=0, le=3)


class ConfigurationProposal(BaseModel):
    instructions: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RecipeProposal(BaseModel):
    recipe: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ProposalRequest(BaseModel):
    payload: ConfigurationProposal | RecipeProposal


class ProposalDecision(BaseModel):
    proposal_id: int
    decision: Literal["approve", "reject"]
    reason: str


class InternalProposalDecision(ProposalDecision):
    handler_id: str


class VerdictRequest(BaseModel):
    failure: str | None


class ImproverCallRequest(BaseModel):
    call_id: str
    model_name: str
    reasoning_effort: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost: float = Field(ge=0)


class RunRequest(BaseModel):
    thread_id: str = Field(min_length=1)


class RunResponse(BaseModel):
    claim_id: str
    status: Literal["in_progress"]


class DispatchRequest(BaseModel):
    concurrency: int = Field(ge=1, le=25)


class CreateRunRequest(DispatchRequest):
    configuration_version: int | None
    model_name: str = Field(min_length=1)
    reasoning_effort: str
    round_id: int | None
    round_purpose: Literal["baseline", "candidate"] | None


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
    configuration_version: int
    round_id: int | None
    round_purpose: Literal["baseline", "candidate"] | None
    status: Literal["in_progress", "completed", "stopped"]
    model_name: str
    reasoning_effort: str
    evaluation_set: str | None
    tiny: bool | None
    summary: DispatchSummary | None
    created_at: str
    completed_at: str | None
    stopped_at: str | None
    claims: Sequence[RunClaim]
    model_cost: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    active_seconds: float
    wall_seconds: float


class EvaluationRequest(DispatchRequest):
    evaluation_set: Literal["evaluation", "held_out"]
    tiny: bool


class CreateEvaluationRequest(CreateRunRequest):
    evaluation_set: Literal["evaluation", "held_out"]
    tiny: bool


class EvaluationMeasures(BaseModel):
    grade: float | None
    judge_cost: float
    decision_accuracy: float
    accuracy: float
    cost_of_mistakes: float
    straight_through: float
    model_cost: float
    model_cost_per_claim: float
    seconds_per_claim: float
    slowest_claim: float
    seconds: float


class EvaluationClaim(RunClaim):
    grade: float | None
    letter: Literal["F", "C", "B", "A"] | None
    same_grounds: float | None
    same_section: float | None
    judge_cost: float
    handler_reason: str
    handler_reference: str
    agent_section: str
    source_claim_id: str
    handler_decision: DecisionKind
    handler_payout: float
    agent_decision: DecisionKind
    agent_payout: float
    agent_reason: str
    decision_match: bool
    right: bool
    mistake_cost: float


class EvaluationRecord(RunRecord):
    measures: EvaluationMeasures
    claim_comparisons: Sequence[EvaluationClaim]


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
    sign_off_required: bool
    gateway_refused: bool
    gateway_refusal_reason: str | None
    activity: list[Activity]
    documents_read: list[DocumentRead]


class PolicyRecord(BaseModel):
    policy_id: str
    customer_id: str
    plan: str
    add_ons: list[str]
    purchased_on: str
    sold_through: str
    trip: dict[str, Any]
    travelers: list[str]
    status: str


class PreviousClaim(BaseModel):
    claim_id: str
    filed_on: str
    incident_date: str
    claim_type: str
    summary: str
    outcome: str
    amount_paid: float
