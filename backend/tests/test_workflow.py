from datetime import datetime

import pytest
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql

from backend import payout as payout_module
from backend import workflow
from backend.database import claims_table, connect, settings_table
from backend.tests.helpers import seed_working_calculation


def decide(
    client,
    claim_id="CLM-1001",
    decision="approve",
    payout=420.0,
    decided_by="UnderCover Agent",
):
    request = {
        "decision": decision,
        "payout": payout,
        "reason": "The loss is covered. The policy wording supports this decision.",
        "section": "Section 3: Trip Delay",
    }
    request["decided_by"] = decided_by
    if decision == "approve":
        seed_working_calculation(claim_id, payout)
    return client.post(
        f"/api/claims/{claim_id}/decision",
        json=request,
    )


def pay(client, amount=420.0, claim_id="CLM-1001"):
    return client.post(f"/api/claims/{claim_id}/payment", json={"amount": amount})


def set_approval_limit(amount):
    with connect() as connection:
        company = connection.execute(
            select(settings_table.c.data).where(settings_table.c.key == "company")
        ).scalar_one()
        connection.execute(
            settings_table.update()
            .where(settings_table.c.key == "company")
            .values(data={**company, "payout_approval_limit": amount})
        )


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


def test_decision_api_persists_supplied_configuration_maker(client):
    response = decide(
        client,
        decided_by="UnderCover Agent (configuration version 7)",
    )

    assert response.status_code == 200
    assert response.json()["decision"]["decided_by"] == (
        "UnderCover Agent (configuration version 7)"
    )
    assert (
        client.get("/api/claims/CLM-1001").json()["decision"]["decided_by"]
        == "UnderCover Agent (configuration version 7)"
    )


def test_decision_api_persists_security_check_maker(client):
    response = decide(
        client,
        decision="refer",
        payout=0.0,
        decided_by="Security check",
    )

    assert response.status_code == 200
    assert response.json()["decision"]["decided_by"] == "Security check"


def test_decision_replay_compares_maker(client):
    original = decide(client)
    assert original.status_code == 200
    replay = decide(client, decided_by="UnderCover Agent (configuration version 2)")

    assert replay.status_code == 409
    assert replay.json()["detail"] == "Claim already has a decision"
    assert (
        client.get("/api/claims/CLM-1001").json()["decision"]["decided_by"]
        == "UnderCover Agent"
    )


@pytest.mark.parametrize("decision", ["decline", "refer"])
def test_declines_and_referrals_are_decided_without_payment(client, decision):
    response = decide(client, decision=decision, payout=0.0)

    assert response.status_code == 200
    assert response.json()["claim"]["status"] == "decided"
    assert response.json()["decision"]["decision"] == decision
    assert response.json()["decision"]["calculation"] is None
    assert response.json()["decision"]["manual_version"] == "2026.2"
    assert pay(client, amount=0.0).status_code == 422


def test_over_limit_payment_waits_for_sign_off(client):
    decide(client, claim_id="CLM-1003", payout=3750.0)

    blocked = pay(client, claim_id="CLM-1003", amount=3750.0)
    assert blocked.status_code == 403
    assert blocked.json() == {
        "detail": "Payment exceeds the current approval limit of $1,000.00"
    }
    bundle = client.get("/api/claims/CLM-1003").json()
    assert bundle["claim"]["status"] == "waiting_for_sign_off"
    assert bundle["paid_amount"] is None
    assert not any("Payment blocked" in item["message"] for item in bundle["activity"])

    sign_off = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    assert sign_off.status_code == 200
    assert sign_off.json()["signed_off_by"] == "HND-01"
    assert sign_off.json()["paid_amount"] == 3750.0
    assert sign_off.json()["signed_off_at"] is not None
    assert sign_off.json()["paid_at"] is not None
    assert sign_off.json()["claim"]["status"] == "decided"

    assert pay(client, claim_id="CLM-1003", amount=3750.0).status_code == 403
    repeated = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    assert repeated.status_code == 200
    assert repeated.json()["paid_amount"] == 3750.0
    activity = [entry["message"] for entry in repeated.json()["activity"]]
    assert activity.count("Payment signed off by Dana Whitfield") == 1
    assert activity.count("Payment recorded: $3,750.00") == 1


def test_signoff_and_payment_roll_back_together_on_payment_failure(client, monkeypatch):
    decide(client, claim_id="CLM-1003", payout=3750.0)
    before = client.get("/api/claims/CLM-1003").json()

    def fail_payment(*args, **kwargs):
        raise RuntimeError("Injected payment failure")

    monkeypatch.setattr(workflow, "_record_payment", fail_payment)
    with pytest.raises(RuntimeError, match="Injected payment failure"):
        client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})

    after = client.get("/api/claims/CLM-1003").json()
    assert after == before
    assert after["signed_off_by"] is None
    assert after["signed_off_at"] is None
    assert after["paid_amount"] is None
    assert after["paid_at"] is None
    assert after["claim"]["status"] == "waiting_for_sign_off"


def test_gateway_hold_and_handler_signoff_pay_atomically_once(client):
    set_approval_limit(5000)
    approved = decide(client, claim_id="CLM-1003", payout=3750.0)
    assert approved.json()["claim"]["status"] == "in_progress"

    held = client.post(
        "/api/internal/claims/CLM-1003/gateway-hold",
        json={"reason": "Gateway rejected the payment request"},
    )
    assert held.status_code == 200
    assert held.json()["claim"]["status"] == "waiting_for_sign_off"
    assert held.json()["gateway_refused"] is True
    assert held.json()["sign_off_required"] is True
    assert (
        held.json()["gateway_refusal_reason"] == "Gateway rejected the payment request"
    )
    assert held.json()["paid_amount"] is None

    signed = client.post("/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"})
    repeated = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )
    assert signed.status_code == repeated.status_code == 200
    assert signed.json()["paid_amount"] == repeated.json()["paid_amount"] == 3750.0
    assert signed.json()["claim"]["status"] == "decided"
    messages = [entry["message"] for entry in repeated.json()["activity"]]
    assert messages.count("Payment signed off by Dana Whitfield") == 1
    assert messages.count("Payment recorded: $3,750.00") == 1


@pytest.mark.parametrize(
    ("claim_id", "decision", "payout", "paid"),
    [
        ("CLM-1001", None, None, False),
        ("CLM-1001", "decline", 0.0, False),
        ("CLM-1001", "approve", 420.0, True),
    ],
)
def test_gateway_hold_rejects_invalid_claim_transitions(
    client, claim_id, decision, payout, paid
):
    if decision is not None:
        decide(client, claim_id=claim_id, decision=decision, payout=payout)
    if paid:
        pay(client, claim_id=claim_id)

    response = client.post(
        f"/api/internal/claims/{claim_id}/gateway-hold",
        json={"reason": "Gateway refused payment"},
    )

    assert response.status_code == 409
    bundle = client.get(f"/api/claims/{claim_id}").json()
    assert bundle["gateway_refused"] is False
    assert bundle["gateway_refusal_reason"] is None


def test_gateway_hold_rejects_already_held_app_approval_without_overwriting_cause(
    client,
):
    decide(client, claim_id="CLM-1003", payout=3750.0)
    before = client.get("/api/claims/CLM-1003").json()
    assert before["claim"]["status"] == "waiting_for_sign_off"
    assert before["sign_off_required"] is True
    assert before["gateway_refused"] is False
    assert before["gateway_refusal_reason"] is None

    response = client.post(
        "/api/internal/claims/CLM-1003/gateway-hold",
        json={"reason": "Gateway refused payment"},
    )

    assert response.status_code == 409
    after = client.get("/api/claims/CLM-1003").json()
    assert after["claim"]["status"] == "waiting_for_sign_off"
    assert after["sign_off_required"] is True
    assert after["gateway_refused"] is False
    assert after["gateway_refusal_reason"] is None


def test_gateway_hold_returns_not_found_for_unknown_claim(client):
    response = client.post(
        "/api/internal/claims/CLM-9999/gateway-hold",
        json={"reason": "Gateway refused payment"},
    )

    assert response.status_code == 404


def test_gateway_hold_rejects_in_progress_claim_without_approval(client):
    started = client.post(
        "/api/internal/claims/CLM-1001/work", json={"thread_id": "hold-test"}
    )
    assert started.status_code == 200

    response = client.post(
        "/api/internal/claims/CLM-1001/gateway-hold",
        json={"reason": "Gateway refused payment"},
    )

    assert response.status_code == 409
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["claim"]["status"] == "in_progress"
    assert bundle["decision"] is None
    assert bundle["gateway_refused"] is False


def test_gateway_hold_rejects_referred_claim(client):
    decide(client, decision="refer", payout=0.0)

    response = client.post(
        "/api/internal/claims/CLM-1001/gateway-hold",
        json={"reason": "Gateway refused payment"},
    )

    assert response.status_code == 409
    bundle = client.get("/api/claims/CLM-1001").json()
    assert bundle["claim"]["status"] == "decided"
    assert bundle["gateway_refused"] is False
    assert bundle["gateway_refusal_reason"] is None


@pytest.mark.parametrize("reason", ["", "   "])
def test_gateway_hold_requires_nonblank_reason(client, reason):
    decide(client, claim_id="CLM-1003", payout=3750.0)

    response = client.post(
        "/api/internal/claims/CLM-1003/gateway-hold", json={"reason": reason}
    )

    assert response.status_code == 422
    assert client.get("/api/claims/CLM-1003").json()["gateway_refused"] is False


def test_gateway_hold_requires_agent_key_even_for_handler(client):
    decide(client, claim_id="CLM-1003", payout=3750.0)
    client.headers.pop("X-API-Key")

    response = client.post(
        "/api/internal/claims/CLM-1003/gateway-hold",
        json={"reason": "Gateway refused payment"},
    )

    assert response.status_code == 401
    assert client.get("/api/claims/CLM-1003").json()["gateway_refused"] is False


def test_exactly_one_thousand_dollars_does_not_need_sign_off(client):
    decide(client, payout=1000.0)

    response = pay(client, amount=1000.0)

    assert response.status_code == 200
    assert response.json()["claim"]["status"] == "decided"


def test_over_limit_payment_is_rejected_before_claim_lookup(client):
    unknown = pay(client, amount=1000.01, claim_id="CLM-9999")
    waiting = pay(client, amount=1000.01, claim_id="CLM-1001")

    assert unknown.status_code == waiting.status_code == 403
    assert (
        unknown.json()
        == waiting.json()
        == {"detail": "Payment exceeds the current approval limit of $1,000.00"}
    )


def test_over_limit_payment_guard_reads_changed_setting(client):
    changed = client.put(
        "/api/settings",
        json={
            "approval_limit": 500,
            "objectives": {"accuracy": 0.6, "grade": 1.0},
            "noise": {
                "accuracy": 0.1,
                "grade": 0.15,
                "cost_per_claim": 0.15,
                "seconds_per_claim": 0.15,
            },
            "tolerance": {
                "accuracy": 0.1,
                "grade": 0.15,
                "cost_per_claim": 1.0,
                "seconds_per_claim": 0.25,
            },
            "spending_limit": 0.5,
            "evaluation_concurrency": 25,
        },
    )
    assert changed.status_code == 200
    unknown = pay(client, amount=500.01, claim_id="CLM-9999")
    assert unknown.status_code == 403
    assert unknown.json() == {
        "detail": "Payment exceeds the current approval limit of $500.00"
    }


def test_payment_must_equal_the_approved_payout(client):
    decide(client)

    response = pay(client, amount=400.0)

    assert response.status_code == 409
    assert response.json() == {"detail": "Payment must equal the approved payout"}
    assert client.get("/api/claims/CLM-1001").json()["paid_amount"] is None


def test_ineligible_signed_in_handler_cannot_sign_off(client):
    decide(client, claim_id="CLM-1003", payout=3750.0)
    client.headers["Authorization"] = "Bearer marcus-token"

    ineligible = client.post("/api/claims/CLM-1003/sign-off")

    assert ineligible.status_code == 403
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


def test_repeated_decision_and_payment_return_the_unchanged_claim(client):
    decide(client)
    paid = pay(client)
    original = client.get("/api/claims/CLM-1001").json()

    repeated_decision = decide(client)
    repeated_payment = pay(client)

    assert repeated_decision.status_code == 200
    assert repeated_decision.json() == original
    assert repeated_payment.status_code == 200
    assert repeated_payment.json() == original
    assert repeated_payment.json() == paid.json()
    assert client.get("/api/claims/CLM-1001").json() == original


def test_different_decision_or_payment_on_completed_claim_is_rejected(client):
    decide(client, decision="decline", payout=0)
    original = client.get("/api/claims/CLM-1001").json()["decision"]

    changed = decide(client)

    assert changed.status_code == 409
    assert changed.json() == {"detail": "Claim already has a decision"}
    assert client.get("/api/claims/CLM-1001").json()["decision"] == original

    decide(client, claim_id="CLM-1002")
    pay(client, claim_id="CLM-1002")
    different_payment = pay(client, amount=419, claim_id="CLM-1002")

    assert different_payment.status_code == 409
    assert different_payment.json() == {
        "detail": "Payment must equal the approved payout"
    }
    assert client.get("/api/claims/CLM-1002").json()["paid_amount"] == 420


def test_nonzero_decline_is_rejected(client):
    response = decide(client, decision="decline", payout=10.0)

    assert response.status_code == 422
    assert "section 14" in str(response.json()).lower()


def test_workflow_actions_add_ordered_activity_once(client):
    decide(client)
    pay(client)
    decide(client)
    pay(client)

    messages = [
        entry["message"]
        for entry in client.get("/api/claims/CLM-1001").json()["activity"]
    ]

    assert messages == [
        "Decision recorded: approve for $420.00",
        "Payment recorded: $420.00",
    ]


def test_postgres_claim_guards_lock_the_row_for_atomic_writes():
    statement = (
        select(claims_table)
        .where(claims_table.c.claim_id == "CLM-1001")
        .with_for_update()
    )

    assert "FOR UPDATE" in statement.compile(dialect=postgresql.dialect()).string


def test_workflow_returns_404_for_unknown_claim(client):
    assert decide(client, claim_id="CLM-9999").status_code == 404
    assert pay(client, claim_id="CLM-9999").status_code == 404
    assert (
        client.post(
            "/api/claims/CLM-9999/sign-off", json={"handler_id": "HND-01"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("change", "part"),
    [
        ({"reason": ""}, "reason"),
        ({"reason": " \t"}, "reason"),
        ({"section": ""}, "section"),
        ({"section": " \t"}, "section"),
        ({"decided_by": ""}, "decided_by"),
        ({"decided_by": " \t"}, "decided_by"),
        ({"payout": -1}, "payout"),
        ({"payout": 0}, "payout"),
        ({"decision": "decline", "payout": 1}, "payout"),
        ({"decision": "refer", "payout": 1}, "payout"),
        ({"decision": "refer", "payout": -1}, "payout"),
    ],
)
def test_section_14_request_validation_precedes_claim_lookup(client, change, part):
    request = {
        "decision": "approve",
        "payout": 420,
        "reason": "Covered loss.",
        "section": "Section 3",
        "decided_by": "UnderCover Agent",
        **change,
    }
    if request["decision"] != "approve" and request["payout"] < 0:
        request["decision"] = "refer"
    known = client.post("/api/claims/CLM-1001/decision", json=request)
    unknown = client.post("/api/claims/CLM-9999/decision", json=request)

    assert known.status_code == unknown.status_code == 422
    assert known.json() == unknown.json()
    assert "section 14" in str(known.json()).lower()
    assert part in str(known.json()).lower()
    assert client.get("/api/claims/CLM-1001").json()["decision"] is None


def test_approval_requires_current_calculation_and_exact_payout(client):
    request = {
        "decision": "approve",
        "payout": 420,
        "reason": "Covered loss.",
        "section": "Section 3",
        "decided_by": "UnderCover Agent",
    }
    missing = client.post("/api/claims/CLM-1001/decision", json=request)
    assert missing.status_code == 422
    assert "section 14" in missing.json()["detail"].lower()
    assert "calculation" in missing.json()["detail"].lower()

    seed_working_calculation("CLM-1001", 419)
    mismatch = client.post("/api/claims/CLM-1001/decision", json=request)
    assert mismatch.status_code == 422
    assert "section 14" in mismatch.json()["detail"].lower()
    assert "payout" in mismatch.json()["detail"].lower()

    seed_working_calculation("CLM-1001", 420)
    response = client.post("/api/claims/CLM-1001/decision", json=request)
    assert response.status_code == 200
    decision = response.json()["decision"]
    assert decision["calculation"] == {
        "lines": [{"step": "Test payout", "amount": 420}],
        "payout": 420,
    }
    assert decision["section"] == "Section 3"
    assert decision["decided_by"] == "UnderCover Agent"
    assert decision["manual_version"] == "2026.2"
    decided_at = datetime.fromisoformat(decision["decided_at"])
    assert decided_at.tzinfo is not None
    offset = decided_at.utcoffset()
    assert offset is not None
    assert offset.total_seconds() == 0
    assert "policy_section" not in decision


def test_latest_calculation_is_authoritative_and_replay_is_immutable(client):
    payout_request = {
        "benefit": "trip_delay",
        "lines": [
            {
                "description": "Hotel",
                "amount": 150,
                "kind": "hotel",
                "quantity": 1,
                "nights": 1,
                "hours": 0,
            }
        ],
        "paid_by_others": 0,
    }
    first = client.post("/api/claims/CLM-1001/payout", json=payout_request)
    payout_request["lines"][0]["amount"] = 175
    latest = client.post("/api/claims/CLM-1001/payout", json=payout_request)
    assert first.status_code == latest.status_code == 200

    request = {
        "decision": "approve",
        "payout": latest.json()["payout"],
        "reason": "Covered hotel expense.",
        "section": "Section 3",
        "decided_by": "UnderCover Agent",
    }
    accepted = client.post("/api/claims/CLM-1001/decision", json=request)
    snapshot = accepted.json()["decision"]
    payout_request["lines"][0]["amount"] = 200
    recalculated = client.post("/api/claims/CLM-1001/payout", json=payout_request)
    assert recalculated.status_code == 200
    replay = client.post("/api/claims/CLM-1001/decision", json=request)
    assert replay.json()["decision"] == snapshot
    assert snapshot["calculation"] == {
        "lines": latest.json()["calculation"],
        "payout": latest.json()["payout"],
    }


def test_attempt_advance_during_calculation_refuses_stale_result(client, monkeypatch):
    calculate = payout_module.work_out_payout

    def advance_attempt(*args, **kwargs):
        result = calculate(*args, **kwargs)
        with connect() as connection:
            connection.execute(
                update(claims_table)
                .where(claims_table.c.claim_id == "CLM-1001")
                .values(failed_attempts=1)
            )
        return result

    monkeypatch.setattr(payout_module, "work_out_payout", advance_attempt)
    payout_request = {
        "benefit": "trip_delay",
        "lines": [
            {
                "description": "Hotel",
                "amount": 150,
                "kind": "hotel",
                "quantity": 1,
                "nights": 1,
                "hours": 0,
            }
        ],
        "paid_by_others": 0,
    }
    response = client.post("/api/claims/CLM-1001/payout", json=payout_request)
    assert response.status_code == 409
    assert response.json()["detail"] == (
        "Claim attempt changed while payout was being calculated"
    )

    with connect() as connection:
        current = connection.execute(
            select(
                claims_table.c.current_calculation_attempt,
                claims_table.c.current_calculation,
            ).where(claims_table.c.claim_id == "CLM-1001")
        ).one()
    assert current.current_calculation_attempt is None
    assert current.current_calculation is None
    rejected = client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "approve",
            "payout": response.json().get("payout", 150),
            "reason": "Covered hotel expense.",
            "section": "Section 3",
            "decided_by": "UnderCover Agent",
        },
    )
    assert rejected.status_code == 422
    assert "current-attempt" in rejected.json()["detail"]


def test_fresh_attempt_cannot_reuse_previous_attempt_calculation(client):
    seed_working_calculation("CLM-1001", 420)
    with connect() as connection:
        connection.execute(
            claims_table.update()
            .where(claims_table.c.claim_id == "CLM-1001")
            .values(failed_attempts=1)
        )
    response = client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "approve",
            "payout": 420,
            "reason": "Covered loss.",
            "section": "Section 3",
            "decided_by": "UnderCover Agent",
        },
    )
    assert response.status_code == 422
    assert "section 14" in response.json()["detail"].lower()
