from backend import main
from backend.database import (
    add_activity,
    create_db_engine,
    initialize_database,
    reseed_database,
)


def test_reseed_restores_the_queue_and_clears_all_work(client, monkeypatch):
    monkeypatch.setattr(main, "execute_queue_run", lambda run_id, thread_id: None)
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "decline",
            "payout": 0,
            "reason": "The wording excludes this loss. The cited section applies.",
            "policy_section": "Section 9: General exclusions",
        },
    )
    add_activity("CLM-1001", "Extra work")
    client.post("/api/runs", json={"thread_id": "old", "claim_cap": 1})

    reseed_database()
    reseed_database()

    claims = client.get("/api/claims").json()
    bundle = client.get("/api/claims/CLM-1001").json()
    assert len(claims) == 25
    assert {claim["status"] for claim in claims} == {"waiting"}
    assert bundle["decision"] is None
    assert bundle["activity"] == []
    assert bundle["documents_read"] == []
    assert client.get("/api/runs/current").json() is None


def test_normal_initialization_preserves_existing_work(client):
    response = client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "decline",
            "payout": 0,
            "reason": "The wording excludes this loss. The cited section applies.",
            "policy_section": "Section 9: General exclusions",
        },
    )
    assert response.status_code == 200

    initialize_database()

    assert client.get("/api/claims/CLM-1001").json()["decision"]["decision"] == (
        "decline"
    )


def test_postgres_engine_uses_schema_and_bounded_pool(monkeypatch):
    calls = []

    def fake_engine(url, **kwargs):
        calls.append((url, kwargs))
        return object()

    monkeypatch.setattr("backend.database.create_engine", fake_engine)

    create_db_engine("postgresql://postgres.example:secret@localhost/postgres")

    url, kwargs = calls[0]
    assert url.startswith("postgresql+psycopg://")
    assert kwargs["connect_args"] == {"options": "-c search_path=undercover"}
    assert kwargs["pool_size"] == 2
    assert kwargs["max_overflow"] == 2


def test_sqlite_engine_needs_no_postgres_connection(tmp_path):
    path = tmp_path / "offline.db"

    engine = create_db_engine(f"sqlite:///{path}")

    assert engine.dialect.name == "sqlite"
    engine.dispose()
