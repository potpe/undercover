from pathlib import Path

from sqlalchemy import insert, update

from backend.configurations import text_diff
from backend.database import (
    configuration_versions_table,
    connect,
    recipe_versions_table,
    reseed_database,
    settings_table,
    timestamp,
)


def test_seeding_creates_configuration_and_recipe_version_one(client):
    configuration = client.get("/api/configuration")
    assert configuration.status_code == 200
    body = configuration.json()
    assert body["current_version"] == 1
    assert len(body["versions"]) == 1
    version = body["versions"][0]
    assert version["instructions"] == Path("backend/agent_instructions.md").read_text()
    assert version["model_name"] == "openai/gpt-6-luna"
    assert version["reasoning_effort"] == "low"
    assert version["diff"] == []
    assert version["held_out_scores"] is None

    recipes = client.get("/api/recipes")
    assert recipes.status_code == 200
    recipe = recipes.json()["versions"][0]
    assert recipe["version"] == 1
    assert "read_results" in recipe["recipe"]
    assert "propose_recipe" in recipe["recipe"]
    assert recipe["diff"] == []


def test_claim_configuration_contains_no_scores_or_handler_decisions(client):
    response = client.get("/api/agent/configuration/current")
    assert response.status_code == 200
    assert set(response.json()) == {
        "version",
        "instructions",
        "model_name",
        "reasoning_effort",
    }


def test_diff_uses_difflib_additions_and_removals():
    assert text_diff("one\ntwo\n", "one\nthree\n") == [
        {"kind": "context", "text": "one"},
        {"kind": "removed", "text": "two"},
        {"kind": "added", "text": "three"},
    ]


def test_configuration_and_recipe_history_return_exact_diffs(client):
    configuration_lines = [
        {"kind": "context", "text": "Rule one"},
        {"kind": "removed", "text": "Old rule"},
        {"kind": "added", "text": "New rule"},
        {"kind": "context", "text": "Rule three"},
    ]
    recipe_lines = [
        {"kind": "context", "text": "Read the results"},
        {"kind": "removed", "text": "Use old test"},
        {"kind": "added", "text": "Use held-out test"},
        {"kind": "context", "text": "Improve the recipe"},
    ]
    with connect() as connection:
        connection.execute(
            update(configuration_versions_table)
            .where(configuration_versions_table.c.version == 1)
            .values(instructions="Rule one\nOld rule\nRule three\n")
        )
        connection.execute(
            insert(configuration_versions_table).values(
                version=2,
                instructions="Rule one\nNew rule\nRule three\n",
                model_name="openai/gpt-6-luna",
                reasoning_effort="low",
                reason="Clarify a general rule",
                proposed_by="improver",
                approved_by="HND-01",
                proposed_at=timestamp(),
                approved_at=timestamp(),
            )
        )
        connection.execute(
            update(settings_table)
            .where(settings_table.c.key == "current_configuration_version")
            .values(data=2)
        )
        connection.execute(
            update(recipe_versions_table)
            .where(recipe_versions_table.c.version == 1)
            .values(recipe="Read the results\nUse old test\nImprove the recipe\n")
        )
        connection.execute(
            insert(recipe_versions_table).values(
                version=2,
                recipe="Read the results\nUse held-out test\nImprove the recipe\n",
                reason="Use the right test set",
                proposed_by="improver",
                approved_by="HND-01",
                proposed_at=timestamp(),
                approved_at=timestamp(),
            )
        )

    configurations = client.get("/api/configuration").json()["versions"]
    recipes = client.get("/api/recipes").json()["versions"]
    assert configurations[0]["diff"] == []
    assert configurations[1]["diff"] == configuration_lines
    assert recipes[0]["diff"] == []
    assert recipes[1]["diff"] == recipe_lines


def test_free_override_is_the_only_allowed_configuration(client, monkeypatch):
    monkeypatch.setenv("MODEL_NAME", "openrouter/free")
    reseed_database()

    version = client.get("/api/agent/configuration/current").json()
    settings = client.get("/api/settings").json()
    assert version["model_name"] == "openrouter/free"
    assert version["reasoning_effort"] == "default"
    assert settings["models_allowed"] == [
        {
            "model_name": "openrouter/free",
            "reasoning_efforts": ["default"],
            "input_price": 0,
            "output_price": 0,
        }
    ]


def test_dispatch_records_the_current_version(client):
    created = client.post(
        "/api/internal/dispatches",
        json={
            "concurrency": 3,
            "configuration_version": None,
            "model_name": "openai/gpt-6-luna",
            "reasoning_effort": "low",
            "round_id": None,
            "round_purpose": None,
        },
    )
    assert created.status_code == 201
    assert created.json()["configuration_version"] == 1
    assert created.json()["round_id"] is None


def test_reseed_restores_versions_and_empties_rounds(client):
    reseed_database()
    assert client.get("/api/rounds").json() == []
    assert len(client.get("/api/configuration").json()["versions"]) == 1
    assert len(client.get("/api/recipes").json()["versions"]) == 1


def test_existing_run_keeps_its_version_when_current_changes(client):
    payload = {
        "concurrency": 3,
        "configuration_version": None,
        "model_name": "openai/gpt-6-luna",
        "reasoning_effort": "low",
        "round_id": None,
        "round_purpose": None,
    }
    first = client.post("/api/internal/dispatches", json=payload).json()
    assert first["configuration_version"] == 1
    assert first["model_name"] == "openai/gpt-6-luna"
    assert first["reasoning_effort"] == "low"
    client.post("/api/internal/runs/stop-active")
    with connect() as connection:
        connection.execute(
            insert(configuration_versions_table).values(
                version=2,
                instructions="New instructions",
                model_name="openai/gpt-6-luna",
                reasoning_effort="medium",
                reason="Test improvement",
                proposed_by="improver",
                approved_by="HND-01",
                proposed_at=timestamp(),
                approved_at=timestamp(),
            )
        )
        connection.execute(
            update(settings_table)
            .where(settings_table.c.key == "current_configuration_version")
            .values(data=2)
        )
    second = client.post(
        "/api/internal/evaluations",
        json={
            **payload,
            "reasoning_effort": "medium",
            "evaluation_set": "evaluation",
            "tiny": True,
        },
    )
    assert second.status_code == 201
    assert second.json()["configuration_version"] == 2
    assert second.json()["model_name"] == "openai/gpt-6-luna"
    assert second.json()["reasoning_effort"] == "medium"
    first_record = client.get(f"/api/internal/runs/{first['run_id']}").json()
    assert first_record["configuration_version"] == 1
    assert first_record["model_name"] == "openai/gpt-6-luna"
    assert first_record["reasoning_effort"] == "low"
    history = client.get("/api/configuration").json()
    assert history["current_version"] == 2
    assert history["versions"][1]["diff"][-1] == {
        "kind": "added",
        "text": "New instructions",
    }
