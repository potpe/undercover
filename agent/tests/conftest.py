import pytest
from fastapi.testclient import TestClient

from backend import agent_service
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
    with TestClient(app) as test_client:
        yield test_client
