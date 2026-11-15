from typing import Any


def calculate_approval(
    client: Any, claim_id: str, expected_payout: float
) -> dict[str, Any]:
    """Calculate a matching emergency-medical expense for an API-played approval."""
    bundle = client.get(f"/api/claims/{claim_id}")
    bundle.raise_for_status()
    deductible = bundle.json()["plan"]["deductible"]
    response = client.post(
        f"/api/claims/{claim_id}/payout",
        json={
            "benefit": "emergency_medical",
            "lines": [
                {
                    "description": "Test-covered medical expense",
                    "amount": expected_payout + deductible,
                    "quantity": 1,
                    "kind": "expense",
                    "nights": 0,
                    "hours": 0,
                }
            ],
            "paid_by_others": 0,
        },
    )
    response.raise_for_status()
    calculation = response.json()
    assert calculation["payout"] == expected_payout
    return calculation
