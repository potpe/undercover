from backend.models import Benefit, PayoutLine
from backend.payout import work_out_payout


def line(description, amount, kind="expense", **options):
    values = {"quantity": 1, "nights": 0, "hours": 0}
    values.update(options)
    return {
        "description": description,
        "amount": amount,
        "kind": kind,
        **values,
    }


def payout(client, claim_id, benefit, lines, paid_by_others=0):
    return client.post(
        f"/api/claims/{claim_id}/payout",
        json={
            "benefit": benefit,
            "lines": lines,
            "paid_by_others": paid_by_others,
        },
    )


def test_known_queue_payouts_use_backend_rules(client):
    delay = payout(
        client,
        "CLM-1001",
        "trip_delay",
        [
            line("Hotel", 245, "hotel", nights=1),
            line("Meals", 95),
            line("Local transport", 80),
        ],
    )
    medical = payout(
        client,
        "CLM-1003",
        "emergency_medical",
        [line("Hospital bill", 3800)],
    )
    passport = payout(
        client,
        "CLM-1005",
        "passport",
        [
            line("Emergency passport fee", 165),
            line("Photos", 18),
            line("Embassy taxis", 60),
        ],
    )

    assert delay.json()["payout"] == 420
    assert delay.json()["needs_sign_off"] is False
    assert medical.json()["payout"] == 3750
    assert medical.json()["needs_sign_off"] is True
    assert medical.json()["calculation"][-1] == {
        "step": "Less the deductible",
        "amount": -50,
    }
    assert passport.json()["payout"] == 243


def test_baggage_limits_compensation_and_deductible(client):
    response = payout(
        client,
        "CLM-1002",
        "baggage",
        [
            line("Hard-shell suitcase", 280, "item"),
            line("Wool suit", 420, "item"),
            line("Dress shoes, one pair", 160, "item"),
            line("Running shoes, one pair", 110, "item"),
            line("Toiletry kit", 60, "item"),
            line("Four shirts", 120, "item", quantity=4),
        ],
        400,
    )

    assert response.status_code == 200
    assert response.json()["payout"] == 580
    assert response.json()["calculation"][1] == {
        "step": "Wool suit, limited to $300.00",
        "amount": 300,
    }
    assert response.json()["calculation"][-2:] == [
        {"step": "Less what others have paid or refunded", "amount": -400},
        {"step": "Less the deductible", "amount": -50},
    ]
    activity = client.get("/api/claims/CLM-1002").json()["activity"]
    assert (
        activity[-1]["message"] == "Calculated payout: $580.00; sign-off not required"
    )


def synthetic_rules(
    lines, paid_by_others=0, add_ons=None, benefit: Benefit = "trip_delay"
):
    policy = {
        "plan": "test",
        "travelers": ["A", "B", "C"],
        "add_ons": [item["add_on"] for item in add_ons or []],
        "trip": {"trip_cost": 1200},
    }
    plan = {
        "per_item_limit": 300,
        "deductible": 50,
        "trip_cancellation_max": 5000,
        "trip_delay_max": 100000,
        "baggage_delay_max": 1000,
        "baggage_max": 5000,
        "passport_max": 500,
        "emergency_medical_max": 100000,
    }
    bundle = {"policy": policy, "plan": plan, "add_ons": add_ons or []}
    return work_out_payout(
        bundle,
        benefit,
        [PayoutLine(**item) for item in lines],
        paid_by_others,
        1000,
    ).model_dump()


def test_all_line_categories_and_electronics_add_on():
    assert synthetic_rules([line("Shirts", 1000, "item", quantity=3)])["payout"] == 900
    assert (
        synthetic_rules([line("Laptops", 2000, "electronics", quantity=2)])["payout"]
        == 600
    )
    assert (
        synthetic_rules(
            [line("Laptops", 2000, "electronics", quantity=2)],
            add_ons=[
                {"add_on": "electronics_plus", "electronics_per_item_limit": 1200}
            ],
        )["payout"]
        == 2000
    )
    assert (
        synthetic_rules([line("Cash one", 80, "cash"), line("Cash two", 50, "cash")])[
            "payout"
        ]
        == 100
    )
    assert synthetic_rules([line("Hotel", 2000, "hotel", nights=2)])["payout"] == 1400
    assert (
        synthetic_rules([line("Meals", 300, "meals_without_receipts", hours=25)])[
            "payout"
        ]
        == 210
    )
    assert synthetic_rules([line("Taxi", 42.236)])["payout"] == 42.24


def test_maximums_zero_floor_and_sign_off_boundary(client):
    capped = synthetic_rules([line("Delay", 1200)], benefit="baggage_delay")
    assert capped["payout"] == 1000
    assert capped["needs_sign_off"] is False
    trip_capped = synthetic_rules([line("Trip", 1300)], benefit="trip_cancellation")
    assert trip_capped["payout"] == 1200
    assert trip_capped["needs_sign_off"] is True

    nothing = synthetic_rules([line("Bill", 100)], paid_by_others=150)
    assert nothing["payout"] == 0
    assert nothing["calculation"][-1] == {
        "step": "Nothing is left to pay",
        "amount": 50,
    }

    at_limit = payout(client, "CLM-1003", "emergency_medical", [line("Bill", 1050)])
    over_limit = payout(
        client, "CLM-1003", "emergency_medical", [line("Bill", 1050.01)]
    )
    assert at_limit.json()["needs_sign_off"] is False
    assert at_limit.json()["payout"] == 1000
    assert over_limit.json()["needs_sign_off"] is True
    assert over_limit.json()["payout"] == 1000.01


def test_payout_rejects_unknown_claims_and_invalid_lines(client):
    unknown = payout(client, "CLM-9999", "trip_delay", [line("Taxi", 20)])
    invalid = payout(client, "CLM-1001", "trip_delay", [line("Taxi", 20, quantity=0)])
    missing = client.post(
        "/api/claims/CLM-1001/payout",
        json={"benefit": "trip_delay", "lines": []},
    )

    assert unknown.status_code == 404
    assert invalid.status_code == 422
    assert missing.status_code == 422
