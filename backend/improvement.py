"""Backend-owned improvement round state, snapshots and verdicts."""

from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import Connection, RowMapping, func, insert, select, update

from backend.configurations import current_version
from backend.database import (
    connect,
    improver_calls_table,
    iso,
    judge_grades_table,
    model_calls_table,
    proposals_table,
    rounds_table,
    runs_table,
    settings_table,
    timestamp,
)
from backend.improvement_rules import compare_scores
from backend.settings import settings_record
from backend.version_scores import average_scores

REQUIRED_EVALUATIONS = 3


def require_round(
    connection: Connection, round_id: int, *, open_only: bool = True
) -> RowMapping:
    row = (
        connection.execute(
            select(rounds_table)
            .where(rounds_table.c.round_id == round_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Round not found")
    if open_only and row["status"] != "open":
        raise HTTPException(status_code=409, detail="Round is stopped or finished")
    return row


def round_cost(connection: Connection, round_id: int) -> float:
    own = connection.execute(
        select(func.coalesce(func.sum(improver_calls_table.c.cost), 0)).where(
            improver_calls_table.c.round_id == round_id
        )
    ).scalar_one()
    model = connection.execute(
        select(func.coalesce(func.sum(model_calls_table.c.cost), 0))
        .join(runs_table, model_calls_table.c.run_id == runs_table.c.run_id)
        .where(runs_table.c.round_id == round_id)
    ).scalar_one()
    judge = connection.execute(
        select(func.coalesce(func.sum(judge_grades_table.c.judge_cost), 0))
        .join(runs_table, judge_grades_table.c.run_id == runs_table.c.run_id)
        .where(runs_table.c.round_id == round_id)
    ).scalar_one()
    return float(own + model + judge)


def proposal_record(row: RowMapping) -> dict[str, Any]:
    return {
        **dict(row),
        "proposed_at": iso(row["proposed_at"]),
        "decided_at": iso(row["decided_at"]),
    }


def round_record(connection: Connection, row: RowMapping) -> dict[str, Any]:
    proposals = (
        connection.execute(
            select(proposals_table)
            .where(proposals_table.c.round_id == row["round_id"])
            .order_by(proposals_table.c.proposal_id)
        )
        .mappings()
        .all()
    )
    return {
        **dict(row),
        "created_at": iso(row["created_at"]),
        "finished_at": iso(row["finished_at"]),
        "stopped_at": iso(row["stopped_at"]),
        "cost": round_cost(connection, row["round_id"]),
        "proposals": [proposal_record(item) for item in proposals],
    }


def get_round(round_id: int) -> dict[str, Any]:
    with connect() as connection:
        return round_record(
            connection, require_round(connection, round_id, open_only=False)
        )


def list_rounds() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(rounds_table).order_by(rounds_table.c.round_id.desc())
            )
            .mappings()
            .all()
        )
        return [round_record(connection, row) for row in rows]


def create_round(handler_id: str) -> dict[str, Any]:
    with connect() as connection:
        connection.execute(
            select(settings_table)
            .where(
                settings_table.c.key.in_(
                    (
                        "company",
                        "improvement",
                        "models_allowed",
                        "current_configuration_version",
                        "current_recipe_version",
                    )
                )
            )
            .with_for_update()
        ).all()
        if connection.execute(
            select(rounds_table.c.round_id).where(rounds_table.c.status == "open")
        ).first():
            raise HTTPException(
                status_code=409, detail="An improvement round is already open"
            )
        result = connection.execute(
            insert(rounds_table)
            .values(
                thread_id=f"improvement-{uuid4()}",
                status="open",
                stage="baseline",
                recipe_version=current_version(connection, "current_recipe_version"),
                settings=settings_record(connection),
                starting_version=current_version(
                    connection, "current_configuration_version"
                ),
                created_by=handler_id,
                created_at=timestamp(),
                progress=0,
            )
            .returning(rounds_table.c.round_id)
        )
        round_id = int(result.scalar_one())
    return get_round(round_id)


def update_round(round_id: int, values: dict[str, Any]) -> dict[str, Any]:
    with connect() as connection:
        require_round(connection, round_id)
        connection.execute(
            update(rounds_table)
            .where(rounds_table.c.round_id == round_id)
            .values(**values)
        )
    return get_round(round_id)


def stop_round(round_id: int) -> dict[str, Any]:
    with connect() as connection:
        row = require_round(connection, round_id)
        connection.execute(
            update(rounds_table)
            .where(rounds_table.c.round_id == round_id)
            .values(status="stopped", stage="stopped", stopped_at=timestamp())
        )
        connection.execute(
            update(runs_table)
            .where(
                runs_table.c.round_id == round_id, runs_table.c.status == "in_progress"
            )
            .values(status="stopped", stopped_at=timestamp())
        )
        if row["candidate_version"] is not None and row["verdict"] is None:
            connection.execute(
                update(rounds_table)
                .where(rounds_table.c.round_id == round_id)
                .values(
                    verdict="rolled_back",
                    verdict_reason="Round stopped before its verdict",
                )
            )
    return get_round(round_id)


def finish_round(round_id: int, reason: str) -> dict[str, Any]:
    record = get_round(round_id)
    if record["candidate_version"] is not None and record["verdict"] is None:
        verdict(round_id, reason)
    return update_round(
        round_id,
        {
            "status": "finished",
            "stage": "finished",
            "finished_at": timestamp(),
            "finish_reason": reason,
        },
    )


def verdict(round_id: int, failure: str | None = None) -> dict[str, Any]:
    record = get_round(round_id)
    before = record["held_out_before"] or average_scores(
        record["starting_version"], "held_out"
    )
    candidate = record["candidate_version"]
    after = average_scores(candidate, "held_out") if candidate else None
    if record["verdict"] is not None:
        return record
    with connect() as connection:
        row = require_round(connection, round_id)
        runs = (
            connection.execute(
                select(runs_table).where(
                    runs_table.c.round_id == round_id,
                    runs_table.c.round_purpose == "candidate",
                )
            )
            .mappings()
            .all()
        )
        complete = len(runs) == REQUIRED_EVALUATIONS and all(
            item["status"] == "completed" for item in runs
        )
        if failure or not complete or before is None or after is None:
            result = {
                "kept": False,
                "reason": failure or "Required evaluations did not complete",
            }
        else:
            result = compare_scores(after, before, row["settings"])
        if result["kept"]:
            connection.execute(
                update(settings_table)
                .where(settings_table.c.key == "current_configuration_version")
                .values(data=candidate)
            )
        connection.execute(
            update(rounds_table)
            .where(rounds_table.c.round_id == round_id)
            .values(
                verdict="kept" if result["kept"] else "rolled_back",
                verdict_reason=result["reason"],
                held_out_before=before,
                held_out_after=after,
                stage="verdict",
            )
        )
    return get_round(round_id)


def record_improver_usage(round_id: int, values: dict[str, Any]) -> None:
    with connect() as connection:
        require_round(connection, round_id)
        if connection.execute(
            select(improver_calls_table.c.call_id).where(
                improver_calls_table.c.call_id == values["call_id"]
            )
        ).first():
            return
        connection.execute(
            insert(improver_calls_table).values(
                round_id=round_id, created_at=timestamp(), **values
            )
        )


def recover_rounds() -> None:
    for record in list_rounds():
        if record["status"] == "open" and record["stage"] not in (
            "configuration_approval",
            "recipe_approval",
        ):
            stop_round(record["round_id"])
