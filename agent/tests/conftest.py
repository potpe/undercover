import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "undercover-agent-test.db"))

    class Accepted:
        def raise_for_status(self):
            return None

    monkeypatch.setattr(main, "agent_post", lambda *args, **kwargs: Accepted())
    with TestClient(app) as test_client:
        yield test_client
