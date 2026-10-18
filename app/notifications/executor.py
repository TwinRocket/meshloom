"""Bounded thread pool for blocking notification sends.

Apprise sends block (``requests``, ``smtplib``). They run here, four threads at
most, instead of the event loop's default pool, so a slow notification service
never starves the other ``asyncio.to_thread`` users (radio, database backups).
The notification engine shares it later.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

T = TypeVar("T")

MAX_WORKERS = 4

_executor: ThreadPoolExecutor | None = None


def notification_executor() -> ThreadPoolExecutor:
    """The shared pool, created on first use."""
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="notify")
    return _executor


async def run_blocking(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn`` in the notification pool and await its result."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        notification_executor(), functools.partial(fn, *args, **kwargs)
    )
