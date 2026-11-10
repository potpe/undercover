"""Synchronous durable checkpoints for independent claim graphs."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, cast

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg import Connection
from psycopg.rows import DictRow, dict_row
from psycopg_pool import ConnectionPool

from backend.database import database_path


@lru_cache
def postgres_pool(database_url: str) -> ConnectionPool[Connection[DictRow]]:
    pool = cast(
        Any,
        ConnectionPool(
            database_url,
            min_size=0,
            max_size=4,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": cast(Any, dict_row),
                "options": "-c search_path=undercover",
            },
        ),
    )
    PostgresSaver(pool).setup()
    return pool


@contextmanager
def claim_checkpointer() -> Iterator[BaseCheckpointSaver]:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        yield PostgresSaver(postgres_pool(database_url))
        return

    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    checkpointer.setup()
    try:
        yield checkpointer
    finally:
        connection.close()


def close_checkpoint_pool() -> None:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        postgres_pool(database_url).close()
        postgres_pool.cache_clear()


def clear_claim_thread(thread_id: str) -> None:
    with claim_checkpointer() as checkpointer:
        checkpointer.delete_thread(thread_id)
