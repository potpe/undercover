import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import agent_service, auth, judge
from backend.main import app

TEST_AGENT_KEY = "undercover-e2e-agent-key-32-characters"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "undercover-test.db"))
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
    monkeypatch.setattr(
        judge,
        "grade_claim",
        lambda state: {
            "grade": 1.51,
            "letter": "C",
            "same_grounds": 0.75,
            "same_section": 0.25,
            "judge_cost": 0.001,
        },
    )
    monkeypatch.setenv("VITE_SUPABASE_URL", "http://supabase.test")
    monkeypatch.setenv("UNDERCOVER_API_KEY", TEST_AGENT_KEY)
    with TestClient(
        app,
        headers={
            "Authorization": "Bearer dana-token",
            "X-API-Key": TEST_AGENT_KEY,
        },
    ) as test_client:
        yield test_client
