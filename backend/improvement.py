"""Persistent improvement round records."""

from typing import Any

from sqlalchemy import select

from backend.database import connect, iso, rounds_table


def list_rounds() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(rounds_table).order_by(rounds_table.c.round_id.desc())
            )
            .mappings()
            .all()
        )
        return [
            {
                "round_id": row["round_id"],
                "thread_id": row["thread_id"],
                "status": row["status"],
                "stage": row["stage"],
                "recipe_version": row["recipe_version"],
                "settings": row["settings"],
                "starting_version": row["starting_version"],
                "candidate_version": row["candidate_version"],
                "held_out_before": row["held_out_before"],
                "held_out_after": row["held_out_after"],
                "verdict": row["verdict"],
                "verdict_reason": row["verdict_reason"],
                "created_by": row["created_by"],
                "created_at": iso(row["created_at"]),
                "finished_at": iso(row["finished_at"]),
                "stopped_at": iso(row["stopped_at"]),
            }
            for row in rows
        ]
