from agent import checkpoints


def test_sqlite_checkpointer_persists_across_connections(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "business.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with checkpoints.claim_checkpointer() as first:
        assert first.__class__.__name__ == "SqliteSaver"
    with checkpoints.claim_checkpointer() as second:
        assert second.__class__.__name__ == "SqliteSaver"


def test_postgres_uses_one_bounded_pool_and_a_new_saver_per_claim(monkeypatch):
    calls = []
    savers = []

    class FakePool:
        def __init__(self, url, **kwargs):
            calls.append((url, kwargs))
            self.closed = False

        def close(self):
            self.closed = True

    class FakeSaver:
        def __init__(self, pool):
            self.pool = pool
            self.lock = object()
            savers.append(self)

        def setup(self):
            calls.append(("setup", {}))

    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres@localhost/postgres")
    monkeypatch.setattr(checkpoints, "ConnectionPool", FakePool)
    monkeypatch.setattr(checkpoints, "PostgresSaver", FakeSaver)
    checkpoints.postgres_pool.cache_clear()

    with checkpoints.claim_checkpointer() as first:
        pass
    with checkpoints.claim_checkpointer() as second:
        pass
    with checkpoints.claim_checkpointer() as third:
        pass

    assert len(calls) == 2
    url, options = calls[0]
    assert url == "postgresql://postgres@localhost/postgres"
    assert options["min_size"] == 0
    assert options["max_size"] == 4
    assert options["kwargs"]["autocommit"] is True
    assert options["kwargs"]["prepare_threshold"] == 0
    assert options["kwargs"]["options"] == "-c search_path=undercover"
    assert first is not second
    first_pool = first.__dict__["pool"]
    assert first_pool is second.__dict__["pool"] is third.__dict__["pool"]
    assert len({id(saver.lock) for saver in savers}) == 4

    checkpoints.close_checkpoint_pool()
    assert first_pool.closed is True
