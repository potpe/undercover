"""Pending improver proposals and immutable versions after handler approval."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, insert, select, update

from backend.configurations import configuration_row, text_diff
from backend.database import (
    configuration_versions_table,
    connect,
    proposals_table,
    recipe_versions_table,
    rounds_table,
    settings_table,
    timestamp,
)
from backend.improvement import get_round, require_round, round_record


def allowed_change(payload: dict[str, Any], settings: dict[str, Any]) -> bool:
    return any(
        model["model_name"] == payload["model"]
        and payload["reasoning_effort"] in model["reasoning_efforts"]
        for model in settings["models_allowed"]
    )


def save_proposal(round_id: int, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    with connect() as connection:
        record = require_round(connection, round_id)
        if kind == "configuration" and not allowed_change(payload, record["settings"]):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Model or reasoning effort is not allowed; "
                    "choose from models_allowed"
                ),
            )
        pending = connection.execute(
            select(proposals_table.c.proposal_id).where(
                proposals_table.c.round_id == round_id,
                proposals_table.c.status == "pending",
            )
        ).first()
        if pending:
            return round_record(connection, record)
        if kind == "configuration":
            old = configuration_row(connection, record["starting_version"])[
                "instructions"
            ]
            new = payload["instructions"]
        else:
            old = connection.execute(
                select(recipe_versions_table.c.recipe).where(
                    recipe_versions_table.c.version == record["recipe_version"]
                )
            ).scalar_one()
            new = payload["recipe"]
        connection.execute(
            insert(proposals_table).values(
                round_id=round_id,
                kind=kind,
                payload=payload,
                reason=payload["reason"],
                proposed_at=timestamp(),
                diff=text_diff(old, new),
                status="pending",
            )
        )
        stage = (
            "configuration_approval" if kind == "configuration" else "recipe_approval"
        )
        connection.execute(
            update(rounds_table)
            .where(rounds_table.c.round_id == round_id)
            .values(stage=stage)
        )
    return get_round(round_id)


def decide_proposal(round_id: int, values: dict[str, Any]) -> dict[str, Any]:
    with connect() as connection:
        require_round(connection, round_id)
        proposal = (
            connection.execute(
                select(proposals_table)
                .where(
                    proposals_table.c.proposal_id == values["proposal_id"],
                    proposals_table.c.round_id == round_id,
                )
                .with_for_update()
            )
            .mappings()
            .first()
        )
        if proposal is None or proposal["status"] != "pending":
            raise HTTPException(
                status_code=409, detail="Proposal is no longer waiting for a decision"
            )
        if values["decision"] == "reject" and not values["reason"].strip():
            raise HTTPException(status_code=422, detail="A rejection needs a reason")
        connection.execute(
            update(proposals_table)
            .where(proposals_table.c.proposal_id == proposal["proposal_id"])
            .values(
                status="approved" if values["decision"] == "approve" else "rejected",
                decided_by=values["handler_id"],
                decided_at=timestamp(),
                decision_reason=values["reason"],
            )
        )
    return get_round(round_id)


def apply_proposal(round_id: int, kind: str) -> dict[str, Any]:
    with connect() as connection:
        record = require_round(connection, round_id)
        proposal = (
            connection.execute(
                select(proposals_table)
                .where(
                    proposals_table.c.round_id == round_id,
                    proposals_table.c.kind == kind,
                    proposals_table.c.status.in_(["approved", "applied"]),
                )
                .order_by(proposals_table.c.proposal_id.desc())
            )
            .mappings()
            .first()
        )
        if proposal is None:
            raise HTTPException(
                status_code=409, detail="The proposal has not been approved"
            )
        if proposal["status"] == "applied":
            return round_record(connection, record)
        payload = proposal["payload"]
        audit = {
            "reason": payload["reason"],
            "proposed_by": "improver",
            "approved_by": proposal["decided_by"],
            "proposed_at": proposal["proposed_at"],
            "approved_at": proposal["decided_at"],
        }
        if kind == "configuration":
            if record["candidate_version"] is not None:
                raise HTTPException(
                    status_code=409, detail="This round already tried a version"
                )
            version = (
                connection.execute(
                    select(func.max(configuration_versions_table.c.version))
                ).scalar_one()
                + 1
            )
            connection.execute(
                insert(configuration_versions_table).values(
                    version=version,
                    instructions=payload["instructions"],
                    model_name=payload["model"],
                    reasoning_effort=payload["reasoning_effort"],
                    **audit,
                )
            )
            connection.execute(
                update(rounds_table)
                .where(rounds_table.c.round_id == round_id)
                .values(candidate_version=version, stage="working")
            )
        else:
            version = (
                connection.execute(
                    select(func.max(recipe_versions_table.c.version))
                ).scalar_one()
                + 1
            )
            connection.execute(
                insert(recipe_versions_table).values(
                    version=version, recipe=payload["recipe"], **audit
                )
            )
            connection.execute(
                update(settings_table)
                .where(settings_table.c.key == "current_recipe_version")
                .values(data=version)
            )
            connection.execute(
                update(rounds_table)
                .where(rounds_table.c.round_id == round_id)
                .values(stage="working")
            )
        connection.execute(
            update(proposals_table)
            .where(proposals_table.c.proposal_id == proposal["proposal_id"])
            .values(status="applied")
        )
    return get_round(round_id)
