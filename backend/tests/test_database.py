import sqlite3
from pathlib import Path
from typing import cast

import yaml
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import CheckpointMetadata, empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver
from sqlalchemy import inspect, select, text

from backend.database import (
    add_activity,
    connect,
    create_db_engine,
    database_path,
    documents_table,
    get_engine,
    initialize_database,
    load_json,
    plans_table,
    reseed_database,
    settings_table,
)


def test_reseed_restores_the_queue_and_clears_all_work(client):
    client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "decline",
            "payout": 0,
            "reason": "The wording excludes this loss. The cited section applies.",
            "section": "Section 9: General exclusions",
            "decided_by": "UnderCover Agent",
        },
    )
    add_activity("CLM-1001", "Extra work")
    client.post(
        "/api/internal/dispatches",
        json={
            "concurrency": 1,
            "configuration_version": None,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "round_id": None,
            "round_purpose": None,
        },
    )

    reseed_database()
    reseed_database()

    claims = client.get("/api/claims").json()
    bundle = client.get("/api/claims/CLM-1001").json()
    assert len(claims) == 26
    assert claims[-1]["claim_id"] == "CLM-1901"
    assert {claim["status"] for claim in claims} == {"waiting"}
    assert bundle["decision"] is None
    assert bundle["activity"] == []
    assert client.get("/api/dispatches/current").json() is None


def test_normal_initialization_preserves_existing_work(client):
    response = client.post(
        "/api/claims/CLM-1001/decision",
        json={
            "decision": "decline",
            "payout": 0,
            "reason": "The wording excludes this loss. The cited section applies.",
            "section": "Section 9: General exclusions",
            "decided_by": "UnderCover Agent",
        },
    )
    assert response.status_code == 200

    initialize_database()

    assert client.get("/api/claims/CLM-1001").json()["decision"]["decision"] == (
        "decline"
    )


def test_context_plans_add_ons_deductible_rules_and_documents_are_seeded(client):
    plan_source = yaml.safe_load(Path("context/plans.yaml").read_text())
    with connect() as connection:
        plans = {
            row["plan"]: row["data"]
            for row in connection.execute(select(plans_table)).mappings()
        }
        settings = {
            row["key"]: row["data"]
            for row in connection.execute(select(settings_table)).mappings()
        }
        documents = {
            row["document"]: row
            for row in connection.execute(select(documents_table)).mappings()
        }

    assert plans == {plan["plan"]: plan for plan in plan_source["plans"]}
    assert settings["add_ons"] == plan_source["add_ons"]
    assert settings["deductible_applies_to"] == plan_source["deductible_applies_to"]
    assert settings["company"] == load_json("company.json")
    assert set(documents) == {"policy_wording", "claims_manual"}
    for name, filename, version, system, owner in (
        (
            "policy_wording",
            "policy_wording.md",
            "2026.1",
            "Published policy documents",
            "Legal",
        ),
        (
            "claims_manual",
            "claims_manual.md",
            "2026.2",
            "Claims operations",
            "Head of claims operations",
        ),
    ):
        source_text = (Path("context") / filename).read_text()
        source_body = source_text.split("---", 2)[2].lstrip("\n")
        document = documents[name]
        assert document["version"] == version
        assert document["source"]["system"] == system
        assert document["source"]["owner"] == owner
        assert document["content"] == source_body


def test_normal_initialization_preserves_retired_tables_without_migrating(client):
    with connect() as connection:
        connection.execute(
            text(
                "CREATE TABLE document_reads (id INTEGER PRIMARY KEY, "
                "claim_id VARCHAR NOT NULL)"
            )
        )

    initialize_database()

    assert inspect(get_engine()).has_table("document_reads")


def test_reseed_drops_the_two_retired_document_tables(client):
    with connect() as connection:
        for table_name in ("document_reads", "document_sections"):
            connection.execute(
                text(
                    f"CREATE TABLE {table_name} "
                    "(id INTEGER PRIMARY KEY, claim_id VARCHAR NOT NULL)"
                )
            )

    reseed_database()

    inspector = inspect(get_engine())
    assert not inspector.has_table("document_reads")
    assert not inspector.has_table("document_sections")


def test_reseed_clears_langgraph_sqlite_checkpoints_and_restores_queue(
    client, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    path = database_path()
    connection = sqlite3.connect(path, check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    thread_config = cast(
        RunnableConfig,
        {"configurable": {"thread_id": "reseed-checkpoint", "checkpoint_ns": ""}},
    )
    saved_config = checkpointer.put(
        thread_config,
        empty_checkpoint(),
        cast(CheckpointMetadata, {"source": "input", "step": 0, "parents": {}}),
        {},
    )
    assert checkpointer.get_tuple(saved_config) is not None
    connection.close()

    reseed_database()

    connection = sqlite3.connect(path, check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    assert checkpointer.get_tuple(saved_config) is None
    connection.close()
    claims = client.get("/api/claims").json()
    assert len(claims) == 26
    assert {claim["status"] for claim in claims} == {"waiting"}


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
    assert kwargs["pool_size"] == 1
    assert kwargs["max_overflow"] == 1


def test_sqlite_engine_needs_no_postgres_connection(tmp_path):
    path = tmp_path / "offline.db"

    engine = create_db_engine(f"sqlite:///{path}")

    assert engine.dialect.name == "sqlite"
    engine.dispose()
