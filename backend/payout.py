"""UnderCover's deterministic payout rules for approved claims."""

from math import ceil, inf
from typing import Any

from fastapi import HTTPException

from backend.database import add_activity, connect, get_claim_bundle
from backend.models import (
    Benefit,
    CalculationStep,
    PayoutLine,
    PayoutRequest,
    PayoutResponse,
)
from backend.workflow import approval_limit

CASH_LIMIT = 100.0
HOTEL_NIGHT_LIMIT = 350.0
MEALS_PER_TRAVELER_PER_DAY = 35.0
DEDUCTIBLE_BENEFITS = {"baggage", "emergency_medical"}
BENEFIT_MAXIMUM = {
    "trip_cancellation": "trip_cancellation_max",
    "trip_interruption": "trip_cancellation_max",
    "trip_delay": "trip_delay_max",
    "baggage_delay": "baggage_delay_max",
    "baggage": "baggage_max",
    "passport": "passport_max",
    "emergency_medical": "emergency_medical_max",
}


def step(description: str, amount: float) -> CalculationStep:
    return CalculationStep(step=description, amount=round(amount, 2))


def limit_for(
    line: PayoutLine,
    policy: dict[str, Any],
    plan: dict[str, Any],
    add_ons: list[dict[str, Any]],
) -> float:
    travelers = len(policy["travelers"])
    match line.kind:
        case "item":
            return plan["per_item_limit"] * line.quantity
        case "electronics":
            electronics = next(
                (item for item in add_ons if item["add_on"] == "electronics_plus"),
                None,
            )
            per_item = (
                electronics["electronics_per_item_limit"]
                if electronics
                else plan["per_item_limit"]
            )
            return per_item * line.quantity
        case "hotel":
            rooms = ceil(travelers / 2)
            return HOTEL_NIGHT_LIMIT * rooms * line.nights
        case "meals_without_receipts":
            days = ceil(line.hours / 24)
            return MEALS_PER_TRAVELER_PER_DAY * travelers * days
        case _:
            return inf


def eligible_amount(
    line: PayoutLine,
    policy: dict[str, Any],
    plan: dict[str, Any],
    add_ons: list[dict[str, Any]],
) -> CalculationStep:
    limit = limit_for(line, policy, plan, add_ons)
    if line.amount > limit:
        return step(f"{line.description}, limited to ${limit:,.2f}", limit)
    return step(line.description, line.amount)


def benefit_maximum(
    policy: dict[str, Any], plan: dict[str, Any], benefit: Benefit
) -> float:
    maximum = plan[BENEFIT_MAXIMUM[benefit]]
    if benefit in ("trip_cancellation", "trip_interruption"):
        return min(maximum, policy["trip"]["trip_cost"])
    return maximum


def work_out_payout(
    bundle: dict[str, Any],
    benefit: Benefit,
    lines: list[PayoutLine],
    paid_by_others: float,
    approval_limit: float,
) -> PayoutResponse:
    policy = bundle["policy"]
    plan = bundle["plan"]
    add_ons = bundle["add_ons"]
    calculation = [
        eligible_amount(line, policy, plan, add_ons)
        for line in lines
        if line.kind != "cash"
    ]
    cash = sum(line.amount for line in lines if line.kind == "cash")
    if cash:
        calculation.append(
            step(f"Cash, up to ${CASH_LIMIT:,.0f} in all", min(cash, CASH_LIMIT))
        )
    total = sum(entry.amount for entry in calculation)

    if paid_by_others:
        calculation.append(
            step("Less what others have paid or refunded", -paid_by_others)
        )
        total -= paid_by_others
    if benefit in DEDUCTIBLE_BENEFITS and total > 0:
        deductible = min(plan["deductible"], total)
        if deductible:
            calculation.append(step("Less the deductible", -deductible))
            total -= deductible
    maximum = benefit_maximum(policy, plan, benefit)
    if total > maximum:
        calculation.append(
            step(
                f"Less the amount over the maximum of ${maximum:,.0f}", maximum - total
            )
        )
        total = maximum
    if total < 0:
        calculation.append(step("Nothing is left to pay", -total))
        total = 0.0

    payout = round(total, 2)
    return PayoutResponse(
        calculation=calculation,
        payout=payout,
        needs_sign_off=payout > approval_limit,
    )


def calculate_claim_payout(claim_id: str, request: PayoutRequest) -> PayoutResponse:
    bundle = get_claim_bundle(claim_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    with connect() as connection:
        limit = approval_limit(connection)
    result = work_out_payout(
        bundle,
        request.benefit,
        request.lines,
        request.paid_by_others,
        limit,
    )
    requirement = (
        "sign-off required" if result.needs_sign_off else "sign-off not required"
    )
    add_activity(claim_id, f"Calculated payout: ${result.payout:,.2f}; {requirement}")
    return result
