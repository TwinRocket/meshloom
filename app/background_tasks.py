"""Tracked fire-and-forget background tasks.

``asyncio.create_task`` only keeps a weak reference to the task it returns, so
an unreferenced task can be garbage-collected mid-flight, and an exception it
raises is only reported (as "Task exception was never retrieved") when the
task object happens to be collected. ``spawn()`` fixes both:

- the task is held in a module-level set until it finishes;
- a done-callback logs any exception with its traceback;
- ``drain_background_tasks()`` cancels and awaits whatever is still running,
  and is called from the app lifespan teardown.

Long-lived loops that already keep their own task handle (periodic sync,
connection monitor, ...) do not need this helper.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_tasks: set[asyncio.Task[Any]] = set()


def _on_done(task: asyncio.Task[Any]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background task %s failed",
            task.get_name(),
            exc_info=(type(exc), exc, exc.__traceback__),
        )


def spawn(coro: Coroutine[Any, Any, T], *, name: str | None = None) -> asyncio.Task[T]:
    """Schedule ``coro`` as a tracked background task and return it."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _tasks.add(task)
    task.add_done_callback(_on_done)
    return task


def pending_background_tasks() -> int:
    """Number of tracked tasks still running (for tests and diagnostics)."""
    return sum(1 for task in _tasks if not task.done())


async def drain_background_tasks(timeout: float = 5.0) -> None:
    """Cancel every tracked task and wait (bounded) for them to finish."""
    current = asyncio.current_task()
    tasks = [task for task in _tasks if not task.done() and task is not current]
    if not tasks:
        return
    logger.info("Cancelling %d background task(s)", len(tasks))
    for task in tasks:
        task.cancel()
    _done, still_pending = await asyncio.wait(tasks, timeout=timeout)
    if still_pending:
        logger.warning(
            "%d background task(s) did not stop within %.1fs: %s",
            len(still_pending),
            timeout,
            ", ".join(sorted(task.get_name() for task in still_pending)),
        )
