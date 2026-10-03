"""Pytest configuration and shared fixtures."""

import os
import shutil
import tempfile
from pathlib import Path

import httpx
import pytest

# Isolate the file-backed SQLite DB per xdist worker before app.config/app.database
# import. setdefault is not enough: forked workers inherit the controller env and
# would otherwise share one file while TestClient now always runs lifespan.
_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")
_TEST_DB_DIR = Path(tempfile.mkdtemp(prefix=f"meshcore-pytest-{_WORKER}-"))
_TEST_DB_PATH = _TEST_DB_DIR / "meshcore.db"
os.environ["MESHCORE_DATABASE_PATH"] = str(_TEST_DB_PATH)

from app.database import Database  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def cleanup_test_db_dir():
    """Clean up temporary pytest DB directory after the test session."""
    yield
    shutil.rmtree(_TEST_DB_DIR, ignore_errors=True)


@pytest.fixture
async def test_db():
    """Create an in-memory test database with schema + migrations."""
    from app.repository import (
        channels,
        contact_groups,
        contact_telemetry,
        contacts,
        directory,
        messages,
        push_subscriptions,
        radio_proxy,
        radio_transport,
        raw_packets,
        repeater_pane_cache,
        repeater_telemetry,
        settings,
        telemetry_alert_state,
    )
    from app.repository import fanout as fanout_repo

    db = Database(":memory:")
    await db.connect()

    submodules = [
        contacts,
        channels,
        contact_groups,
        directory,
        messages,
        radio_proxy,
        radio_transport,
        raw_packets,
        settings,
        fanout_repo,
        repeater_telemetry,
        repeater_pane_cache,
        contact_telemetry,
        push_subscriptions,
        telemetry_alert_state,
    ]
    originals = [(mod, mod.db) for mod in submodules]

    for mod in submodules:
        mod.db = db

    # Also patch the db reference used by the packets router for VACUUM
    import app.routers.packets as packets_module

    original_packets_db = packets_module.db
    packets_module.db = db

    try:
        yield db
    finally:
        for mod, original in originals:
            mod.db = original
        packets_module.db = original_packets_db
        await db.disconnect()


@pytest.fixture(autouse=True)
async def _reset_hashtag_catalogue():
    """Cancel ingest-triggered catalogue work so tasks do not leak between tests."""
    from app.services.hashtag_catalogue import reset_for_tests

    yield
    await reset_for_tests()


@pytest.fixture(autouse=True)
def _quiet_oss_update_fetch(monkeypatch):
    """Lifespan starts the OSS poll; never hit Stats from TestClient tests."""
    from app.services.oss_updates import reset_oss_update_cache

    async def _no_network():
        return None

    monkeypatch.setattr("app.services.oss_updates.fetch_meshloom_latest", _no_network)
    reset_oss_update_cache()
    yield
    reset_oss_update_cache()


@pytest.fixture(autouse=True)
def _reset_radio_ingest_gate():
    """Keep the process-wide ingest gate open between tests."""
    from app.radio import radio_manager
    from app.services.radio_ingest_gate import allow_ingest
    from app.services.radio_registry import radio_registry

    def reset() -> None:
        allow_ingest()
        if radio_registry.has("default"):
            default_radio = radio_registry.get_default()
            default_radio.allow_ingest()
            default_radio.connection_desired = True
        radio_manager.connection_desired = True

    reset()
    yield
    reset()


@pytest.fixture(autouse=True)
def _reset_radio_channel_slots():
    """Hand every test an empty channel slot cache.

    The radio holds a handful of channel slots and the manager remembers which
    key sits in which, process-wide. A test that fills slot 0 leaves the next one
    starting at 1, and a test asserting it wrote to slot 0 fails on a number it
    never chose. Which tests share a worker decides whether that happens.
    """
    from app.radio import radio_manager
    from app.services.radio_registry import radio_registry

    def clear() -> None:
        for rid in [k for k in list(radio_registry._instances.keys()) if k != "default"]:
            radio_registry.unregister(rid)
        radio_manager._channel_slot_by_key.clear()
        radio_manager._channel_key_by_slot.clear()
        radio_manager._pending_message_channel_key_by_slot.clear()
        if radio_registry.has("default"):
            default_radio = radio_registry.get_default()
            if default_radio is not radio_manager:
                default_radio._channel_slot_by_key.clear()
                default_radio._channel_key_by_slot.clear()
                default_radio._pending_message_channel_key_by_slot.clear()

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
def _reset_radio_operation_lock():
    """Hand every test an unheld radio lock.

    The lock lives on a process-wide manager and the non-blocking path reads
    `.locked()` before it tries to acquire, so a test that leaves it held makes
    the radio look busy to every test after it in the same xdist worker. What
    that looks like is a watchdog that quietly declines to send and an assertion
    failing somewhere unrelated, on some runs and not others.

    Cleared rather than replaced: the manager builds one on demand, bound to the
    loop that is actually running.
    """
    from app.radio import radio_manager
    from app.services.radio_registry import radio_registry

    def clear() -> None:
        for rid in [k for k in list(radio_registry._instances.keys()) if k != "default"]:
            radio_registry.unregister(rid)
        radio_manager._operation_lock = None
        radio_manager._reconnect_lock = None
        radio_manager._setup_lock = None
        radio_manager._lifecycle_lock = None
        if not radio_registry.has("default"):
            radio_registry.register(radio_manager)
        elif radio_registry.get_default() is not radio_manager:
            default_radio = radio_registry.get_default()
            default_radio._operation_lock = None
            default_radio._reconnect_lock = None
            default_radio._setup_lock = None
            default_radio._lifecycle_lock = None

    clear()
    yield
    clear()


@pytest.fixture(autouse=True)
async def _reset_radio_proxy_runtime():
    """Stop the process-wide proxy so TCP sessions and locks do not leak."""
    from app.radio_proxy.manager import radio_proxy_manager

    yield
    try:
        await radio_proxy_manager.stop()
    except Exception:
        pass
    radio_proxy_manager._sessions.clear()
    radio_proxy_manager._pending_acks.clear()
    radio_proxy_manager._last_error = None


@pytest.fixture
def client():
    """Create an httpx AsyncClient for testing the app."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.fixture
def captured_broadcasts():
    """Capture WebSocket broadcasts for verification."""
    broadcasts = []

    def mock_broadcast(event_type: str, data: dict, **kwargs):
        broadcasts.append({"type": event_type, "data": data})

    return broadcasts, mock_broadcast
