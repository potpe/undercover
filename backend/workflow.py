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
from backend.improvement_budget import check_run_connection
from backend.models import DecisionRequest, PaymentRequest


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
            select(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .with_for_update()
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    check_run_connection(connection, row["evaluation_run_id"])
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
        current_limit = approval_limit(connection)
        needs_sign_off = (
            request.decision == "approve" and request.payout > current_limit
        )
        if request.decision == "approve":
            status = "waiting_for_sign_off" if needs_sign_off else "in_progress"
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
                sign_off_required=needs_sign_off,
                approval_limit=current_limit,
            )
        )
        insert_activity(
            connection,
            claim_id,
            f"Decision recorded: {request.decision} for {money(request.payout)}",
        )


def sign_off(claim_id: str, handler_id: str) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        if row["evaluation_run_id"] is not None:
            reject(connection, claim_id, "Evaluation claims cannot be signed off")
        handler = connection.execute(
            select(handlers_table.c.data).where(
                handlers_table.c.handler_id == handler_id
            )
        ).scalar_one_or_none()
        if handler is None:
            reject(connection, claim_id, "Handler not found", 404)
        if not handler["can_approve_over_limit"]:
            reject(
                connection,
                claim_id,
                "Handler cannot approve over-limit payouts",
                status_code=403,
            )
        if (
            row["signed_off_by"] == handler_id
            and row["paid_amount"] is not None
            and float(row["paid_amount"]) == float(row["payout"])
        ):
            return
        if row["status"] != "waiting_for_sign_off" or not row["sign_off_required"]:
            reject(connection, claim_id, "Claim does not need sign-off")
        if row["paid_amount"] is not None:
            reject(connection, claim_id, "Claim already has a payment")
        if row["signed_off_by"]:
            reject(connection, claim_id, "Claim already signed off")
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(signed_off_by=handler_id, signed_off_at=timestamp())
        )
        insert_activity(
            connection,
            claim_id,
            f"Payment signed off by {handler['name']}",
        )
        _record_payment(
            connection,
            claim_id,
            PaymentRequest(amount=float(row["payout"])),
            require_sign_off=True,
        )


def _record_payment(
    connection: Connection,
    claim_id: str,
    request: PaymentRequest,
    *,
    require_sign_off: bool = False,
) -> None:
    row = claim_row(connection, claim_id)
    if row["paid_amount"] is not None and request.amount == float(row["paid_amount"]):
        return
    if row["decision"] != "approve":
        reject(connection, claim_id, "Claim has no approval to pay", action="Payment")
    if request.amount != float(row["payout"]):
        reject(
            connection,
            claim_id,
            "Payment must equal the approved payout",
            action="Payment",
        )
    if require_sign_off and not row["signed_off_by"]:
        reject(
            connection, claim_id, "Payment requires handler sign-off", action="Payment"
        )
    if row["sign_off_required"] and not row["signed_off_by"]:
        limit = float(row["approval_limit"])
        message = f"Payout over {money(limit)} requires handler sign-off"
        insert_activity(connection, claim_id, f"Payment blocked: {message}")
        connection.commit()
        raise HTTPException(status_code=409, detail=message)
    connection.execute(
        update(claims_table)
        .where(claims_table.c.claim_id == claim_id)
        .values(paid_amount=request.amount, status="decided", paid_at=timestamp())
    )
    insert_activity(connection, claim_id, f"Payment recorded: {money(request.amount)}")


def record_payment(claim_id: str, request: PaymentRequest) -> None:
    with connect() as connection:
        current_limit = approval_limit(connection)
        if request.amount > current_limit:
            detail = (
                f"Payment exceeds the current approval limit of {money(current_limit)}"
            )
            raise HTTPException(
                status_code=403,
                detail=detail,
            )
        _record_payment(connection, claim_id, request)


def start_run(claim_id: str, thread_id: str | None = None) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        if row["status"] != "waiting":
            reject(connection, claim_id, "Claim is not waiting to run")
        connection.execute(
            update(claims_table)
            .where(claims_table.c.claim_id == claim_id)
            .values(
                status="in_progress",
                agent_thread_id=thread_id,
                work_run_id=None,
            )
        )


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
