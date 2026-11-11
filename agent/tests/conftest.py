import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import agent_service, auth
from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "undercover-agent-test.db"))
    for key in (
        "DATABASE_URL",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_BASE_URL",
        "OPENROUTER_API_KEY",
        "MODEL_NAME",
    ):
        monkeypatch.delenv(key, raising=False)

    class Accepted:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(agent_service, "agent_post", lambda *args, **kwargs: Accepted())

    def fake_decode(token, keys=None):
        emails = {
            "dana-token": "dana.whitfield@undercover.example",
            "marcus-token": "marcus.oyelaran@undercover.example",
            "priya-token": "priya.raman@undercover.example",
            "stranger-token": "stranger@undercover.example",
        }
        if token not in emails:
            raise HTTPException(status_code=401, detail="Sign in to continue")
        return {"email": emails[token]}

    monkeypatch.setattr(auth, "decode_token", fake_decode)
    monkeypatch.setenv("VITE_SUPABASE_URL", "http://supabase.test")
    with TestClient(app, headers={"Authorization": "Bearer dana-token"}) as test_client:
        yield test_client
