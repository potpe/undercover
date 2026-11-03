import json
import sqlite3
from typing import NoReturn

from fastapi import HTTPException

from backend.database import connect, insert_activity
from backend.models import DecisionRequest, PaymentRequest, SignOffRequest


def money(amount: float) -> str:
    return f"${amount:,.2f}"


def reject(
    connection: sqlite3.Connection,
    claim_id: str,
    message: str,
    status_code: int = 409,
    action: str = "Action",
) -> NoReturn:
    insert_activity(connection, claim_id, f"{action} rejected: {message}")
    connection.commit()
    raise HTTPException(status_code=status_code, detail=message)


def approval_limit(connection: sqlite3.Connection) -> float:
    row = connection.execute(
        "SELECT data FROM settings WHERE key = 'company'"
    ).fetchone()
    return json.loads(row["data"])["payout_approval_limit"]


def claim_row(connection: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM claims WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    return row


def record_decision(claim_id: str, request: DecisionRequest) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        if row["decision"]:
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
            """
            UPDATE claims
            SET decision = ?, payout = ?, reason = ?, policy_section = ?, status = ?
            WHERE claim_id = ?
            """,
            (
                request.decision,
                request.payout,
                request.reason,
                request.policy_section,
                status,
                claim_id,
            ),
        )
        insert_activity(
            connection,
            claim_id,
            f"Decision recorded: {request.decision} for {money(request.payout)}",
        )


def sign_off(claim_id: str, request: SignOffRequest) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        handler_row = connection.execute(
            "SELECT data FROM handlers WHERE handler_id = ?", (request.handler_id,)
        ).fetchone()
        if handler_row is None:
            reject(connection, claim_id, "Handler not found", 404)
        handler = json.loads(handler_row["data"])
        if not handler["can_approve_over_limit"]:
            reject(connection, claim_id, "Handler cannot approve over-limit payouts")
        if row["decision"] != "approve" or row["payout"] <= approval_limit(connection):
            reject(connection, claim_id, "Claim does not need sign-off")
        if row["paid_amount"] is not None:
            reject(connection, claim_id, "Claim already has a payment")
        if row["signed_off_by"]:
            reject(connection, claim_id, "Claim already signed off")
        connection.execute(
            "UPDATE claims SET signed_off_by = ? WHERE claim_id = ?",
            (request.handler_id, claim_id),
        )
        insert_activity(
            connection,
            claim_id,
            f"Payment signed off by {handler['name']}",
        )


def record_payment(claim_id: str, request: PaymentRequest) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        if row["paid_amount"] is not None:
            reject(
                connection, claim_id, "Claim already has a payment", action="Payment"
            )
        if row["decision"] != "approve":
            reject(
                connection, claim_id, "Claim has no approval to pay", action="Payment"
            )
        if request.amount != row["payout"]:
            reject(
                connection,
                claim_id,
                "Payment must equal the approved payout",
                action="Payment",
            )
        limit = approval_limit(connection)
        if row["payout"] > limit and not row["signed_off_by"]:
            message = f"Payout over {money(limit)} requires handler sign-off"
            insert_activity(connection, claim_id, f"Payment blocked: {message}")
            connection.commit()
            raise HTTPException(status_code=409, detail=message)
        connection.execute(
            "UPDATE claims SET paid_amount = ?, status = 'decided' WHERE claim_id = ?",
            (request.amount, claim_id),
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
            "UPDATE claims SET status = 'in_progress' WHERE claim_id = ?", (claim_id,)
        )
        insert_activity(connection, claim_id, "Agent run started")


def fail_run(claim_id: str, error: Exception) -> None:
    with connect() as connection:
        row = claim_row(connection, claim_id)
        insert_activity(connection, claim_id, f"Agent run failed: {error}")
        if row["decision"] is None:
            connection.execute(
                "UPDATE claims SET status = 'waiting' WHERE claim_id = ?", (claim_id,)
            )
