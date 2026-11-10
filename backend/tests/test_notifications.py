import httpx

from backend import agent_service


def test_signoff_stands_when_agent_service_is_unavailable(client, monkeypatch):
    decided = client.post(
        "/api/claims/CLM-1003/decision",
        json={
            "decision": "approve",
            "payout": 3750,
            "reason": "The injury is covered. The deductible produces this payout.",
            "policy_section": "Section 7: Emergency Medical; Section 10: Payment",
        },
    )
    assert decided.status_code == 200

    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("Agent offline")

    monkeypatch.setattr(agent_service, "agent_post", unavailable)
    response = client.post(
        "/api/claims/CLM-1003/sign-off", json={"handler_id": "HND-01"}
    )

    assert response.status_code == 200
    bundle = response.json()
    assert bundle["claim"]["status"] == "in_progress"
    assert bundle["signed_off_by"] == "HND-01"
    assert bundle["signed_off_at"] is not None
    assert bundle["paid_amount"] is None
