"""Backend-owned claim configuration and improver recipe versions."""

import difflib
from itertools import islice
from typing import Any

from fastapi import HTTPException
from sqlalchemy import Connection, RowMapping, select

from backend.database import (
    configuration_versions_table,
    connect,
    documents_table,
    iso,
    recipe_versions_table,
    settings_table,
)
from backend.version_scores import average_scores_in


def text_diff(old: str, new: str) -> list[dict[str, str]]:
    """Return readable added, removed and context lines for two texts."""
    lines = []
    diff = difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=3)
    for line in islice(diff, 2, None):
        if line.startswith("@@"):
            continue
        kind = "context"
        text = line
        if line.startswith("+"):
            kind, text = "added", line[1:]
        elif line.startswith("-"):
            kind, text = "removed", line[1:]
        elif line.startswith(" "):
            text = line[1:]
        lines.append({"kind": kind, "text": text})
    return lines


def current_version(connection: Connection, key: str) -> int:
    return int(
        connection.execute(
            select(settings_table.c.data).where(settings_table.c.key == key)
        ).scalar_one()
    )


def configuration_row(connection: Connection, version: int | None = None) -> RowMapping:
    selected = (
        current_version(connection, "current_configuration_version")
        if version is None
        else version
    )
    row = (
        connection.execute(
            select(configuration_versions_table).where(
                configuration_versions_table.c.version == selected
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Configuration version not found")
    return row


def agent_configuration(version: int | None = None) -> dict[str, Any]:
    """Return only the fields a claim agent needs for one version."""
    with connect() as connection:
        row = configuration_row(connection, version)
        documents = {
            document["document"]: document
            for document in connection.execute(
                select(documents_table).where(
                    documents_table.c.document.in_(("policy_wording", "claims_manual"))
                )
            )
            .mappings()
            .all()
        }
        instructions = row["instructions"].rstrip()
        if documents:
            appended = []
            for name, title in (
                ("policy_wording", "Policy wording"),
                ("claims_manual", "Claims manual"),
            ):
                document = documents.get(name)
                if document is not None:
                    appended.append(
                        f"## {title}, version {document['version']}\n\n"
                        f"{document['content'].rstrip()}"
                    )
            instructions = (
                f"{instructions}\n\n## Context documents\n\n"
                + "\n\n---\n\n".join(appended)
            )
        return {
            "version": row["version"],
            "instructions": instructions,
            "model_name": row["model_name"],
            "reasoning_effort": row["reasoning_effort"],
        }


def configuration_record(connection: Connection, row: RowMapping) -> dict[str, Any]:
    previous = (
        configuration_row(connection, row["version"] - 1)
        if row["version"] > 1
        else None
    )
    return {
        "version": row["version"],
        "instructions": row["instructions"],
        "model_name": row["model_name"],
        "reasoning_effort": row["reasoning_effort"],
        "reason": row["reason"],
        "proposed_by": row["proposed_by"],
        "approved_by": row["approved_by"],
        "proposed_at": iso(row["proposed_at"]),
        "approved_at": iso(row["approved_at"]),
        "diff": (
            text_diff(previous["instructions"], row["instructions"]) if previous else []
        ),
        "held_out_scores": average_scores_in(connection, row["version"], "held_out"),
    }


def list_configurations() -> dict[str, Any]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(configuration_versions_table).order_by(
                    configuration_versions_table.c.version
                )
            )
            .mappings()
            .all()
        )
        return {
            "current_version": current_version(
                connection, "current_configuration_version"
            ),
            "versions": [configuration_record(connection, row) for row in rows],
        }


def get_configuration(version: int) -> dict[str, Any]:
    with connect() as connection:
        return configuration_record(connection, configuration_row(connection, version))


def recipe_record(connection: Connection, row: RowMapping) -> dict[str, Any]:
    previous = (
        connection.execute(
            select(recipe_versions_table).where(
                recipe_versions_table.c.version == row["version"] - 1
            )
        )
        .mappings()
        .first()
    )
    return {
        "version": row["version"],
        "recipe": row["recipe"],
        "reason": row["reason"],
        "proposed_by": row["proposed_by"],
        "approved_by": row["approved_by"],
        "proposed_at": iso(row["proposed_at"]),
        "approved_at": iso(row["approved_at"]),
        "diff": text_diff(previous["recipe"], row["recipe"]) if previous else [],
    }


def list_recipes() -> dict[str, Any]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(recipe_versions_table).order_by(recipe_versions_table.c.version)
            )
            .mappings()
            .all()
        )
        return {
            "current_version": current_version(connection, "current_recipe_version"),
            "versions": [recipe_record(connection, row) for row in rows],
        }


def current_recipe() -> dict[str, Any]:
    with connect() as connection:
        version = current_version(connection, "current_recipe_version")
        row = (
            connection.execute(
                select(recipe_versions_table).where(
                    recipe_versions_table.c.version == version
                )
            )
            .mappings()
            .one()
        )
        return {"version": version, "recipe": row["recipe"]}
