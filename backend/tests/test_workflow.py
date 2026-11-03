import json

import pytest

from backend.database import connect


def decide(client, claim_id="CLM-1001", decision="approve", payout=420.0):
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json={
            "decision": decision,
            "payout": payout,
            "reason": "The loss is covered. The policy wording supports this decision.",
            "policy_section": "Section 3: Trip Delay",
        },
    )


def pay(client, amount=420.0, claim_id="CLM-1001"):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def test_small_approval_is_decided_only_after_payment(client):
    decision = decide(client)
    assert decision.status_code == 200
    assert decision.json()["claim"]["status"] == "in_progress"
    assert decision.json()["decision"]["payout"] == 420.0
    assert decision.json()["paid_amount"] is None

    payment = pay(client)
    assert payment.status_code == 200
    assert payment.json()["claim"]["status"] == "decided"
    assert payment.json()["paid_amount"] == 420.0


@pytest.mark.parametrize("decision", ["decline", "refer"])
def test_declines_and_referrals_are_decided_without_payment(client, decision):
    response = decide(client, decision=decision, payout=0.0)

    assert response.status_code == 200
    assert response.json()["claim"]["status"] == "decided"
    assert response.json()["decision"]["decision"] == decision
    assert pay(client, amount=0.0).status_code == 422


def test_over_limit_payment_waits_for_sign_off(client):
    decide(client, claim_id="CLM-1003", payout=3750.0)

    blocked = pay(client, claim_id="CLM-1003", amount=3750.0)
    assert blocked.status_code == 409
    assert blocked.json() == {
        "detail": "Payout over $1,000.00 requires handler sign-off"
    }
    bundle = client.get("/api/claims/CLM-1003").json()
    assert bundle["claim"]["status"] == "waiting_for_sign_off"
    assert bundle["paid_amount"] is None
    assert bundle["activity"][-1]["message"] == (
        "Payment blocked: Payout over $1,000.00 requires handler sign-off"
    )

    sign_off = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    assert sign_off.status_code == 200
    assert sign_off.json()["signed_off_by"] == "HND-01"
    assert sign_off.json()["paid_amount"] is None
    assert sign_off.json()["claim"]["status"] == "waiting_for_sign_off"

    paid = pay(client, claim_id="CLM-1003", amount=3750.0)
    assert paid.status_code == 200
    assert paid.json()["claim"]["status"] == "decided"
    assert paid.json()["paid_amount"] == 3750.0


def test_exactly_one_thousand_dollars_does_not_need_sign_off(client):
    decide(client, payout=1000.0)

    response = pay(client, amount=1000.0)

    assert response.status_code == 200
    assert response.json()["claim"]["status"] == "decided"


def test_payment_must_equal_the_approved_payout(client):
    decide(client)

    response = pay(client, amount=400.0)

    assert response.status_code == 409
    assert response.json() == {"detail": "Payment must equal the approved payout"}
    assert client.get("/api/claims/CLM-1001").json()["paid_amount"] is None


def test_unknown_and_ineligible_handlers_cannot_sign_off(client):
    decide(client, claim_id="CLM-1003", payout=3750.0)
    unknown = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-99"}
    )
    assert unknown.status_code == 404

    with connect() as connection:
        handler = json.loads(
            connection.execute(
                "SELECT data FROM handlers WHERE handler_id = 'HND-02'"
            ).fetchone()["data"]
        )
        handler["can_approve_over_limit"] = False
        connection.execute(
            "UPDATE handlers SET data = ? WHERE handler_id = 'HND-02'",
            (json.dumps(handler),),
        )
    ineligible = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-02"}
    )
    assert ineligible.status_code == 409
    assert ineligible.json() == {"detail": "Handler cannot approve over-limit payouts"}
    assert client.get("/api/claims/CLM-1003").json()["signed_off_by"] is None


def test_completed_claim_cannot_be_decided_again(client):
    decide(client, decision="decline", payout=0.0)
    original = client.get("/api/claims/CLM-1001").json()["decision"]

    response = decide(client)

    assert response.status_code == 409
    assert response.json() == {"detail": "Claim already has a decision"}
    assert client.get("/api/claims/CLM-1001").json()["decision"] == original


def test_payment_requires_an_approval(client):
    response = pay(client)

    assert response.status_code == 409
    assert response.json() == {"detail": "Claim has no approval to pay"}


def test_declined_claim_cannot_be_paid(client):
    decide(client, decision="decline", payout=0.0)

    response = pay(client, amount=1.0)

    assert response.status_code == 409
    assert response.json() == {"detail": "Claim has no approval to pay"}


def test_negative_payout_is_rejected_without_changing_the_claim(client):
    response = decide(client, payout=-1.0)

    assert response.status_code == 422
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["decision"] is None
    assert bundle["claim"]["status"] == "waiting"


def test_duplicate_payment_is_rejected(client):
    decide(client)
    pay(client)

    response = pay(client)

    assert response.status_code == 409
    assert response.json() == {"detail": "Claim already has a payment"}
    assert client.get("/api/claims/CLM-1001").json()["paid_amount"] == 420.0


def test_nonzero_decline_is_rejected(client):
    response = decide(client, decision="decline", payout=10.0)

    assert response.status_code == 409
    assert response.json() == {"detail": "Declines and referrals must have zero payout"}


def test_workflow_actions_add_ordered_activity(client):
    decide(client)
    pay(client)
    pay(client)

    messages = [
        entry["message"]
        for entry in client.get("/api/claims/CLM-1001").json()["activity"]
    ]

    assert messages == [
        "Decision recorded: approve for $420.00",
        "Payment recorded: $420.00",
        "Payment rejected: Claim already has a payment",
    ]


def test_workflow_returns_404_for_unknown_claim(client):
    assert decide(client, claim_id="CLM-9999").status_code == 404
    assert pay(client, claim_id="CLM-9999").status_code == 404
    assert (
        client.post(
            "/api/claims/CLM-9999/sign-off", json={"handler_id": "HND-01"}
        ).status_code
        == 404
    )
