import sqlite3
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from backend.database import connect


def now() -> str:
    return datetime.now(UTC).isoformat()


def run_record(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    todos = connection.execute(
        "SELECT * FROM run_todos WHERE run_id = ? ORDER BY position", (row["run_id"],)
    ).fetchall()
    return {
        "run_id": row["run_id"],
        "thread_id": row["thread_id"],
        "claim_cap": row["claim_cap"],
        "target_claim_id": row["target_claim_id"],
        "model_name": row["model_name"],
        "status": row["status"],
        "summary": row["summary"],
        "created_at": row["created_at"],
        "completed_at": row["completed_at"],
        "todos": [
            {
                "claim_id": todo["claim_id"],
                "position": todo["position"],
                "status": todo["status"],
                "attempts": todo["attempts"],
                "result": todo["result"],
                "error": todo["error"],
            }
            for todo in todos
        ],
    }


def get_run(run_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        return run_record(connection, row) if row else None


def current_run() -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM runs ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        return run_record(connection, row) if row else None


def require_run(run_id: int) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def create_run(
    thread_id: str, claim_cap: int, target_claim_id: str | None = None
) -> dict[str, Any]:
    with connect() as connection:
        active = connection.execute(
            "SELECT run_id FROM runs WHERE status = 'in_progress'"
        ).fetchone()
        if active:
            raise HTTPException(status_code=409, detail="Another run is in progress")
        if target_claim_id:
            target = connection.execute(
                "SELECT status FROM claims WHERE claim_id = ?", (target_claim_id,)
            ).fetchone()
            if target is None:
                raise HTTPException(status_code=404, detail="Claim not found")
            if target["status"] != "waiting":
                raise HTTPException(
                    status_code=409, detail="Claim is not waiting to run"
                )
        cursor = connection.execute(
            """
            INSERT INTO runs (thread_id, claim_cap, target_claim_id, status, created_at)
            VALUES (?, ?, ?, 'in_progress', ?)
            """,
            (thread_id, claim_cap, target_claim_id, now()),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("Run insert did not return an id")
        connection.commit()
        return require_run(cursor.lastrowid)


def planned_run(connection: sqlite3.Connection, run_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    existing = connection.execute(
        "SELECT COUNT(*) FROM run_todos WHERE run_id = ?", (run_id,)
    ).fetchone()[0]
    if existing:
        raise HTTPException(status_code=409, detail="Run already has a plan")
    if row["status"] != "in_progress":
        raise HTTPException(status_code=409, detail="Run is not in progress")
    return row


def validate_plan(
    connection: sqlite3.Connection, run: sqlite3.Row, claim_ids: list[str]
) -> None:
    if len(set(claim_ids)) != len(claim_ids):
        raise HTTPException(status_code=409, detail="Plan contains duplicate claims")
    if len(claim_ids) > run["claim_cap"]:
        raise HTTPException(status_code=409, detail="Plan exceeds the run's claim cap")
    target = run["target_claim_id"]
    if target and claim_ids != [target]:
        raise HTTPException(
            status_code=409, detail="Plan does not match the named claim"
        )
    for claim_id in claim_ids:
        claim = connection.execute(
            "SELECT status FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if claim is None:
            raise HTTPException(status_code=404, detail="Claim not found")
        if claim["status"] != "waiting":
            raise HTTPException(
                status_code=409, detail="Plan includes a claim that is not waiting"
            )
    waiting = connection.execute(
        "SELECT COUNT(*) FROM claims WHERE status = 'waiting'"
    ).fetchone()[0]
    expected = 1 if target else min(run["claim_cap"], waiting)
    if len(claim_ids) != expected:
        raise HTTPException(
            status_code=409, detail="Plan does not fill the run's claim cap"
        )


def save_plan(run_id: int, claim_ids: list[str], model_name: str) -> dict[str, Any]:
    with connect() as connection:
        row = planned_run(connection, run_id)
        validate_plan(connection, row, claim_ids)
        for position, claim_id in enumerate(claim_ids, start=1):
            connection.execute(
                """
                INSERT INTO run_todos (run_id, claim_id, position, status)
                VALUES (?, ?, ?, 'to_do')
                """,
                (run_id, claim_id, position),
            )
        connection.execute(
            "UPDATE runs SET model_name = ? WHERE run_id = ?", (model_name, run_id)
        )
    return require_run(run_id)


def finish_run(run_id: int, summary: str) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run["status"] == "completed":
        raise HTTPException(status_code=409, detail="Run already completed")
    if not run["todos"]:
        raise HTTPException(status_code=409, detail="Run has no plan")
    if any(todo["status"] in ("to_do", "in_progress") for todo in run["todos"]):
        raise HTTPException(status_code=409, detail="Run has unfinished claims")
    with connect() as connection:
        connection.execute(
            """
            UPDATE runs SET status = 'completed', summary = ?, completed_at = ?
            WHERE run_id = ?
            """,
            (summary, now(), run_id),
        )
    return require_run(run_id)
