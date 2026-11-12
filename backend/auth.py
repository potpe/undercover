"""Verify Supabase handler sessions and enforce handler permissions."""

import os
from functools import lru_cache
from typing import Any

import jwt
from fastapi import HTTPException, Request
from jwt.exceptions import PyJWKClientError

from backend.database import list_handlers

SUPABASE_URL = os.getenv("VITE_SUPABASE_URL", "").rstrip("/")


@lru_cache
def signing_keys(url: str) -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json")


def decode_token(token: str, keys: Any | None = None) -> dict[str, Any]:
    """Verify a Supabase access token and return its claims."""
    if keys is None:
        if not SUPABASE_URL:
            raise HTTPException(
                status_code=503, detail="Supabase Auth is not configured"
            )
        keys = signing_keys(SUPABASE_URL)
    try:
        key = keys.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            key,
            algorithms=["RS256", "ES256"],
            audience="authenticated",
        )
    except (jwt.PyJWTError, PyJWKClientError) as error:
        raise HTTPException(status_code=401, detail="Sign in to continue") from error


def current_handler(request: Request) -> dict[str, Any]:
    """Return the handler identified by a valid bearer token."""
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    claims = decode_token(token)
    email = claims.get("email")
    handler = next(
        (
            item
            for item in list_handlers()
            if isinstance(email, str) and item["email"].casefold() == email.casefold()
        ),
        None,
    )
    if handler is None:
        raise HTTPException(status_code=403, detail="This user is not a claims handler")
    return handler


def senior_handler(request: Request) -> dict[str, Any]:
    handler = current_handler(request)
    if not handler["can_approve_over_limit"]:
        raise HTTPException(
            status_code=403,
            detail=(
                "Only a handler who can approve over-limit payouts "
                "may manage improvement rounds"
            ),
        )
    return handler
