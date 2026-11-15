from datetime import datetime

import pytest


def settings_payload(**changes):
    settings = {
        "approval_limit": 1000,
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
    }
    settings.update(changes)
    return settings


def decide(client, payout):
    return client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "approve",
            "payout": payout,
            "reason": "The covered expenses support this approval.",
            "policy_section": "Section 3; Section 10",
            "decided_by": "UnderCover Agent",
        },
    )


def test_settings_are_seeded_and_models_are_read_only(client):
    response = client.get("/api/settings")
    assert response.status_code == 200
    body = response.json()
    assert body["approval_limit"] == 1000
    assert body["objectives"] == {"accuracy": 0.6, "grade": 1.0}
    assert body["noise"]["seconds_per_claim"] == 0.15
    assert body["tolerance"]["cost_per_claim"] == 1.0
    assert body["spending_limit"] == 0.5
    assert body["evaluation_concurrency"] == 25
    assert [model["model_name"] for model in body["models_allowed"]] == [
        "openai/gpt-6-luna",
        "google/gemini-3.5-flash-lite",
        "deepseek/deepseek-v4.1-flash",
    ]
    assert body["models_allowed"][0]["input_price"] == 0.1


def test_settings_change_records_handler_and_applies_to_next_decision(client):
    held = decide(client, 1500)
    assert held.json()["claim"]["status"] == "waiting_for_sign_off"

    changed = client.put("/api/settings", json=settings_payload(approval_limit=2000))
    assert changed.status_code == 200
    assert changed.json()["approval_limit"] == 2000

    blocked = client.post("/api/claims/CLM-1001/payment", json={"amount": 1500})
    assert blocked.status_code == 409

    client.post(
        "/api/claims/CLM-1002/decision",
        json={
            "decision": "approve",
            "payout": 1500,
            "reason": "The covered expenses support this approval.",
            "policy_section": "Section 3; Section 10",
            "decided_by": "UnderCover Agent",
        },
    )
    claim = client.get("/api/claims/CLM-1002").json()
    assert claim["claim"]["status"] == "in_progress"
    changes = client.get("/api/settings/changes").json()
    assert len(changes) == 1
    assert changes[0]["key"] == "approval_limit"
    assert changes[0]["old_value"] == 1000
    assert changes[0]["new_value"] == 2000
    assert changes[0]["changed_by"] == "HND-01"
    assert datetime.fromisoformat(changes[0]["changed_at"]).tzinfo is not None


def test_marcus_cannot_change_settings(client):
    client.headers["Authorization"] = "Bearer marcus-token"
    response = client.put("/api/settings", json=settings_payload(approval_limit=2000))
    assert response.status_code == 403
    client.headers["Authorization"] = "Bearer dana-token"
    assert client.get("/api/settings").json()["approval_limit"] == 1000


@pytest.mark.parametrize("field", ["objectives", "noise", "tolerance"])
def test_all_improvement_setting_groups_can_change(client, field):
    value = settings_payload()[field]
    key = next(iter(value))
    value = {**value, key: value[key] + 0.01}
    response = client.put("/api/settings", json=settings_payload(**{field: value}))
    assert response.status_code == 200
    assert response.json()[field] == value
