import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Connection,
    DateTime,
    Engine,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    insert,
    inspect,
    select,
)
from sqlalchemy.schema import CreateSchema, DropSchema

from backend.documents import parse_sections, rank_sections

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DEFAULT_DATABASE_PATH = ROOT / "backend" / "undercover.db"
SCHEMA = "undercover"

metadata = MetaData()
claims_table = Table(
    "claims",
    metadata,
    Column("claim_id", String, primary_key=True),
    Column("policy_id", String, nullable=False),
    Column("customer_id", String, nullable=False),
    Column("claim_type", String, nullable=False),
    Column("summary", String, nullable=False),
    Column("amount_claimed", Numeric, nullable=False),
    Column("data", JSON, nullable=False),
    Column("status", String, nullable=False),
    Column("decision", String),
    Column("payout", Numeric),
    Column("reason", Text),
    Column("policy_section", Text),
    Column("signed_off_by", String),
    Column("paid_amount", Numeric),
    Column("agent_thread_id", String),
    Column("signed_off_at", DateTime(timezone=True)),
    Column("paid_at", DateTime(timezone=True)),
)
policies_table = Table(
    "policies",
    metadata,
    Column("policy_id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
customers_table = Table(
    "customers",
    metadata,
    Column("customer_id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
plans_table = Table(
    "plans",
    metadata,
    Column("plan", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
handlers_table = Table(
    "handlers",
    metadata,
    Column("handler_id", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
settings_table = Table(
    "settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
activity_table = Table(
    "activity",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("message", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
document_sections_table = Table(
    "document_sections",
    metadata,
    Column("document", String, primary_key=True),
    Column("section_id", String, primary_key=True),
    Column("title", String, nullable=False),
    Column("content", Text, nullable=False),
)
document_reads_table = Table(
    "document_reads",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("claim_id", String, nullable=False),
    Column("document", String, nullable=False),
    Column("section_id", String, nullable=False),
    Column("title", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("claim_id", "document", "section_id"),
)
runs_table = Table(
    "runs",
    metadata,
    Column("run_id", Integer, primary_key=True, autoincrement=True),
    Column("thread_id", String, nullable=False),
    Column("claim_cap", Integer, nullable=False),
    Column("target_claim_id", String),
    Column("model_name", String),
    Column("status", String, nullable=False),
    Column("summary", Text),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True)),
)
run_todos_table = Table(
    "run_todos",
    metadata,
    Column("run_id", Integer, primary_key=True),
    Column("claim_id", String, primary_key=True),
    Column("position", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column("attempts", Integer, nullable=False, default=0),
    Column("result", Text),
    Column("error", Text),
    UniqueConstraint("run_id", "position"),
)


def database_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", str(DEFAULT_DATABASE_PATH)))


def database_url() -> str:
    hosted = os.getenv("DATABASE_URL")
    if hosted:
        if hosted.startswith("postgres://"):
            return hosted.replace("postgres://", "postgresql+psycopg://", 1)
        if hosted.startswith("postgresql://"):
            return hosted.replace("postgresql://", "postgresql+psycopg://", 1)
        return hosted
    return f"sqlite:///{database_path().resolve().as_posix()}"


def create_db_engine(url: str) -> Engine:
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql"):
        return create_engine(
            url,
            connect_args={"options": f"-c search_path={SCHEMA}"},
            pool_size=2,
            max_overflow=2,
        )
    return create_engine(url)


@lru_cache
def cached_engine(url: str) -> Engine:
    return create_db_engine(url)


def get_engine() -> Engine:
    return cached_engine(database_url())


@contextmanager
def connect() -> Iterator[Connection]:
    with get_engine().begin() as connection:
        yield connection


def load_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def timestamp() -> datetime:
    return datetime.now(UTC)


def iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def seed_documents(connection: Connection) -> None:
    for document in ("policy_wording", "claims_manual"):
        for section in parse_sections(document):
            exists = connection.execute(
                select(document_sections_table.c.section_id).where(
                    document_sections_table.c.document == document,
                    document_sections_table.c.section_id == section["section_id"],
                )
            ).first()
            if exists is None:
                connection.execute(insert(document_sections_table).values(**section))


def seed_business_data(connection: Connection) -> None:
    if connection.execute(select(func.count()).select_from(claims_table)).scalar_one():
        return
    for source in load_json("claims.json"):
        claim = {**source, "status": "waiting"}
        connection.execute(
            insert(claims_table).values(
                claim_id=claim["claim_id"],
                policy_id=claim["policy_id"],
                customer_id=claim["customer_id"],
                claim_type=claim["claim_type"],
                summary=claim["summary"],
                amount_claimed=claim["amount_claimed"],
                data=claim,
                status="waiting",
            )
        )
    for policy in load_json("policies.json"):
        connection.execute(
            insert(policies_table).values(policy_id=policy["policy_id"], data=policy)
        )
    for customer in load_json("customers.json"):
        connection.execute(
            insert(customers_table).values(
                customer_id=customer["customer_id"], data=customer
            )
        )
    plan_data = load_json("plans.json")
    for plan in plan_data["plans"]:
        connection.execute(insert(plans_table).values(plan=plan["plan"], data=plan))
    for handler in load_json("handlers.json"):
        connection.execute(
            insert(handlers_table).values(
                handler_id=handler["handler_id"], data=handler
            )
        )
    settings = {
        "company": load_json("company.json"),
        "add_ons": plan_data["add_ons"],
        "deductible_applies_to": plan_data["deductible_applies_to"],
    }
    for key, value in settings.items():
        connection.execute(insert(settings_table).values(key=key, data=value))


def initialize_database() -> None:
    if not os.getenv("DATABASE_URL"):
        database_path().parent.mkdir(parents=True, exist_ok=True)
    engine = get_engine()
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(CreateSchema(SCHEMA, if_not_exists=True))
        metadata.create_all(connection)
        seed_documents(connection)
        seed_business_data(connection)


def reseed_database() -> None:
    engine = get_engine()
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(DropSchema(SCHEMA, cascade=True, if_exists=True))
            connection.execute(CreateSchema(SCHEMA))
        else:
            for table_name in inspect(connection).get_table_names():
                if not table_name.startswith("sqlite_"):
                    Table(table_name, MetaData(), autoload_with=connection).drop(
                        connection
                    )
    initialize_database()


def insert_activity(connection: Connection, claim_id: str, message: str) -> None:
    connection.execute(
        insert(activity_table).values(
            claim_id=claim_id, message=message, created_at=timestamp()
        )
    )


def add_activity(claim_id: str, message: str) -> None:
    with connect() as connection:
        insert_activity(connection, claim_id, message)


def list_claims() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(claims_table, customers_table.c.data.label("customer_data"))
                .join(
                    customers_table,
                    claims_table.c.customer_id == customers_table.c.customer_id,
                )
                .order_by(claims_table.c.claim_id)
            )
            .mappings()
            .all()
        )
    return [
        {
            "claim_id": row["claim_id"],
            "customer_name": row["customer_data"]["name"],
            "claim_type": row["claim_type"],
            "summary": row["summary"],
            "amount_claimed": float(row["amount_claimed"]),
            "status": row["status"],
            "decision": row["decision"],
            "payout": float(row["payout"]) if row["payout"] is not None else None,
        }
        for row in rows
    ]


def get_claim_bundle(claim_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = (
            connection.execute(
                select(claims_table).where(claims_table.c.claim_id == claim_id)
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        policy = connection.execute(
            select(policies_table.c.data).where(
                policies_table.c.policy_id == row["policy_id"]
            )
        ).scalar_one()
        customer = connection.execute(
            select(customers_table.c.data).where(
                customers_table.c.customer_id == row["customer_id"]
            )
        ).scalar_one()
        activity_rows = (
            connection.execute(
                select(activity_table)
                .where(activity_table.c.claim_id == claim_id)
                .order_by(activity_table.c.id)
            )
            .mappings()
            .all()
        )
        document_rows = (
            connection.execute(
                select(
                    document_reads_table.c.document,
                    document_reads_table.c.section_id,
                    document_reads_table.c.title,
                )
                .where(document_reads_table.c.claim_id == claim_id)
                .order_by(document_reads_table.c.id)
            )
            .mappings()
            .all()
        )
        plan = connection.execute(
            select(plans_table.c.data).where(plans_table.c.plan == policy["plan"])
        ).scalar_one()
        add_on_data = connection.execute(
            select(settings_table.c.data).where(settings_table.c.key == "add_ons")
        ).scalar_one()

    claim = {**row["data"], "status": row["status"]}
    add_ons = [item for item in add_on_data if item["add_on"] in policy["add_ons"]]
    decision = None
    if row["decision"]:
        decision = {
            "decision": row["decision"],
            "payout": float(row["payout"]),
            "reason": row["reason"],
            "policy_section": row["policy_section"],
        }
    return {
        "claim": claim,
        "policy": policy,
        "customer": customer,
        "plan": plan,
        "add_ons": add_ons,
        "decision": decision,
        "paid_amount": (
            float(row["paid_amount"]) if row["paid_amount"] is not None else None
        ),
        "signed_off_by": row["signed_off_by"],
        "signed_off_at": iso(row["signed_off_at"]),
        "paid_at": iso(row["paid_at"]),
        "agent_thread_id": row["agent_thread_id"],
        "activity": [
            {
                "id": activity["id"],
                "message": activity["message"],
                "created_at": iso(activity["created_at"]),
            }
            for activity in activity_rows
        ],
        "documents_read": [dict(document) for document in document_rows],
    }


def list_handlers() -> list[dict[str, Any]]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(handlers_table.c.data).order_by(handlers_table.c.handler_id)
            )
            .scalars()
            .all()
        )
    return list(rows)


def search_document(document: str, query: str) -> list[dict[str, Any]]:
    with connect() as connection:
        rows = (
            connection.execute(
                select(document_sections_table).where(
                    document_sections_table.c.document == document
                )
            )
            .mappings()
            .all()
        )
    return rank_sections([dict(row) for row in rows], query)


def read_document_section(
    claim_id: str, document: str, section_id: str
) -> dict[str, Any] | None:
    with connect() as connection:
        row = (
            connection.execute(
                select(document_sections_table).where(
                    document_sections_table.c.document == document,
                    document_sections_table.c.section_id == section_id,
                )
            )
            .mappings()
            .first()
        )
        if row is None:
            return None
        existing = connection.execute(
            select(document_reads_table.c.id).where(
                document_reads_table.c.claim_id == claim_id,
                document_reads_table.c.document == document,
                document_reads_table.c.section_id == section_id,
            )
        ).first()
        if existing is None:
            connection.execute(
                insert(document_reads_table).values(
                    claim_id=claim_id,
                    document=document,
                    section_id=section_id,
                    title=row["title"],
                    created_at=timestamp(),
                )
            )
            label = (
                "Policy wording" if document == "policy_wording" else "Claims manual"
            )
            insert_activity(
                connection,
                claim_id,
                f"Read {label} section {section_id}: {row['title']}",
            )
        return {
            "document": row["document"],
            "section_id": row["section_id"],
            "title": row["title"],
            "content": row["content"],
        }
