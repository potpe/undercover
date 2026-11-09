from collections.abc import Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, RowMapping, func, insert, select, update

from backend.database import (
    claims_table,
    connect,
    insert_activity,
    iso,
    run_todos_table,
    runs_table,
    timestamp,
)
from backend.models import PaymentRequest
from backend.workflow import record_payment


def run_record(connection: Connection, row: RowMapping) -> dict[str, Any]:
    todos = (
        connection.execute(
            select(run_todos_table)
            .where(run_todos_table.c.run_id == row["run_id"])
            .order_by(run_todos_table.c.position)
        )
        .mappings()
        .all()
    )
    return {
        "run_id": row["run_id"],
        "thread_id": row["thread_id"],
        "claim_cap": row["claim_cap"],
        "target_claim_id": row["target_claim_id"],
        "model_name": row["model_name"],
        "status": row["status"],
        "summary": row["summary"],
        "created_at": iso(row["created_at"]),
        "completed_at": iso(row["completed_at"]),
        "todos": [dict(todo) for todo in todos],
    }


def get_run(run_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        row = (
            connection.execute(select(runs_table).where(runs_table.c.run_id == run_id))
            .mappings()
            .first()
        )
        return run_record(connection, row) if row else None


def current_run() -> dict[str, Any] | None:
    with connect() as connection:
        row = (
            connection.execute(
                select(runs_table).order_by(runs_table.c.run_id.desc()).limit(1)
            )
            .mappings()
            .first()
        )
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
            select(runs_table.c.run_id).where(runs_table.c.status == "in_progress")
        ).first()
        if active:
            raise HTTPException(status_code=409, detail="Another run is in progress")
        if target_claim_id:
            target = connection.execute(
                select(claims_table.c.status).where(
                    claims_table.c.claim_id == target_claim_id
                )
            ).scalar_one_or_none()
            if target is None:
                raise HTTPException(status_code=404, detail="Claim not found")
            if target != "waiting":
                raise HTTPException(
                    status_code=409, detail="Claim is not waiting to run"
                )
        result = connection.execute(
            insert(runs_table).values(
                thread_id=thread_id,
                claim_cap=claim_cap,
                target_claim_id=target_claim_id,
                status="in_progress",
                created_at=timestamp(),
            )
        )
        primary_key = result.inserted_primary_key
        if primary_key is None:
            raise RuntimeError("Run insert did not return an id")
        run_id = primary_key[0]
    return require_run(run_id)


def planned_run(connection: Connection, run_id: int) -> RowMapping:
    row = (
        connection.execute(select(runs_table).where(runs_table.c.run_id == run_id))
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    existing = connection.execute(
        select(func.count())
        .select_from(run_todos_table)
        .where(run_todos_table.c.run_id == run_id)
    ).scalar_one()
    if existing:
        raise HTTPException(status_code=409, detail="Run already has a plan")
    if row["status"] != "in_progress":
        raise HTTPException(status_code=409, detail="Run is not in progress")
    return row


def validate_plan(
    connection: Connection, run: RowMapping, claim_ids: list[str]
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
            select(claims_table.c.status).where(claims_table.c.claim_id == claim_id)
        ).scalar_one_or_none()
        if claim is None:
            raise HTTPException(status_code=404, detail="Claim not found")
        if claim != "waiting":
            raise HTTPException(
                status_code=409, detail="Plan includes a claim that is not waiting"
            )
    waiting = connection.execute(
        select(func.count())
        .select_from(claims_table)
        .where(claims_table.c.status == "waiting")
    ).scalar_one()
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
                insert(run_todos_table).values(
                    run_id=run_id,
                    claim_id=claim_id,
                    position=position,
                    status="to_do",
                    attempts=0,
                )
            )
        connection.execute(
            update(runs_table)
            .where(runs_table.c.run_id == run_id)
            .values(model_name=model_name)
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
            update(runs_table)
            .where(runs_table.c.run_id == run_id)
            .values(status="completed", summary=summary, completed_at=timestamp())
        )
    return require_run(run_id)


def fail_queue_run(run_id: int, error: str) -> None:
    run = get_run(run_id)
    if run is None or run["status"] != "in_progress":
        return
    for todo in run["todos"]:
        if todo["status"] == "in_progress":
            fail_work(run_id, todo["claim_id"], error)
    with connect() as connection:
        connection.execute(
            update(runs_table)
            .where(runs_table.c.run_id == run_id)
            .values(status="failed", summary=error, completed_at=timestamp())
        )


def work_outcome(
    run_id: int, todo: RowMapping, status: str | None = None
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "claim_id": todo["claim_id"],
        "status": status or todo["status"],
        "attempts": todo["attempts"],
        "result": todo["result"],
    }


def claim_result(claim: RowMapping) -> str:
    return f"{claim['claim_id']}: {claim['decision']} ${float(claim['payout']):,.2f}"


def selected_todo(connection: Connection, run_id: int, claim_id: str) -> RowMapping:
    todo = (
        connection.execute(
            select(run_todos_table).where(
                run_todos_table.c.run_id == run_id,
                run_todos_table.c.claim_id == claim_id,
            )
        )
        .mappings()
        .first()
    )
    if todo is None:
        raise HTTPException(status_code=404, detail="Claim is not in this run")
    return todo


def complete_work(run_id: int, claim_id: str, result: str) -> dict[str, Any]:
    with connect() as connection:
        connection.execute(
            update(run_todos_table)
            .where(
                run_todos_table.c.run_id == run_id,
                run_todos_table.c.claim_id == claim_id,
            )
            .values(status="done", result=result, error=None)
        )
        todo = selected_todo(connection, run_id, claim_id)
        return work_outcome(run_id, todo)


def fail_work(run_id: int, claim_id: str, error: str) -> dict[str, Any]:
    with connect() as connection:
        todo = selected_todo(connection, run_id, claim_id)
        attempts = todo["attempts"] + 1
        todo_status = "to_do" if attempts == 1 else "left_for_handler"
        claim_status = "waiting" if attempts == 1 else "left_for_handler"
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(status=claim_status)
        )
        connection.execute(
            update(run_todos_table)
            .where(
                run_todos_table.c.run_id == run_id,
                run_todos_table.c.claim_id == claim_id,
            )
            .values(status=todo_status, attempts=attempts, result=error, error=error)
        )
        insert_activity(connection, claim_id, f"Attempt {attempts} failed: {error}")
        updated = selected_todo(connection, run_id, claim_id)
        outcome_status = "retry" if attempts == 1 else "left_for_handler"
        return work_outcome(run_id, updated, outcome_status)


def selected_work(
    connection: Connection, run_id: int, claim_id: str
) -> tuple[RowMapping, RowMapping]:
    run = connection.execute(
        select(runs_table.c.run_id).where(runs_table.c.run_id == run_id)
    ).first()
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    todo = selected_todo(connection, run_id, claim_id)
    claim = (
        connection.execute(
            select(claims_table).where(claims_table.c.claim_id == claim_id)
        )
        .mappings()
        .first()
    )
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim is not in this run")
    return todo, claim


def work_claim(
    run_id: int, claim_id: str, runner: Callable[[str, str], str]
) -> dict[str, Any]:
    with connect() as connection:
        todo, claim = selected_work(connection, run_id, claim_id)
        if todo["status"] in ("done", "left_for_handler"):
            return work_outcome(run_id, todo)
        if claim["status"] in ("decided", "waiting_for_sign_off"):
            return complete_work(run_id, claim_id, claim_result(claim))
        if todo["status"] == "in_progress" or claim["status"] != "waiting":
            raise HTTPException(status_code=409, detail="Claim is already being worked")
        attempt = todo["attempts"] + 1
        thread_id = f"{run_id}:{claim_id}:attempt-{attempt}"
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(status="in_progress", agent_thread_id=thread_id)
        )
        connection.execute(
            update(run_todos_table)
            .where(
                run_todos_table.c.run_id == run_id,
                run_todos_table.c.claim_id == claim_id,
            )
            .values(status="in_progress", error=None)
        )

    reply = None
    error = None
    try:
        reply = runner(claim_id, thread_id)
    except Exception as caught:
        error = str(caught)

    with connect() as connection:
        claim = (
            connection.execute(
                select(claims_table).where(claims_table.c.claim_id == claim_id)
            )
            .mappings()
            .one()
        )
    if (
        claim["status"] == "in_progress"
        and claim["decision"] == "approve"
        and claim["paid_amount"] is None
    ):
        try:
            record_payment(claim_id, PaymentRequest(amount=float(claim["payout"])))
        except Exception as caught:
            error = str(caught)
        with connect() as connection:
            claim = (
                connection.execute(
                    select(claims_table).where(claims_table.c.claim_id == claim_id)
                )
                .mappings()
                .one()
            )
    if claim["status"] in ("decided", "waiting_for_sign_off"):
        result = " ".join((reply or claim_result(claim)).split())
        return complete_work(run_id, claim_id, result)
    return fail_work(
        run_id, claim_id, error or "Agent finished without recording a decision"
    )
