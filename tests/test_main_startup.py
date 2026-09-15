"""Tests for app startup/lifespan behavior."""

import asyncio
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from app.main import app, lifespan
from app.models import RadioTransportSnapshot

_CONFIGURED = RadioTransportSnapshot(transport="serial", serial_port="/dev/ttyUSB0")


class TestStartupLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_does_not_wait_for_radio_setup(self):
        """HTTP serving should start before post-connect setup finishes."""
        setup_started = asyncio.Event()
        release_setup = asyncio.Event()

        async def slow_setup(*_args, **_kwargs):
            setup_started.set()
            await release_setup.wait()
            return True

        with ExitStack() as stack:
            for target, kwargs in (
                ("app.main.db.connect", {"new": AsyncMock()}),
                ("app.main.db.disconnect", {"new": AsyncMock()}),
                ("app.radio_sync.ensure_default_channels", {"new": AsyncMock()}),
                ("app.main.radio_manager.start_connection_monitor", {"new": AsyncMock()}),
                ("app.main.radio_manager.stop_connection_monitor", {"new": AsyncMock()}),
                ("app.main.radio_manager.disconnect", {"new": AsyncMock()}),
                (
                    "app.main.radio_manager.reconnect_and_prepare",
                    {"new": AsyncMock(side_effect=slow_setup)},
                ),
                (
                    "app.services.radio_transport.maybe_import_legacy_env",
                    {"new": AsyncMock(return_value=_CONFIGURED)},
                ),
                (
                    "app.services.radio_transport.get_transport",
                    {"new": AsyncMock(return_value=_CONFIGURED)},
                ),
                (
                    "app.services.radio_transport.database_has_mesh_history",
                    {"new": AsyncMock(return_value=False)},
                ),
                ("app.fanout.manager.fanout_manager.load_from_db", {"new": AsyncMock()}),
                ("app.services.meshloom_community.seed_community_from_env", {"new": AsyncMock()}),
                ("app.fanout.manager.fanout_manager.stop_all", {"new": AsyncMock()}),
                ("app.main.stop_message_polling", {"new": AsyncMock()}),
                ("app.main.stop_periodic_advert", {"new": AsyncMock()}),
                ("app.main.stop_periodic_sync", {"new": AsyncMock()}),
                ("app.main.stop_telemetry_collect", {"new": AsyncMock()}),
                ("app.main.stop_background_contact_reconciliation", {"new": AsyncMock()}),
                ("app.main.stop_radio_stats_sampling", {"new": AsyncMock()}),
                ("app.main.stop_oss_update_polling", {"new": AsyncMock()}),
                ("app.main.stop_stale_contact_purge", {"new": AsyncMock()}),
                ("app.main.start_radio_stats_sampling", {"new": AsyncMock()}),
                ("app.main.start_oss_update_polling", {"new": AsyncMock()}),
                ("app.main.start_stale_contact_purge", {}),
                ("app.push.vapid.ensure_vapid_keys", {"new": AsyncMock()}),
                ("app.websocket.broadcast_health", {}),
            ):
                stack.enter_context(patch(target, **kwargs))

            cm = lifespan(app)
            await asyncio.wait_for(cm.__aenter__(), timeout=2)

            await asyncio.wait_for(setup_started.wait(), timeout=2)
            startup_task = app.state.startup_radio_task
            assert startup_task.done() is False

            release_setup.set()
            await asyncio.wait_for(cm.__aexit__(None, None, None), timeout=2)
