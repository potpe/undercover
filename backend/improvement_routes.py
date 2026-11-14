"""Handler and service contracts for backend-owned improvement rounds."""

from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from backend import agent_service
from backend.auth import agent_key, current_handler, senior_handler
from backend.database import connect, recipe_versions_table
from backend.improvement import (
    create_round,
    finish_round,
    get_round,
    list_rounds,
    record_improver_usage,
    recover_rounds,
    stop_round,
    update_round,
    verdict,
)
from backend.improvement_budget import check_budget
from backend.improvement_proposals import apply_proposal, decide_proposal, save_proposal
from backend.improver_results import read_results
from backend.models import (
    ConfigurationProposal,
    FailureRequest,
    ImproverCallRequest,
    InternalProposalDecision,
    ProposalDecision,
    ProposalRequest,
    RoundRecord,
    RoundStageRequest,
    StartRoundRequest,
    VerdictRequest,
)
from backend.version_scores import average_scores

router = APIRouter()
Senior = Annotated[dict[str, Any], Depends(senior_handler)]
SignedIn = Annotated[dict[str, Any], Depends(current_handler)]


def forward(path: str, body: dict) -> dict:
    try:
        response = agent_service.agent_post(path, body)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=error.response.status_code,
            detail=error.response.json()["detail"],
        ) from error
    except httpx.HTTPError as error:
        raise HTTPException(
            status_code=503, detail="The agent service is not running"
        ) from error


@router.post("/api/rounds", operation_id="start_improvement_round", status_code=202)
def start(handler: Senior) -> RoundRecord:
    """Ask the agent service to start one improvement round."""
    return RoundRecord(**forward("/rounds", {"handler_id": handler["handler_id"]}))


@router.get("/api/rounds/{round_id}", operation_id="get_improvement_round")
def detail(round_id: int, _handler: SignedIn) -> RoundRecord:
    """Return a round's progress, proposals, verdict and cost."""
    return RoundRecord(**get_round(round_id))


@router.post("/api/rounds/{round_id}/stop", operation_id="stop_improvement_round")
def stop(round_id: int, _handler: Senior) -> RoundRecord:
    """Stop a round and refuse subsequent work recorded for it."""
    return RoundRecord(**stop_round(round_id))


@router.post(
    "/api/rounds/{round_id}/decision",
    operation_id="decide_improvement_proposal",
    status_code=202,
)
def decision(round_id: int, request: ProposalDecision, handler: Senior) -> RoundRecord:
    """Forward an authorized proposal decision without recording it locally."""
    return RoundRecord(
        **forward(
            f"/rounds/{round_id}/decision",
            {**request.model_dump(), "handler_id": handler["handler_id"]},
        )
    )


@router.post(
    "/api/internal/rounds",
    operation_id="create_improvement_round",
    status_code=201,
    dependencies=[Depends(agent_key)],
)
def internal_create(request: StartRoundRequest) -> RoundRecord:
    """Create the service's round with an immutable settings snapshot."""
    return RoundRecord(**create_round(request.handler_id))


@router.post(
    "/api/internal/rounds/recover",
    operation_id="recover_improvement_rounds",
    dependencies=[Depends(agent_key)],
)
def internal_recover() -> dict[str, Any]:
    """Stop interrupted work but preserve durable approval waits."""
    recover_rounds()
    return {"rounds": [item for item in list_rounds() if item["status"] == "open"]}


@router.get(
    "/api/internal/rounds/{round_id}",
    operation_id="get_service_round",
    dependencies=[Depends(agent_key)],
)
def internal_detail(round_id: int) -> RoundRecord:
    """Return the stored round for service orchestration."""
    return RoundRecord(**get_round(round_id))


@router.get(
    "/api/internal/rounds/{round_id}/recipe",
    operation_id="get_round_recipe",
    dependencies=[Depends(agent_key)],
)
def internal_recipe(round_id: int) -> dict[str, Any]:
    """Return the recipe version frozen for this round."""
    record = get_round(round_id)
    with connect() as connection:
        recipe = connection.execute(
            select(recipe_versions_table.c.recipe).where(
                recipe_versions_table.c.version == record["recipe_version"]
            )
        ).scalar_one()
    return {"version": record["recipe_version"], "recipe": recipe}


@router.get(
    "/api/internal/rounds/{round_id}/results",
    operation_id="read_improver_results",
    dependencies=[Depends(agent_key)],
)
def internal_results(round_id: int) -> dict[str, Any]:
    """Return evaluation detail and held-out aggregate scores only."""
    return read_results(round_id)


@router.post(
    "/api/internal/rounds/{round_id}/check",
    operation_id="check_round_budget",
    dependencies=[Depends(agent_key)],
)
def internal_check(round_id: int) -> RoundRecord:
    """Refuse new round work after stopping or spending its limit."""
    with connect() as connection:
        check_budget(connection, round_id)
    return RoundRecord(**get_round(round_id))


@router.post(
    "/api/internal/rounds/{round_id}/stage",
    operation_id="record_round_stage",
    dependencies=[Depends(agent_key)],
)
def internal_stage(round_id: int, request: RoundStageRequest) -> RoundRecord:
    """Record service progress without changing protected settings."""
    values: dict[str, Any] = request.model_dump()
    if request.stage == "working":
        record = get_round(round_id)
        if record["held_out_before"] is None:
            values["held_out_before"] = average_scores(
                record["starting_version"], "held_out"
            )
    return RoundRecord(**update_round(round_id, values))


@router.post(
    "/api/internal/rounds/{round_id}/proposal",
    operation_id="record_improver_proposal",
    dependencies=[Depends(agent_key)],
)
def internal_proposal(round_id: int, request: ProposalRequest) -> RoundRecord:
    """Record a validated proposal waiting for a handler."""
    kind = (
        "configuration"
        if isinstance(request.payload, ConfigurationProposal)
        else "recipe"
    )
    return RoundRecord(**save_proposal(round_id, kind, request.payload.model_dump()))


@router.post(
    "/api/internal/rounds/{round_id}/decision",
    operation_id="record_proposal_decision",
    dependencies=[Depends(agent_key)],
)
def internal_decision(round_id: int, request: InternalProposalDecision) -> RoundRecord:
    """Record the service-accepted handler decision before checkpoint resume."""
    return RoundRecord(**decide_proposal(round_id, request.model_dump()))


@router.post(
    "/api/internal/rounds/{round_id}/apply/{kind}",
    operation_id="apply_approved_proposal",
    dependencies=[Depends(agent_key)],
)
def internal_apply(round_id: int, kind: str) -> RoundRecord:
    """Create a version only from the round's approved proposal."""
    if kind not in ("configuration", "recipe"):
        raise HTTPException(status_code=422, detail="Unknown proposal kind")
    return RoundRecord(**apply_proposal(round_id, kind))


@router.post(
    "/api/internal/rounds/{round_id}/verdict",
    operation_id="record_round_verdict",
    dependencies=[Depends(agent_key)],
)
def internal_verdict(round_id: int, request: VerdictRequest) -> RoundRecord:
    """Keep or roll back the candidate using the backend business rule."""
    return RoundRecord(**verdict(round_id, request.failure))


@router.post(
    "/api/internal/rounds/{round_id}/finish",
    operation_id="finish_improvement_round",
    dependencies=[Depends(agent_key)],
)
def internal_finish(round_id: int, request: FailureRequest) -> RoundRecord:
    """Finish a round and roll back any incomplete candidate."""
    return RoundRecord(**finish_round(round_id, request.error))


@router.post(
    "/api/internal/rounds/{round_id}/model-calls",
    operation_id="record_improver_call",
    dependencies=[Depends(agent_key)],
)
def internal_call(round_id: int, request: ImproverCallRequest) -> RoundRecord:
    """Record the improver's actual usage and cost for its round."""
    record_improver_usage(round_id, request.model_dump())
    return RoundRecord(**get_round(round_id))


@router.post(
    "/api/internal/rounds/{round_id}/stop",
    operation_id="stop_interrupted_round",
    dependencies=[Depends(agent_key)],
)
def internal_stop(round_id: int) -> RoundRecord:
    """Stop a round whose accepted decision has already left its checkpoint wait."""
    return RoundRecord(**stop_round(round_id))
