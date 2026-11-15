"""Run short asynchronous MCP operations without changing the caller's event loop."""

import asyncio
from collections.abc import Coroutine
from threading import Thread
from typing import Any


def run_mcp[T](coroutine: Coroutine[Any, Any, T]) -> T:
    """Run an MCP coroutine on a temporary worker-thread event loop."""
    result: list[T] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            result.append(asyncio.run(coroutine))
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=worker)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]
