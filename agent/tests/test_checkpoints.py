import time
from concurrent.futures import ThreadPoolExecutor

from agent.checkpoints import claim_checkpointer


def test_sqlite_checkpointer_persists_across_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "business.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with claim_checkpointer() as first:
        assert first.__class__.__name__ == "SqliteSaver"
    with claim_checkpointer() as second:
        assert second.__class__.__name__ == "SqliteSaver"


def test_checkpoint_connections_are_limited_to_four_per_process(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "checkpoints.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    active = 0
    maximum = 0

    def enter_checkpoint():
        nonlocal active, maximum
        with claim_checkpointer():
            active += 1
            maximum = max(maximum, active)
            time.sleep(0.02)
            active -= 1

    with ThreadPoolExecutor(max_workers=5) as pool:
        list(pool.map(lambda _: enter_checkpoint(), range(5)))

    assert maximum == 4


def test_postgres_checkpointer_uses_lab_connection_settings(monkeypatch):
    calls = {}

    class FakeConnection:
        def close(self):
            calls["closed"] = True

    class FakeSaver:
        def __init__(self, connection):
            calls["connection"] = connection

        def setup(self):
            calls["setup"] = True

    def fake_connect(url, **kwargs):
        calls["url"] = url
        calls["kwargs"] = kwargs
        return FakeConnection()

    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/postgres")
    monkeypatch.setattr("agent.checkpoints.psycopg.connect", fake_connect)
    monkeypatch.setattr("agent.checkpoints.PostgresSaver", FakeSaver)

    with claim_checkpointer():
        pass

    assert calls["kwargs"] == {
        "autocommit": True,
        "prepare_threshold": 0,
        "row_factory": calls["kwargs"]["row_factory"],
        "options": "-c search_path=undercover",
    }
    assert calls["setup"] is True
    assert calls["closed"] is True
