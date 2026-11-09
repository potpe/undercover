from typing import NoReturn

from fastapi import HTTPException
from sqlalchemy import Connection, RowMapping, select, update

from backend.database import (
    claims_table,
    connect,
    handlers_table,
    insert_activity,
    settings_table,
    timestamp,
)
from backend.models import DecisionRequest, PaymentRequest, SignOffRequest


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def reject(
    connection: Connection,
    claim_id: str,
    message: str,
    status_code: int = 409,
    action: str = "Action",
) -> NoReturn:
    insert_activity(connection, claim_id, f"{action} rejected: {message}")
    connection.commit()
    raise HTTPException(status_code=status_code, detail=message)


def approval_limit(connection: Connection) -> float:
    company = connection.execute(
        select(settings_table.c.data).where(settings_table.c.key == "company")
    ).scalar_one()
    return float(company["payout_approval_limit"])


def claim_row(connection: Connection, claim_id: str) -> RowMapping:
    row = (
        connection.execute(
            select(claims_table).where(claims_table.c.claim_id == claim_id)
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return row


def same_decision(row: RowMapping, request: DecisionRequest) -> bool:
    return (
        row["decision"] == request.decision
        and float(row["payout"]) == request.payout
        and row["reason"] == request.reason
        and row["policy_section"] == request.policy_section
    )


def record_decision(claim_id: str, request: DecisionRequest) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        if row["decision"]:
            if same_decision(row, request):
                return
            reject(connection, claim_id, "Claim already has a decision")
        if request.decision != "approve" and request.payout != 0:
            reject(
                connection,
                claim_id,
                "Declines and referrals must have zero payout",
            )
        if request.decision == "approve":
            status = (
                "waiting_for_sign_off"
                if request.payout > approval_limit(connection)
                else "in_progress"
            )
        else:
            status = "decided"
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(
                decision=request.decision,
                payout=request.payout,
                reason=request.reason,
                policy_section=request.policy_section,
                status=status,
            )
        )
        insert_activity(
            connection,
            claim_id,
            f"Decision recorded: {request.decision} for {money(request.payout)}",
        )


def sign_off(claim_id: str, request: SignOffRequest) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        handler = connection.execute(
            select(handlers_table.c.data).where(
                handlers_table.c.handler_id == request.handler_id
            )
        ).scalar_one_or_none()
        if handler is None:
            reject(connection, claim_id, "Handler not found", 404)
        if not handler["can_approve_over_limit"]:
            reject(connection, claim_id, "Handler cannot approve over-limit payouts")
        if row["decision"] != "approve" or float(row["payout"]) <= approval_limit(
            connection
        ):
            reject(connection, claim_id, "Claim does not need sign-off")
        if row["paid_amount"] is not None:
            reject(connection, claim_id, "Claim already has a payment")
        if row["signed_off_by"]:
            reject(connection, claim_id, "Claim already signed off")
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(signed_off_by=request.handler_id, signed_off_at=timestamp())
        )
        insert_activity(
            connection,
            claim_id,
            f"Payment signed off by {handler['name']}",
        )


def record_payment(claim_id: str, request: PaymentRequest) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        if row["paid_amount"] is not None and request.amount == float(
            row["paid_amount"]
        ):
            return
        if row["decision"] != "approve":
            reject(
                connection, claim_id, "Claim has no approval to pay", action="Payment"
            )
        if request.amount != float(row["payout"]):
            reject(
                connection,
                claim_id,
                "Payment must equal the approved payout",
                action="Payment",
            )
        limit = approval_limit(connection)
        if float(row["payout"]) > limit and not row["signed_off_by"]:
            message = f"Payout over {money(limit)} requires handler sign-off"
            insert_activity(connection, claim_id, f"Payment blocked: {message}")
            connection.commit()
            raise HTTPException(status_code=409, detail=message)
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(
                paid_amount=request.amount,
                status="decided",
                paid_at=timestamp(),
            )
        )
        insert_activity(
            connection, claim_id, f"Payment recorded: {money(request.amount)}"
        )


def start_run(claim_id: str) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        if row["status"] != "waiting":
            reject(connection, claim_id, "Claim is not waiting to run")
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(status="in_progress")
        )
        insert_activity(connection, claim_id, "Agent run started")


def fail_run(claim_id: str, error: Exception) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        insert_activity(connection, claim_id, f"Agent run failed: {error}")
        if row["decision"] is None:
            connection.execute(
                update(claims_table)
                .where(claims_table.c.claim_id == claim_id)
                .values(status="waiting")
            )
