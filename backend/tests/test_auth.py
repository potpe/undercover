from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException

from backend.auth import decode_token


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
    ],
)
def test_handler_actions_require_a_valid_sign_in(client, method, path, body):
    client.headers.pop("Authorization", None)
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 401

    client.headers["Authorization"] = "Bearer invalid"
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 401


def test_signed_in_user_must_be_a_known_handler(client):
    client.headers["Authorization"] = "Bearer stranger-token"
    response = client.get("/api/me")
    assert response.status_code == 403
    assert response.json() == {"detail": "This user is not a claims handler"}


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
