"""Langfuse tracing for one durable claim thread."""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import cast

from langchain_core.callbacks import BaseCallbackHandler
from langfuse import get_client, propagate_attributes
from langfuse.langchain import CallbackHandler

LANGFUSE_KEYS = ("LANGFUSE_SECRET_KEY", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_BASE_URL")


@contextmanager
def trace_claim(
    claim_id: str, thread_id: str, tags: list[str] | None = None
) -> Iterator[list[BaseCallbackHandler]]:
    if not all(os.getenv(key) for key in LANGFUSE_KEYS):
        yield []
        return

    langfuse = get_client()
    handler = CallbackHandler()
    session_id = f"{claim_id}:{thread_id}"
    try:
        with (
            langfuse.start_as_current_observation(
                as_type="span", name=f"Claim {claim_id}"
            ),
            propagate_attributes(session_id=session_id, tags=tags or []),
        ):
            yield [cast(BaseCallbackHandler, handler)]
    finally:
        langfuse.flush()


@contextmanager
def trace_round(round_id: int) -> Iterator[list[BaseCallbackHandler]]:
    if not all(os.getenv(key) for key in LANGFUSE_KEYS):
        yield []
        return
    langfuse = get_client()
    try:
        with propagate_attributes(
            session_id=f"Improvement {round_id}", tags=[f"round-{round_id}"]
        ):
            yield [cast(BaseCallbackHandler, CallbackHandler())]
    finally:
        langfuse.flush()
