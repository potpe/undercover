"""Synchronous durable checkpoints for claim graphs."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, cast

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from psycopg import Connection
from psycopg.rows import DictRow, dict_row

from backend.database import database_path


@contextmanager
def claim_checkpointer() -> Iterator[BaseCheckpointSaver]:
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        connection = cast(
            Connection[DictRow],
            psycopg.connect(
                database_url,
                autocommit=True,
                prepare_threshold=0,
                row_factory=cast(Any, dict_row),
                options="-c search_path=undercover",
            ),
        )
        checkpointer = PostgresSaver(connection)
        checkpointer.setup()
        try:
            yield checkpointer
        finally:
            connection.close()
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
