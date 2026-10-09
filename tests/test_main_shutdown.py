"""Shutdown is fault tolerant: every step runs, radio steps are bounded, DB always closes."""

import asyncio
import logging
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.main as main
from app.models import RadioTransportSnapshot

_STOPPERS = [
    "stop_background_contact_reconciliation",
    "stop_message_polling",
    "stop_radio_stats_sampling",
    "stop_oss_update_polling",
    "stop_hashtag_catalogue_polling",
    "stop_periodic_advert",
    "stop_periodic_sync",
    "stop_telemetry_collect",
    "stop_stale_contact_purge",
]


@pytest.mark.asyncio
async def test_failing_step_does_not_skip_the_rest(caplog):
    mocks = {name: AsyncMock() for name in _STOPPERS}
    mocks["stop_message_polling"].side_effect = RuntimeError("poll stop exploded")
    fanout = MagicMock(stop_all=AsyncMock(side_effect=RuntimeError("fanout")))
    proxy = MagicMock(stop=AsyncMock())

    with ExitStack() as stack:
        for name, mock in mocks.items():
            stack.enter_context(patch.object(main, name, mock))
        stack.enter_context(patch.object(main.radio_manager, "_meshcore", None))
        stop_monitor = stack.enter_context(
            patch.object(main.radio_manager, "stop_connection_monitor", AsyncMock())
        )
        disconnect = stack.enter_context(
            patch.object(main.radio_manager, "disconnect", AsyncMock())
        )
        stack.enter_context(
            patch("app.services.community_live.shutdown_community_live", AsyncMock())
        )
        with caplog.at_level(logging.ERROR, logger="app.main"):
            await main._shutdown(None, fanout, proxy)

    for mock in mocks.values():
        mock.assert_awaited_once()
    proxy.stop.assert_awaited_once()
    stop_monitor.assert_awaited_once()
    disconnect.assert_awaited_once()
    messages = [r.getMessage() for r in caplog.records]
    assert "Shutdown step failed: message polling" in messages
    assert "Shutdown step failed: fanout modules" in messages


@pytest.mark.asyncio
async def test_hung_radio_disconnect_is_bounded(caplog):
    async def hang():
        await asyncio.sleep(3600)

    with (
        patch.object(main, "SHUTDOWN_RADIO_TIMEOUT_SECONDS", 0.05),
        caplog.at_level(logging.ERROR, logger="app.main"),
    ):
        await asyncio.wait_for(
            main._shutdown_step("radio disconnect", hang, main.SHUTDOWN_RADIO_TIMEOUT_SECONDS),
            timeout=1,
        )
    assert any("timed out" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_synchronous_raise_in_step_is_contained():
    def boom():
        raise ValueError("sync")

    await main._shutdown_step("sync step", boom)


@pytest.mark.asyncio
async def test_db_disconnect_runs_even_if_teardown_raises():
    configured = RadioTransportSnapshot(transport="serial", serial_port="/dev/ttyUSB0")
    db_disconnect = AsyncMock()
    with ExitStack() as stack:
        for target, kwargs in (
            ("app.main.db.connect", {"new": AsyncMock()}),
            ("app.main.db.disconnect", {"new": db_disconnect}),
            ("app.main._shutdown", {"new": AsyncMock(side_effect=RuntimeError("teardown"))}),
            ("app.radio_sync.ensure_default_channels", {"new": AsyncMock()}),
            ("app.main.radio_manager.start_connection_monitor", {"new": AsyncMock()}),
            ("app.main.radio_manager.reconnect_and_prepare", {"new": AsyncMock()}),
            (
                "app.services.radio_transport.maybe_import_legacy_env",
                {"new": AsyncMock(return_value=configured)},
            ),
            (
                "app.services.radio_transport.get_transport",
                {"new": AsyncMock(return_value=configured)},
            ),
            (
                "app.services.radio_transport.database_has_mesh_history",
                {"new": AsyncMock(return_value=False)},
            ),
            ("app.fanout.manager.fanout_manager.load_from_db", {"new": AsyncMock()}),
            ("app.radio_proxy.manager.radio_proxy_manager.start_from_db", {"new": AsyncMock()}),
            ("app.services.meshloom_community.seed_community_from_env", {"new": AsyncMock()}),
            ("app.main.start_radio_stats_sampling", {"new": AsyncMock()}),
            ("app.main.start_oss_update_polling", {"new": AsyncMock()}),
            ("app.main.start_hashtag_catalogue_polling", {}),
            ("app.main.start_stale_contact_purge", {}),
            ("app.push.vapid.ensure_vapid_keys", {"new": AsyncMock()}),
            ("app.websocket.broadcast_health", {}),
        ):
            stack.enter_context(patch(target, **kwargs))

        cm = main.lifespan(main.app)
        await asyncio.wait_for(cm.__aenter__(), timeout=2)
        with pytest.raises(RuntimeError, match="teardown"):
            await asyncio.wait_for(cm.__aexit__(None, None, None), timeout=2)

    db_disconnect.assert_awaited_once()
