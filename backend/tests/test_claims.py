from backend.database import add_activity, initialize_database


def test_queue_contains_the_25_development_claims_and_security_claim(client):
    response = client.get("/api/claims")

    assert response.status_code == 200
    claims = response.json()
    assert len(claims) == 26
    assert [claim["claim_id"] for claim in claims] == [
        *(f"CLM-{number}" for number in range(1001, 1026)),
        "CLM-1901",
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
    security_claim = client.get("/api/claims/CLM-1901").json()
    assert security_claim["claim"]["policy_id"] == "POL-2901"
    assert security_claim["claim"]["customer_id"] == "CUS-3901"
    assert security_claim["customer"]["name"] == "Derek Vance"
    assert security_claim["policy"]["policy_id"] == "POL-2901"
    assert security_claim["claim"]["status"] == "waiting"


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
    assert bundle["signed_off_by_name"] is None
    assert bundle["signed_off_at"] is None
    assert bundle["paid_at"] is None
    assert bundle["agent_thread_id"] is None
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
    assert all(entry["kind"] == "activity" for entry in activity)
    assert all(entry["details"] is None for entry in activity)


def test_legacy_activity_endpoint_keeps_message_contract(client):
    response = client.post(
        "/api/claims/CLM-1001/activity", json={"message": "Existing bookkeeping"}
    )

    assert response.status_code == 200
    entry = response.json()["activity"][-1]
    assert entry == {
        "id": entry["id"],
        "created_at": entry["created_at"],
        "message": "Existing bookkeeping",
        "kind": "activity",
        "details": None,
    }


def test_typed_claim_steps_share_the_ordered_activity_feed(client):
    steps = [
        {
            "kind": "tool",
            "message": "Called claim tool",
            "details": {"name": "get_claim", "input": {"claim_id": "CLM-1001"}},
        },
        {
            "kind": "lookup",
            "message": "Looked up policy",
            "details": {
                "operation": "search_documents",
                "input": {"query": "trip delay"},
                "sources": [
                    {
                        "file": "policy_wording.md",
                        "system": "Published policy documents",
                        "owner": "Legal",
                        "reviewed": "2026-09-01",
                    },
                    {
                        "file": "claims_manual.md",
                        "system": "Claims operations",
                        "owner": "Head of claims operations",
                        "reviewed": "2026-09-01",
                    },
                ],
            },
        },
        {
            "kind": "memory_search",
            "message": "Searched customer memory",
            "details": {"query": "trip delay", "results": ["Prior delay claim"]},
        },
        {
            "kind": "memory_save",
            "message": "Saved useful memory",
            "details": {"messages": ["Customer has a prior delay claim."]},
        },
        {
            "kind": "refusal",
            "message": "Tool request was refused",
            "details": {
                "source": "gateway",
                "reason": "Payment is not available to the agent.",
                "operation": "pay_claim",
            },
        },
    ]
    responses = [client.post("/api/claims/CLM-1001/steps", json=step) for step in steps]

    assert all(response.status_code == 200 for response in responses)
    feed = responses[-1].json()["activity"]
    assert [entry["kind"] for entry in feed] == [step["kind"] for step in steps]
    assert [entry["message"] for entry in feed] == [step["message"] for step in steps]
    assert [entry["details"] for entry in feed] == [step["details"] for step in steps]
    expected_fields = {"id", "created_at", "message", "kind", "details"}
    assert all(set(entry) == expected_fields for entry in feed)
    assert all(entry["created_at"] for entry in feed)
    assert [entry["id"] for entry in feed] == sorted(entry["id"] for entry in feed)


def test_typed_claim_steps_reject_invalid_shape_and_unknown_claim(client):
    malformed = {
        "kind": "lookup",
        "message": "Lookup with incomplete source",
        "details": {
            "operation": "search_documents",
            "input": {},
            "sources": [{"file": "policy_wording.md"}],
        },
    }
    assert client.post("/api/claims/CLM-1001/steps", json=malformed).status_code == 422
    assert (
        client.post(
            "/api/claims/CLM-1001/steps",
            json={"kind": "unknown", "message": "Bad", "details": {}},
        ).status_code
        == 422
    )
    valid = {
        "kind": "memory_save",
        "message": "Saved memory",
        "details": {"messages": ["Useful detail"]},
    }
    assert client.post("/api/claims/CLM-9999/steps", json=valid).status_code == 404


def test_initialization_does_not_duplicate_seed_records(client):
    initialize_database()
    initialize_database()

    assert len(client.get("/api/claims").json()) == 26
