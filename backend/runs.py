from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, RowMapping, func, insert, select, update

from backend.database import (
    claims_table,
    connect,
    insert_activity,
    iso,
    model_calls_table,
    run_claims_table,
    runs_table,
    timestamp,
    work_periods_table,
)


def usage_records(connection: Connection, run_id: int | None) -> dict[str, Any]:
    model_filter = (
        model_calls_table.c.run_id.is_(None)
        if run_id is None
        else model_calls_table.c.run_id == run_id
    )
    model = connection.execute(
        select(
            func.coalesce(func.sum(model_calls_table.c.cost), 0),
            func.coalesce(func.sum(model_calls_table.c.prompt_tokens), 0),
            func.coalesce(func.sum(model_calls_table.c.completion_tokens), 0),
            func.coalesce(func.sum(model_calls_table.c.total_tokens), 0),
        ).where(model_filter)
    ).one()
    work_filter = (
        work_periods_table.c.run_id.is_(None)
        if run_id is None
        else work_periods_table.c.run_id == run_id
    )
    work = connection.execute(
        select(
            func.coalesce(func.sum(work_periods_table.c.seconds), 0),
            func.min(work_periods_table.c.started_at),
            func.max(work_periods_table.c.finished_at),
        ).where(work_filter)
    ).one()
    return {
        "model_cost": float(model[0]),
        "prompt_tokens": int(model[1]),
        "completion_tokens": int(model[2]),
        "total_tokens": int(model[3]),
        "active_seconds": float(work[0]),
        "first_started_at": iso(work[1]),
        "last_finished_at": iso(work[2]),
    }


def run_usage(run_id: int | None) -> dict[str, Any]:
    with connect() as connection:
        return usage_records(connection, run_id)


def claim_usage(connection: Connection, run_id: int) -> dict[str, dict[str, Any]]:
    costs = connection.execute(
        select(
            model_calls_table.c.claim_id,
            func.sum(model_calls_table.c.cost),
        )
        .where(model_calls_table.c.run_id == run_id)
        .group_by(model_calls_table.c.claim_id)
    ).all()
    periods = connection.execute(
        select(
            work_periods_table.c.claim_id,
            func.sum(work_periods_table.c.seconds),
            func.min(work_periods_table.c.started_at),
            func.max(work_periods_table.c.finished_at),
        )
        .where(work_periods_table.c.run_id == run_id)
        .group_by(work_periods_table.c.claim_id)
    ).all()
    result = {
        claim_id: {
            "model_cost": float(cost),
            "active_seconds": 0.0,
            "started_at": None,
            "finished_at": None,
        }
        for claim_id, cost in costs
    }
    for claim_id, seconds, started, finished in periods:
        result.setdefault(claim_id, {"model_cost": 0.0, "active_seconds": 0.0}).update(
            active_seconds=float(seconds),
            started_at=iso(started),
            finished_at=iso(finished),
        )
    return result


def wall_seconds(row: RowMapping, usage: dict[str, Any]) -> float:
    started = usage["first_started_at"]
    if not started:
        return 0.0
    finished = row["completed_at"] or row["stopped_at"] or timestamp()
    if finished.tzinfo is None:
        finished = finished.replace(tzinfo=UTC)
    return max(0.0, (finished - datetime.fromisoformat(started)).total_seconds())


def run_record(connection: Connection, row: RowMapping) -> dict[str, Any]:
    rows = (
        connection.execute(
            select(run_claims_table, claims_table.c.failed_attempts)
            .join(claims_table, run_claims_table.c.claim_id == claims_table.c.claim_id)
            .where(run_claims_table.c.run_id == row["run_id"])
            .order_by(run_claims_table.c.position)
        )
        .mappings()
        .all()
    )
    usage = usage_records(connection, row["run_id"])
    per_claim = claim_usage(connection, row["run_id"])
    claims = []
    for claim in rows:
        claim_cost = per_claim.get(claim["claim_id"], {})
        claims.append(
            {
                "claim_id": claim["claim_id"],
                "position": claim["position"],
                "status": claim["status"],
                "attempts": claim["failed_attempts"],
                "result": claim["result"],
                "error": claim["error"],
                "started_at": claim_cost.get("started_at"),
                "finished_at": claim_cost.get("finished_at"),
                "model_cost": claim_cost.get("model_cost", 0.0),
                "active_seconds": claim_cost.get("active_seconds", 0.0),
            }
        )
    return {
        "run_id": row["run_id"],
        "kind": row["kind"],
        "concurrency": row["concurrency"],
        "status": row["status"],
        "model_name": row["model_name"],
        "reasoning_effort": row["reasoning_effort"],
        "evaluation_set": row["evaluation_set"],
        "tiny": row["tiny"],
        "summary": row["summary"],
        "created_at": iso(row["created_at"]),
        "completed_at": iso(row["completed_at"]),
        "stopped_at": iso(row["stopped_at"]),
        "claims": claims,
        **{
            key: value
            for key, value in usage.items()
            if key not in ("first_started_at", "last_finished_at")
        },
        "wall_seconds": wall_seconds(row, usage),
    }


def get_run(run_id: int) -> dict[str, Any] | None:
    with connect() as connection:
        row = (
            connection.execute(select(runs_table).where(runs_table.c.run_id == run_id))
            .mappings()
            .first()
        )
        return run_record(connection, row) if row else None


def require_run(run_id: int) -> dict[str, Any]:
    run = get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


def current_dispatch() -> dict[str, Any] | None:
    with connect() as connection:
        row = (
            connection.execute(
                select(runs_table)
                .where(runs_table.c.kind == "dispatch")
                .order_by(runs_table.c.run_id.desc())
                .limit(1)
            )
            .mappings()
            .first()
        )
        return run_record(connection, row) if row else None


def inserted_id(result: Any) -> int:
    primary_key = result.inserted_primary_key
    if primary_key is None:
        raise RuntimeError("Run insert did not return an id")
    return int(primary_key[0])


def create_dispatch(
    concurrency: int, model_name: str, reasoning_effort: str | None
) -> dict[str, Any]:
    with connect() as connection:
        active = connection.execute(
            select(runs_table.c.run_id).where(runs_table.c.status == "in_progress")
        ).first()
        if active:
            raise HTTPException(
                status_code=409, detail="Another dispatch or evaluation is running"
            )
        selected = (
            connection.execute(
                select(claims_table).where(
                    claims_table.c.evaluation_run_id.is_(None),
                    claims_table.c.status.in_(["waiting", "in_progress"]),
                )
            )
            .mappings()
            .all()
        )
        selected = sorted(
            selected,
            key=lambda claim: (claim["data"]["submitted_on"], claim["claim_id"]),
        )
        result = connection.execute(
            insert(runs_table).values(
                kind="dispatch",
                concurrency=concurrency,
                model_name=model_name,
                reasoning_effort=reasoning_effort,
                status="in_progress",
                created_at=timestamp(),
            )
        )
        run_id = inserted_id(result)
        for position, claim in enumerate(selected, start=1):
            connection.execute(
                insert(run_claims_table).values(
                    run_id=run_id,
                    claim_id=claim["claim_id"],
                    position=position,
                    status=claim["status"],
                )
            )
    return require_run(run_id)


def work_rows(
    connection: Connection, run_id: int, claim_id: str
) -> tuple[RowMapping, RowMapping]:
    run = (
        connection.execute(select(runs_table).where(runs_table.c.run_id == run_id))
        .mappings()
        .first()
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    item = (
        connection.execute(
            select(run_claims_table).where(
                run_claims_table.c.run_id == run_id,
                run_claims_table.c.claim_id == claim_id,
            )
        )
        .mappings()
        .first()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="Claim is not in this run")
    claim = (
        connection.execute(
            select(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if claim is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return item, claim


def outcome(
    item: RowMapping, claim: RowMapping | dict[str, Any], status: str, result: str
) -> dict:
    return {
        "run_id": item["run_id"],
        "claim_id": item["claim_id"],
        "status": status,
        "attempts": claim["failed_attempts"],
        "result": result,
    }


def begin_work(run_id: int, claim_id: str) -> dict[str, Any]:
    with connect() as connection:
        item, claim = work_rows(connection, run_id, claim_id)
        if item["status"] in ("done", "left_for_handler"):
            return {
                "run_id": run_id,
                "claim_id": claim_id,
                "run_kind": "dispatch",
                "thread_id": claim["agent_thread_id"],
                "status": item["status"],
                "attempts": claim["failed_attempts"],
                "result": item["result"],
            }
        if claim["status"] in ("decided", "waiting_for_sign_off"):
            result = claim_result(claim)
            connection.execute(
                update(run_claims_table)
                .where(
                    run_claims_table.c.run_id == run_id,
                    run_claims_table.c.claim_id == claim_id,
                )
                .values(status="done", result=result, error=None)
            )
            status = "done"
            thread_id = claim["agent_thread_id"]
            attempts = claim["failed_attempts"]
        else:
            if claim["status"] == "waiting":
                attempts = claim["failed_attempts"] + 1
                thread_id = f"{run_id}:{claim_id}:attempt-{attempts}"
            elif claim["status"] == "in_progress" and claim["agent_thread_id"]:
                attempts = claim["failed_attempts"]
                thread_id = claim["agent_thread_id"]
            else:
                raise HTTPException(
                    status_code=409, detail="Claim is not ready to be worked"
                )
            connection.execute(
                update(claims_table)
                .where(claims_table.c.claim_id == claim_id)
                .values(
                    status="in_progress",
                    agent_thread_id=thread_id,
                    work_run_id=run_id,
                )
            )
            connection.execute(
                update(run_claims_table)
                .where(
                    run_claims_table.c.run_id == run_id,
                    run_claims_table.c.claim_id == claim_id,
                )
                .values(status="in_progress", error=None)
            )
            status = "in_progress"
        run = connection.execute(
            select(runs_table.c.kind).where(runs_table.c.run_id == run_id)
        ).scalar_one()
        attempts = claim["failed_attempts"]
        return {
            "run_id": run_id,
            "claim_id": claim_id,
            "run_kind": run,
            "thread_id": thread_id,
            "status": status,
            "attempts": attempts,
            "result": item["result"],
        }


def claim_result(claim: RowMapping) -> str:
    return f"{claim['claim_id']}: {claim['decision']} ${float(claim['payout']):,.2f}"


def complete_work(run_id: int, claim_id: str, result: str) -> dict[str, Any]:
    with connect() as connection:
        item, claim = work_rows(connection, run_id, claim_id)
        if claim["status"] not in ("decided", "waiting_for_sign_off"):
            raise RuntimeError("Agent finished without recording a decision")
        connection.execute(
            update(run_claims_table)
            .where(
                run_claims_table.c.run_id == run_id,
                run_claims_table.c.claim_id == claim_id,
            )
            .values(status="done", result=result, error=None)
        )
        return outcome(item, claim, "done", result)


def fail_work(run_id: int, claim_id: str, error: str) -> dict[str, Any]:
    with connect() as connection:
        item, claim = work_rows(connection, run_id, claim_id)
        if claim["status"] in ("decided", "waiting_for_sign_off"):
            result = claim_result(claim)
            connection.execute(
                update(run_claims_table)
                .where(
                    run_claims_table.c.run_id == run_id,
                    run_claims_table.c.claim_id == claim_id,
                )
                .values(status="done", result=result, error=None)
            )
            return outcome(item, claim, "done", result)
        attempts = claim["failed_attempts"] + 1
        retry = attempts == 1
        claim_status = "waiting" if retry else "left_for_handler"
        item_status = "waiting" if retry else "left_for_handler"
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(status=claim_status, failed_attempts=attempts)
        )
        connection.execute(
            update(run_claims_table)
            .where(
                run_claims_table.c.run_id == run_id,
                run_claims_table.c.claim_id == claim_id,
            )
            .values(status=item_status, result=error, error=error)
        )
        insert_activity(connection, claim_id, f"Attempt {attempts} failed: {error}")
        claim = dict(claim)
        claim["failed_attempts"] = attempts
        return outcome(item, claim, "retry" if retry else "left_for_handler", error)


def leave_work(run_id: int, claim_id: str, error: str) -> dict[str, Any]:
    with connect() as connection:
        item, claim = work_rows(connection, run_id, claim_id)
        connection.execute(
            update(run_claims_table)
            .where(
                run_claims_table.c.run_id == run_id,
                run_claims_table.c.claim_id == claim_id,
            )
            .values(status="in_progress", result=error, error=error)
        )
        return outcome(item, claim, "in_progress", error)


def fail_claim(claim_id: str, error: str) -> None:
    with connect() as connection:
        run_id = connection.execute(
            select(claims_table.c.work_run_id).where(
                claims_table.c.claim_id == claim_id
            )
        ).scalar_one_or_none()
    if run_id is None:
        raise HTTPException(status_code=404, detail="Claim has no run to fail")
    fail_work(run_id, claim_id, error)


def selected_summary(connection: Connection, run_id: int, error: str | None) -> dict:
    items = (
        connection.execute(
            select(run_claims_table).where(run_claims_table.c.run_id == run_id)
        )
        .mappings()
        .all()
    )
    claims = []
    for item in items:
        claim = (
            connection.execute(
                select(claims_table).where(claims_table.c.claim_id == item["claim_id"])
            )
            .mappings()
            .one()
        )
        claims.append((item, claim))
    return {
        "approved": sum(claim["decision"] == "approve" for _, claim in claims),
        "declined": sum(claim["decision"] == "decline" for _, claim in claims),
        "referred": sum(claim["decision"] == "refer" for _, claim in claims),
        "waiting_for_sign_off": [
            {"claim_id": claim["claim_id"], "payout": float(claim["payout"])}
            for _, claim in claims
            if claim["status"] == "waiting_for_sign_off"
        ],
        "left_for_handler": [
            claim["claim_id"]
            for _, claim in claims
            if claim["status"] == "left_for_handler"
        ],
        "in_progress": [
            claim["claim_id"]
            for item, claim in claims
            if claim["status"] == "in_progress"
            or item["status"] in ("waiting", "in_progress")
        ],
        "error": error,
    }


def finish_run(run_id: int) -> dict[str, Any]:
    with connect() as connection:
        exists = connection.execute(
            select(runs_table.c.run_id).where(runs_table.c.run_id == run_id)
        ).first()
        if exists is None:
            raise HTTPException(status_code=404, detail="Run not found")
        summary = selected_summary(connection, run_id, None)
        connection.execute(
            update(runs_table)
            .where(runs_table.c.run_id == run_id)
            .values(status="completed", summary=summary, completed_at=timestamp())
        )
    return require_run(run_id)


def stop_run(run_id: int, error: str) -> None:
    with connect() as connection:
        summary = selected_summary(connection, run_id, error)
        connection.execute(
            update(runs_table)
            .where(runs_table.c.run_id == run_id)
            .values(status="stopped", summary=summary, stopped_at=timestamp())
        )


def stop_active_runs() -> int:
    with connect() as connection:
        rows = (
            connection.execute(
                select(runs_table.c.run_id).where(runs_table.c.status == "in_progress")
            )
            .scalars()
            .all()
        )
        for run_id in rows:
            summary = selected_summary(connection, run_id, None)
            connection.execute(
                update(runs_table)
                .where(runs_table.c.run_id == run_id)
                .values(status="stopped", summary=summary, stopped_at=timestamp())
            )
    return len(rows)
