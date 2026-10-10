"""Tests for the tracked background-task helper and region backfill single-flight."""

import asyncio
import gc
import logging
from unittest.mock import patch

import pytest

from app.background_tasks import drain_background_tasks, pending_background_tasks, spawn


@pytest.mark.asyncio
async def test_spawn_keeps_strong_reference_until_done():
    gate = asyncio.Event()
    done = []

    async def work():
        await gate.wait()
        done.append(True)

    spawn(work())
    gc.collect()
    assert pending_background_tasks() >= 1
    gate.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert done == [True]


@pytest.mark.asyncio
async def test_spawn_logs_exceptions(caplog):
    async def boom():
        raise RuntimeError("kaput")

    with caplog.at_level(logging.ERROR, logger="app.background_tasks"):
        task = spawn(boom(), name="boom-task")
        with pytest.raises(RuntimeError):
            await task
        await asyncio.sleep(0)

    assert any("boom-task" in r.getMessage() and r.exc_info for r in caplog.records)


@pytest.mark.asyncio
async def test_drain_cancels_pending_tasks():
    cancelled = asyncio.Event()

    async def forever():
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = spawn(forever())
    await asyncio.sleep(0)
    await drain_background_tasks(timeout=1)
    assert task.cancelled()
    assert cancelled.is_set()
    assert pending_background_tasks() == 0


@pytest.mark.asyncio
async def test_region_backfill_is_single_flight_and_coalesces_latest():
    import app.services.messages as messages_service

    gate = asyncio.Event()
    calls: list[list[str]] = []

    async def fake_backfill(regions):
        calls.append(list(regions))
        await gate.wait()
        return {}

    with patch.object(messages_service, "backfill_message_regions", fake_backfill):
        first = messages_service.schedule_region_backfill(["a"])
        await asyncio.sleep(0)
        second = messages_service.schedule_region_backfill(["b"])
        third = messages_service.schedule_region_backfill(["c"])
        assert first is second is third
        gate.set()
        await first

    # One pass with the first list, then a single follow-up with the newest one.
    assert calls == [["a"], ["c"]]
