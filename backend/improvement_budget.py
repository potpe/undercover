"""Round spending checks and historical evaluation estimates."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, select

from backend.configurations import configuration_row
from backend.database import connect, runs_table
from backend.improvement import require_round, round_cost


def check_budget(connection: Connection, round_id: int) -> None:
    record = require_round(connection, round_id)
    if round_cost(connection, round_id) >= record["settings"]["spending_limit"]:
        raise HTTPException(status_code=409, detail="Round spending limit reached")


def check_run(run_id: int | None, *, spending: bool = False) -> None:
    if run_id is None:
        return
    with connect() as connection:
        check_run_connection(connection, run_id, spending=spending)


def check_run_connection(
    connection: Connection, run_id: int | None, *, spending: bool = False
) -> None:
    if run_id is None:
        return
    row = connection.execute(
        select(runs_table.c.round_id).where(runs_table.c.run_id == run_id)
    ).first()
    if row and row[0] is not None:
        if spending:
            check_budget(connection, row[0])
        else:
            require_round(connection, row[0])


def historical_high(connection: Connection, model: str) -> float:
    rows = (
        connection.execute(
            select(runs_table.c.scores).where(
                runs_table.c.model_name == model,
                runs_table.c.kind == "evaluation",
                runs_table.c.status == "completed",
            )
        )
        .scalars()
        .all()
    )
    return max((row["cost_per_claim"] for row in rows if row is not None), default=0.0)


def estimate_evaluation(
    connection: Connection, record: Any, version: int, count: int
) -> float:
    configuration = configuration_row(connection, version)
    high = historical_high(connection, configuration["model_name"])
    if high == 0:
        current = configuration_row(connection, record["starting_version"])
        high = historical_high(connection, current["model_name"])
        catalog = {
            model["model_name"]: model for model in record["settings"]["models_allowed"]
        }
        old, new = catalog[current["model_name"]], catalog[configuration["model_name"]]
        ratios = [
            new[key] / old[key] if old[key] else 0
            for key in ("input_price", "output_price")
        ]
        high *= max(ratios)
    return count * high


def authorize_evaluation(
    connection: Connection, round_id: int, version: int, count: int
) -> None:
    record = require_round(connection, round_id)
    check_budget(connection, round_id)
    estimate = estimate_evaluation(connection, record, version, count)
    remaining = record["settings"]["spending_limit"] - round_cost(connection, round_id)
    if estimate > remaining:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Evaluation estimate ${estimate:.4f} exceeds "
                f"${remaining:.4f} remaining"
            ),
        )
