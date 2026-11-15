import httpx

from backend import agent_service
from backend.tests.helpers import seed_working_calculation


def test_signoff_pays_atomically_without_contacting_agent_service(client, monkeypatch):
    seed_working_calculation("CLM-1003", 3750)
    decided = client.post(
        "/api/claims/CLM-1003/decision",
        json={
            "decision": "approve",
            "payout": 3750,
            "reason": "The injury is covered. The deductible produces this payout.",
            "section": "Section 7: Emergency Medical; Section 10: Payment",
            "decided_by": "UnderCover Agent",
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
    assert bundle["claim"]["status"] == "decided"
    assert bundle["signed_off_by"] == "HND-01"
    assert bundle["signed_off_at"] is not None
    assert bundle["paid_amount"] == 3750
    assert bundle["paid_at"] is not None
