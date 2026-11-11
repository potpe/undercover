"""Database-backed platform settings and their handler audit trail."""

from typing import Any

from sqlalchemy import Connection, insert, select, update

from backend.database import (
    connect,
    iso,
    setting_changes_table,
    settings_table,
    timestamp,
)


def setting(connection: Connection, key: str) -> Any:
    return connection.execute(
        select(settings_table.c.data).where(settings_table.c.key == key)
    ).scalar_one()


def settings_record(connection: Connection) -> dict[str, Any]:
    company = setting(connection, "company")
    improvement = setting(connection, "improvement")
    return {
        "approval_limit": float(company["payout_approval_limit"]),
        **improvement,
        "models_allowed": setting(connection, "models_allowed"),
    }


def get_settings() -> dict[str, Any]:
    with connect() as connection:
        return settings_record(connection)


def flattened(value: dict[str, Any], prefix: str = "") -> dict[str, Any]:
    result = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(flattened(item, path))
        else:
            result[path] = item
    return result


def update_settings(values: dict[str, Any], handler_id: str) -> dict[str, Any]:
    with connect() as connection:
        before = settings_record(connection)
        after = {key: values[key] for key in values}
        old_flat = flattened({key: before[key] for key in after})
        new_flat = flattened(after)
        changed_at = timestamp()
        for key, new_value in new_flat.items():
            if old_flat[key] != new_value:
                connection.execute(
                    insert(setting_changes_table).values(
                        key=key,
                        old_value=old_flat[key],
                        new_value=new_value,
                        changed_by=handler_id,
                        changed_at=changed_at,
                    )
                )
        company = setting(connection, "company")
        company["payout_approval_limit"] = values["approval_limit"]
        connection.execute(
            update(settings_table)
            .where(settings_table.c.key == "company")
            .values(data=company)
        )
        improvement = {key: values[key] for key in setting(connection, "improvement")}
        connection.execute(
            update(settings_table)
            .where(settings_table.c.key == "improvement")
            .values(data=improvement)
        )
        return settings_record(connection)


def list_setting_changes() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(setting_changes_table).order_by(
                    setting_changes_table.c.id.desc()
                )
            )
            .mappings()
            .all()
        )
        return [
            {
                "key": row["key"],
                "old_value": row["old_value"],
                "new_value": row["new_value"],
                "changed_by": row["changed_by"],
                "changed_at": iso(row["changed_at"]),
            }
            for row in rows
        ]
