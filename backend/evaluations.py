"""Backend-owned evaluation copies and deterministic scoreboard measures."""

from typing import Any

from fastapi import HTTPException
from sqlalchemy import insert, select

from backend.database import (
    claims_table,
    connect,
    handler_decisions_table,
    held_out_claims_table,
    run_claims_table,
    runs_table,
    timestamp,
)
from backend.runs import inserted_id, require_run


def create_evaluation(
    concurrency: int,
    evaluation_set: str,
    tiny: bool,
    model_name: str,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    with connect() as connection:
        active = connection.execute(
            select(runs_table.c.run_id).where(runs_table.c.status == "in_progress")
        ).first()
        if active:
            raise HTTPException(
                status_code=409, detail="Another dispatch or evaluation is running"
            )
        if evaluation_set == "evaluation":
            rows = (
                connection.execute(
                    select(claims_table).where(
                        claims_table.c.evaluation_run_id.is_(None)
                    )
                )
                .mappings()
                .all()
            )
            sources = [dict(row["data"]) for row in rows]
        else:
            rows = (
                connection.execute(
                    select(held_out_claims_table).order_by(
                        held_out_claims_table.c.claim_id
                    )
                )
                .mappings()
                .all()
            )
            sources = [dict(row["data"]) for row in rows]
        labels = (
            connection.execute(
                select(handler_decisions_table).where(
                    handler_decisions_table.c.evaluation_set == evaluation_set
                )
            )
            .mappings()
            .all()
        )
        by_source = {label["claim_id"]: label["data"] for label in labels}
        if tiny:
            sources = [
                claim for claim in sources if by_source[claim["claim_id"]]["tiny"]
            ]
        sources.sort(key=lambda claim: (claim["submitted_on"], claim["claim_id"]))
        result = connection.execute(
            insert(runs_table).values(
                kind="evaluation",
                concurrency=concurrency,
                model_name=model_name,
                reasoning_effort=reasoning_effort,
                evaluation_set=evaluation_set,
                tiny=tiny,
                status="in_progress",
                created_at=timestamp(),
            )
        )
        run_id = inserted_id(result)
        for position, source in enumerate(sources, start=1):
            copy_id = f"EVAL-{run_id}-{position:02d}"
            copy = {**source, "claim_id": copy_id, "status": "waiting"}
            connection.execute(
                insert(claims_table).values(
                    claim_id=copy_id,
                    policy_id=source["policy_id"],
                    customer_id=source["customer_id"],
                    claim_type=source["claim_type"],
                    summary=source["summary"],
                    amount_claimed=source["amount_claimed"],
                    data=copy,
                    status="waiting",
                    failed_attempts=0,
                    evaluation_run_id=run_id,
                )
            )
            connection.execute(
                insert(run_claims_table).values(
                    run_id=run_id,
                    claim_id=copy_id,
                    source_claim_id=source["claim_id"],
                    position=position,
                    status="waiting",
                )
            )
    return require_run(run_id)


def score_answer(
    agent: dict[str, Any] | None, handler: dict[str, Any]
) -> dict[str, Any]:
    effective = agent or {"decision": "refer", "payout": 0.0}
    decision_match = effective["decision"] == handler["decision"]
    difference = abs(float(effective["payout"]) - float(handler["payout"]))
    right = decision_match and difference <= 1
    if decision_match:
        mistake_cost = difference if effective["decision"] == "approve" else 0.0
    elif effective["decision"] == "decline":
        mistake_cost = 1500.0
    elif effective["decision"] == "approve":
        mistake_cost = float(effective["payout"])
    else:
        mistake_cost = 50.0
    return {
        "decision_match": decision_match,
        "right": right,
        "mistake_cost": mistake_cost,
    }


def evaluation_record(run_id: int) -> dict[str, Any]:
    run = require_run(run_id)
    if run["kind"] != "evaluation":
        raise HTTPException(status_code=404, detail="Evaluation not found")
    base_claims = {claim["claim_id"]: claim for claim in run["claims"]}
    with connect() as connection:
        items = (
            connection.execute(
                select(run_claims_table)
                .where(run_claims_table.c.run_id == run_id)
                .order_by(run_claims_table.c.position)
            )
            .mappings()
            .all()
        )
        label_rows = (
            connection.execute(
                select(handler_decisions_table).where(
                    handler_decisions_table.c.evaluation_set == run["evaluation_set"]
                )
            )
            .mappings()
            .all()
        )
        labels = {label["claim_id"]: label["data"] for label in label_rows}
        claims = []
        for item in items:
            copy = (
                connection.execute(
                    select(claims_table).where(
                        claims_table.c.claim_id == item["claim_id"]
                    )
                )
                .mappings()
                .one()
            )
            handler = labels[item["source_claim_id"]]
            agent = (
                {
                    "decision": copy["decision"],
                    "payout": float(copy["payout"]),
                }
                if copy["decision"]
                else None
            )
            score = score_answer(agent, handler)
            claims.append(
                {
                    **base_claims[item["claim_id"]],
                    "source_claim_id": item["source_claim_id"],
                    "handler_decision": handler["decision"],
                    "handler_payout": float(handler["payout"]),
                    "agent_decision": agent["decision"] if agent else "refer",
                    "agent_payout": float(agent["payout"]) if agent else 0.0,
                    "agent_reason": copy["reason"] or "No decision recorded",
                    **score,
                }
            )
    count = len(claims)
    active_seconds = [claim["active_seconds"] for claim in claims]
    measures = {
        "decision_accuracy": (sum(claim["decision_match"] for claim in claims) / count),
        "accuracy": sum(claim["right"] for claim in claims) / count,
        "cost_of_mistakes": sum(claim["mistake_cost"] for claim in claims),
        "straight_through": (
            sum(claim["agent_decision"] != "refer" for claim in claims) / count
        ),
        "model_cost": run["model_cost"],
        "model_cost_per_claim": run["model_cost"] / count,
        "seconds_per_claim": sum(active_seconds) / count,
        "slowest_claim": max(active_seconds),
        "seconds": run["wall_seconds"],
    }
    return {**run, "claim_comparisons": claims, "measures": measures}


def list_evaluations() -> list[dict[str, Any]]:
    with connect() as connection:
        ids = (
            connection.execute(
                select(runs_table.c.run_id)
                .where(runs_table.c.kind == "evaluation")
                .order_by(runs_table.c.run_id.desc())
            )
            .scalars()
            .all()
        )
    return [evaluation_record(run_id) for run_id in ids]
