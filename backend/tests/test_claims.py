from backend.database import add_activity, initialize_database


def test_queue_contains_only_the_25_development_claims(client):
    response = client.get("/api/claims")

    assert response.status_code == 200
    claims = response.json()
    assert len(claims) == 25
    assert [claim["claim_id"] for claim in claims] == [
        f"CLM-{number}" for number in range(1001, 1026)
    ]
    assert {claim["status"] for claim in claims} == {"waiting"}
    assert claims[0] == {
        "claim_id": "CLM-1001",
        "customer_name": "Maya Patel",
        "claim_type": "trip_delay",
        "summary": "Flight home canceled by the airline, rebooked the next morning",
        "amount_claimed": 420.0,
        "status": "waiting",
        "decision": None,
        "payout": None,
    }


def test_claim_details_include_the_complete_claim_bundle(client):
    response = client.get("/api/claims/CLM-1001")

    assert response.status_code == 200
    bundle = response.json()
    assert bundle["claim"]["story"].startswith("My flight home from Rome")
    assert len(bundle["claim"]["items"]) == 3
    assert bundle["claim"]["amount_claimed"] == 420.0
    assert len(bundle["claim"]["documents"]) == 4
    assert bundle["claim"]["status"] == "waiting"
    assert bundle["policy"]["policy_id"] == "POL-2001"
    assert bundle["plan"]["name"] == "UnderCover Premium"
    assert bundle["plan"]["deductible"] == 50.0
    assert bundle["add_ons"] == []
    assert bundle["customer"]["customer_id"] == "CUS-3001"
    assert bundle["customer"]["previous_claims"][0]["claim_id"] == "CLM-0412"
    assert bundle["decision"] is None
    assert bundle["paid_amount"] is None
    assert bundle["signed_off_by"] is None
    assert bundle["activity"] == []


def test_medical_claim_exposes_bill_and_plan_deductible(client):
    bundle = client.get("/api/claims/CLM-1003").json()

    assert bundle["claim"]["items"][0]["amount"] == 3800.0
    assert bundle["plan"]["deductible"] == 50.0
    assert bundle["plan"]["emergency_medical_max"] == 100000.0


def test_add_ons_are_resolved_for_the_policy(client):
    bundle = client.get("/api/claims/CLM-1009").json()

    assert bundle["add_ons"] == [
        {
            "add_on": "electronics_plus",
            "name": "Electronics Plus",
            "effect": "Raises the per-item limit for electronics to the amount below.",
            "electronics_per_item_limit": 1200.0,
        }
    ]


def test_handlers_are_available(client):
    handlers = client.get("/api/handlers")

    assert [handler["name"] for handler in handlers.json()] == [
        "Dana Whitfield",
        "Marcus Oyelaran",
        "Priya Raman",
    ]


def test_unknown_claim_returns_404(client):
    response = client.get("/api/claims/CLM-9999")

    assert response.status_code == 404
    assert response.json() == {"detail": "Claim not found"}


def test_activity_is_returned_in_insertion_order(client):
    add_activity("CLM-1001", "First step")
    add_activity("CLM-1001", "Second step")

    activity = client.get("/api/claims/CLM-1001").json()["activity"]

    assert [entry["message"] for entry in activity] == ["First step", "Second step"]
    assert activity[0]["id"] < activity[1]["id"]
    assert all(entry["created_at"] for entry in activity)


def test_initialization_does_not_duplicate_seed_records(client):
    initialize_database()
    initialize_database()

    assert len(client.get("/api/claims").json()) == 25
