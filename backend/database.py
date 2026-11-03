import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DATABASE_PATH = ROOT / "backend" / "undercover.db"


def database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", str(DEFAULT_DATABASE_PATH)))


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(database_path())
    connection.row_factory = sqlite3.Row
    return connection


def load_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def initialize_database() -> None:
    database_path().parent.mkdir(parents=True, exist_ok=True)
    with connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS claims (
                claim_id TEXT PRIMARY KEY,
                policy_id TEXT NOT NULL,
                customer_id TEXT NOT NULL,
                claim_type TEXT NOT NULL,
                summary TEXT NOT NULL,
                amount_claimed REAL NOT NULL,
                data TEXT NOT NULL,
                status TEXT NOT NULL,
                decision TEXT,
                payout REAL,
                reason TEXT,
                policy_section TEXT,
                signed_off_by TEXT,
                paid_amount REAL
            );
            CREATE TABLE IF NOT EXISTS policies (
                policy_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS customers (
                customer_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS plans (
                plan TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS handlers (
                handler_id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        if connection.execute("SELECT COUNT(*) FROM claims").fetchone()[0]:
            return

        claims = load_json("claims.json")
        for claim in claims:
            claim["status"] = "waiting"
            connection.execute(
                """
                INSERT INTO claims (
                    claim_id, policy_id, customer_id, claim_type, summary,
                    amount_claimed, data, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    claim["claim_id"],
                    claim["policy_id"],
                    claim["customer_id"],
                    claim["claim_type"],
                    claim["summary"],
                    claim["amount_claimed"],
                    json.dumps(claim),
                    claim["status"],
                ),
            )
        for policy in load_json("policies.json"):
            connection.execute(
                "INSERT INTO policies (policy_id, data) VALUES (?, ?)",
                (policy["policy_id"], json.dumps(policy)),
            )
        for customer in load_json("customers.json"):
            connection.execute(
                "INSERT INTO customers (customer_id, data) VALUES (?, ?)",
                (customer["customer_id"], json.dumps(customer)),
            )
        plan_data = load_json("plans.json")
        for plan in plan_data["plans"]:
            connection.execute(
                "INSERT INTO plans (plan, data) VALUES (?, ?)",
                (plan["plan"], json.dumps(plan)),
            )
        for handler in load_json("handlers.json"):
            connection.execute(
                "INSERT INTO handlers (handler_id, data) VALUES (?, ?)",
                (handler["handler_id"], json.dumps(handler)),
            )
        settings = {
            "company": load_json("company.json"),
            "add_ons": plan_data["add_ons"],
        }
        for key, value in settings.items():
            connection.execute(
                "INSERT INTO settings (key, data) VALUES (?, ?)",
                (key, json.dumps(value)),
            )


def add_activity(claim_id: str, message: str) -> None:
    with connect() as connection:
        connection.execute(
            "INSERT INTO activity (claim_id, message, created_at) VALUES (?, ?, ?)",
            (claim_id, message, datetime.now(UTC).isoformat()),
        )


def list_claims() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """
            SELECT claims.*, customers.data AS customer_data
            FROM claims JOIN customers ON claims.customer_id = customers.customer_id
            ORDER BY claims.claim_id
            """
        ).fetchall()
    return [
        {
            "claim_id": row["claim_id"],
            "customer_name": json.loads(row["customer_data"])["name"],
            "claim_type": row["claim_type"],
            "summary": row["summary"],
            "amount_claimed": row["amount_claimed"],
            "status": row["status"],
            "decision": row["decision"],
            "payout": row["payout"],
        }
        for row in rows
    ]


def get_claim_bundle(claim_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM claims WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        if row is None:
            return None
        policy_row = connection.execute(
            "SELECT data FROM policies WHERE policy_id = ?", (row["policy_id"],)
        ).fetchone()
        customer_row = connection.execute(
            "SELECT data FROM customers WHERE customer_id = ?", (row["customer_id"],)
        ).fetchone()
        activity_rows = connection.execute(
            "SELECT * FROM activity WHERE claim_id = ? ORDER BY id", (claim_id,)
        ).fetchall()
        policy = json.loads(policy_row["data"])
        plan_row = connection.execute(
            "SELECT data FROM plans WHERE plan = ?", (policy["plan"],)
        ).fetchone()
        add_ons_row = connection.execute(
            "SELECT data FROM settings WHERE key = 'add_ons'"
        ).fetchone()

    claim = json.loads(row["data"])
    claim["status"] = row["status"]
    plan = json.loads(plan_row["data"])
    add_ons = [
        add_on
        for add_on in json.loads(add_ons_row["data"])
        if add_on["add_on"] in policy["add_ons"]
    ]
    decision = None
    if row["decision"]:
        decision = {
            "decision": row["decision"],
            "payout": row["payout"],
            "reason": row["reason"],
            "policy_section": row["policy_section"],
        }
    return {
        "claim": claim,
        "policy": policy,
        "customer": json.loads(customer_row["data"]),
        "plan": plan,
        "add_ons": add_ons,
        "decision": decision,
        "paid_amount": row["paid_amount"],
        "signed_off_by": row["signed_off_by"],
        "activity": [
            {
                "id": activity["id"],
                "message": activity["message"],
                "created_at": activity["created_at"],
            }
            for activity in activity_rows
        ],
    }


def list_handlers() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT data FROM handlers ORDER BY handler_id"
        ).fetchall()
    return [json.loads(row["data"]) for row in rows]
