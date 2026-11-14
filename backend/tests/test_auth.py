from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.routing import APIRoute

from backend.auth import (
    agent_key,
    agent_or_handler,
    current_handler,
    decode_token,
    senior_handler,
)
from backend.improvement_routes import router as improvement_router
from backend.main import app


def signing_material():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pem, private.public_key()


def token_for(private, **changes):
    claims = {
        "aud": "authenticated",
        "email": "dana.whitfield@undercover.example",
        "exp": datetime.now(UTC) + timedelta(minutes=5),
        **changes,
    }
    return jwt.encode(claims, private, algorithm="RS256")


def test_decode_token_verifies_signature_audience_and_expiry():
    private, public = signing_material()

    class Keys:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public)

    assert decode_token(token_for(private), Keys())["email"] == (
        "dana.whitfield@undercover.example"
    )
    with pytest.raises(HTTPException) as wrong_audience:
        decode_token(token_for(private, aud="wrong"), Keys())
    assert wrong_audience.value.status_code == 401
    with pytest.raises(HTTPException) as expired:
        decode_token(
            token_for(private, exp=datetime.now(UTC) - timedelta(minutes=1)), Keys()
        )
    assert expired.value.status_code == 401


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("post", "/api/dispatches", {"concurrency": 1}),
        (
            "post",
            "/api/evaluations",
            {"concurrency": 1, "evaluation_set": "evaluation", "tiny": True},
        ),
        ("post", "/api/claims/CLM-1001/run", {"thread_id": "unauthenticated"}),
        ("post", "/api/claims/CLM-1003/sign-off", None),
        ("post", "/api/rounds", None),
        ("post", "/api/rounds/1/decision", None),
        ("put", "/api/settings", None),
    ],
)
def test_handler_actions_require_a_valid_sign_in(client, method, path, body):
    client.headers.pop("Authorization", None)
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 401
    client.headers["Authorization"] = "Bearer invalid"
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 401


def test_agent_only_and_shared_read_credentials(client, monkeypatch):
    client.headers.pop("Authorization", None)
    client.headers.pop("X-API-Key", None)
    assert (
        client.post(
            "/api/claims/CLM-1001/activity", json={"message": "test"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/claims/CLM-1001/activity",
            json={"message": "test"},
            headers=[(b"X-API-Key", b"malformed-\xe9")],
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/claims", headers=[(b"X-API-Key", b"malformed-\xe9")]
        ).status_code
        == 401
    )

    client.headers["X-API-Key"] = "wrong"
    assert (
        client.post(
            "/api/claims/CLM-1001/activity", json={"message": "test"}
        ).status_code
        == 401
    )
    client.headers["X-API-Key"] = "undercover-e2e-agent-key-32-characters"
    assert (
        client.post(
            "/api/claims/CLM-1001/activity", json={"message": "test"}
        ).status_code
        == 200
    )
    assert client.get("/api/claims").status_code == 200

    client.headers.pop("X-API-Key")
    assert client.get("/api/claims").status_code == 401
    client.headers["Authorization"] = "Bearer dana-token"
    assert client.get("/api/claims").status_code == 200
    monkeypatch.delenv("UNDERCOVER_API_KEY")
    client.headers.pop("Authorization")
    assert client.get("/api/claims").status_code == 401
    assert (
        client.post(
            "/api/claims/CLM-1001/activity", json={"message": "test"}
        ).status_code
        == 401
    )
    client.headers["Authorization"] = "Bearer dana-token"
    assert client.get("/api/claims").status_code == 200
    assert client.get("/api/health").status_code == 200


def test_registered_api_routes_match_explicit_dependency_inventory():
    expected = {
        ("GET", "/api/health"): set(),
        ("GET", "/api/configuration"): {"current_handler"},
        ("GET", "/api/configuration/{version}"): {"current_handler"},
        ("GET", "/api/agent/configuration/current"): {"agent_key"},
        ("GET", "/api/agent/configuration/{version}"): {"agent_key"},
        ("GET", "/api/recipes"): {"current_handler"},
        ("GET", "/api/internal/recipes/current"): {"agent_key"},
        ("GET", "/api/settings"): {"current_handler"},
        ("PUT", "/api/settings"): {"current_handler"},
        ("GET", "/api/settings/changes"): {"current_handler"},
        ("GET", "/api/rounds"): {"current_handler"},
        ("GET", "/api/claims"): {"agent_or_handler"},
        ("POST", "/api/internal/claims/{claim_id}/work"): {"agent_key"},
        ("GET", "/api/claims/{claim_id}"): {"agent_or_handler"},
        ("POST", "/api/claims/{claim_id}/payout"): {"agent_key"},
        ("POST", "/api/claims/{claim_id}/decision"): {"agent_key"},
        ("GET", "/api/me"): {"current_handler"},
        ("POST", "/api/claims/{claim_id}/sign-off"): {"current_handler"},
        ("POST", "/api/claims/{claim_id}/failure"): {"agent_key"},
        ("POST", "/api/claims/{claim_id}/payment"): {"agent_key"},
        ("POST", "/api/claims/{claim_id}/run"): {"current_handler"},
        ("POST", "/api/dispatches"): {"current_handler"},
        ("GET", "/api/dispatches/current"): {"current_handler"},
        ("POST", "/api/evaluations"): {"current_handler"},
        ("GET", "/api/evaluations"): {"current_handler"},
        ("GET", "/api/evaluations/{run_id}"): {"current_handler"},
        ("POST", "/api/internal/evaluations"): {"agent_key"},
        ("POST", "/api/internal/dispatches"): {"agent_key"},
        ("POST", "/api/internal/runs/stop-active"): {"agent_key"},
        ("GET", "/api/internal/runs/{run_id}"): {"agent_key"},
        ("POST", "/api/internal/runs/{run_id}/budget"): {"agent_key"},
        (
            "POST",
            "/api/internal/runs/{run_id}/claims/{claim_id}/begin",
        ): {"agent_key"},
        (
            "POST",
            "/api/internal/runs/{run_id}/claims/{claim_id}/complete",
        ): {"agent_key"},
        (
            "POST",
            "/api/internal/runs/{run_id}/claims/{claim_id}/fail",
        ): {"agent_key"},
        (
            "POST",
            "/api/internal/runs/{run_id}/claims/{claim_id}/leave",
        ): {"agent_key"},
        ("POST", "/api/internal/runs/{run_id}/stop"): {"agent_key"},
        ("POST", "/api/internal/runs/{run_id}/finish"): {"agent_key"},
        ("POST", "/api/claims/{claim_id}/model-calls"): {"agent_key"},
        ("POST", "/api/claims/{claim_id}/work-periods"): {"agent_key"},
        ("POST", "/api/claims/{claim_id}/activity"): {"agent_key"},
        ("POST", "/api/claims/{claim_id}/document-search"): {"agent_key"},
        ("POST", "/api/claims/{claim_id}/document-section"): {"agent_key"},
        ("GET", "/api/agent/instructions"): {"current_handler"},
        ("GET", "/api/handlers"): {"current_handler"},
        ("POST", "/api/rounds"): {"senior_handler"},
        ("GET", "/api/rounds/{round_id}"): {"current_handler"},
        ("POST", "/api/rounds/{round_id}/stop"): {"senior_handler"},
        ("POST", "/api/rounds/{round_id}/decision"): {"senior_handler"},
        ("POST", "/api/internal/rounds"): {"agent_key"},
        ("POST", "/api/internal/rounds/recover"): {"agent_key"},
        ("GET", "/api/internal/rounds/{round_id}"): {"agent_key"},
        ("GET", "/api/internal/rounds/{round_id}/recipe"): {"agent_key"},
        ("GET", "/api/internal/rounds/{round_id}/results"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/check"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/stage"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/proposal"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/decision"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/apply/{kind}"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/verdict"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/finish"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/model-calls"): {"agent_key"},
        ("POST", "/api/internal/rounds/{round_id}/stop"): {"agent_key"},
    }
    routes = [
        route
        for route in [*app.routes, *improvement_router.routes]
        if isinstance(route, APIRoute) and route.path.startswith("/api/")
    ]
    actual = {
        (method, route.path): {
            getattr(dependency.call, "__name__", "")
            for dependency in route.dependant.dependencies
            if dependency.call
            in {agent_key, agent_or_handler, current_handler, senior_handler}
        }
        for route in routes
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}
    }
    assert actual == expected


def test_unknown_api_path_remains_json_not_found(client):
    client.headers.pop("Authorization", None)
    client.headers.pop("X-API-Key", None)
    response = client.get("/api/not-a-route")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_signed_in_user_must_be_a_known_handler(client):
    client.headers["Authorization"] = "Bearer stranger-token"
    response = client.get("/api/me")
    assert response.status_code == 403
    assert response.json() == {"detail": "This user is not a claims handler"}


def test_agent_key_can_create_internal_improvement_round_for_handler_id(client):
    client.headers.pop("Authorization", None)
    response = client.post("/api/internal/rounds", json={"handler_id": "HND-01"})
    assert response.status_code == 201, response.json()
    record = response.json()
    assert record["status"] == "open"
    round_id = record["round_id"]
    proposal = client.post(
        f"/api/internal/rounds/{round_id}/proposal",
        json={
            "payload": {
                "instructions": "Check coverage carefully.",
                "model": "openai/gpt-6-luna",
                "reasoning_effort": "low",
                "reason": "Improve accuracy",
            }
        },
    )
    assert proposal.status_code == 200, proposal.json()
    proposal_id = proposal.json()["proposals"][0]["proposal_id"]
    decision = client.post(
        f"/api/internal/rounds/{round_id}/decision",
        json={
            "proposal_id": proposal_id,
            "decision": "reject",
            "reason": "Retain the current configuration",
            "handler_id": "HND-01",
        },
    )
    assert decision.status_code == 200, decision.json()


def test_signed_in_handler_and_over_limit_permission(client):
    me = client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["name"] == "Dana Whitfield"

    decided = client.post(
        "/api/claims/CLM-1003/decision",
        json={
            "decision": "approve",
            "payout": 3750,
            "reason": "Emergency treatment is covered by this policy.",
            "policy_section": "Section 7; Section 10",
        },
    )
    assert decided.json()["claim"]["status"] == "waiting_for_sign_off"

    client.headers["Authorization"] = "Bearer marcus-token"
    refused = client.post("/api/claims/CLM-1003/sign-off")
    assert refused.status_code == 403
    assert refused.json()["detail"] == "Handler cannot approve over-limit payouts"
    assert client.get("/api/claims/CLM-1003").json()["signed_off_by"] is None

    client.headers["Authorization"] = "Bearer dana-token"
    signed = client.post("/api/claims/CLM-1003/sign-off")
    assert signed.status_code == 200
    assert signed.json()["signed_off_by"] == "HND-01"
