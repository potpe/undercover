"""Version averages include only completed whole evaluations owned by rounds."""

from typing import Any

from sqlalchemy import Connection, select

from backend.database import connect, runs_table


def qualifying_runs(
    connection: Connection, version: int, evaluation_set: str
) -> list[dict[str, Any]]:
    rows = (
        connection.execute(
            select(runs_table)
            .where(
                runs_table.c.configuration_version == version,
                runs_table.c.kind == "evaluation",
                runs_table.c.evaluation_set == evaluation_set,
                runs_table.c.tiny.is_(False),
                runs_table.c.round_id.is_not(None),
                runs_table.c.status == "completed",
                runs_table.c.scores.is_not(None),
            )
            .order_by(runs_table.c.run_id)
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows if row["scores"] is not None]


def average_scores(version: int, evaluation_set: str) -> dict[str, float | None] | None:
    with connect() as connection:
        return average_scores_in(connection, version, evaluation_set)


def average_scores_in(
    connection: Connection, version: int, evaluation_set: str
) -> dict[str, float | None] | None:
    rows = qualifying_runs(connection, version, evaluation_set)
    if not rows:
        return None
    result: dict[str, float | None] = {}
    for key in ("accuracy", "grade", "cost_per_claim", "seconds_per_claim"):
        values = [row["scores"][key] for row in rows if row["scores"][key] is not None]
        result[key] = sum(values) / len(values) if values else None
    return result
