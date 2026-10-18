"""Pytest configuration and shared fixtures."""

import os
import shutil
import tempfile
from contextlib import asynccontextmanager
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


@asynccontextmanager
async def patched_database(path: str = ":memory:"):
    """A connected Database with schema + migrations, wired into every repository."""
    from app.repository import (
        auth,
        channels,
        contact_groups,
        contact_telemetry,
        contacts,
        directory,
        messages,
        plugins,
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

    db = Database(path)
    await db.connect()

    submodules = [
        auth,
        contacts,
        channels,
        contact_groups,
        directory,
        messages,
        plugins,
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


@pytest.fixture
async def test_db():
    """Create an in-memory test database with schema + migrations."""
    async with patched_database() as db:
        yield db


@pytest.fixture(autouse=True)
async def _reset_hashtag_catalogue():
    """Cancel ingest-triggered catalogue work so tasks do not leak between tests."""
    from app.services.hashtag_catalogue import reset_for_tests

    yield
    await reset_for_tests()


@pytest.fixture(autouse=True)
def _quiet_oss_update_fetch(monkeypatch):
    """Lifespan starts the release poll; never hit GitHub or Community from tests."""
    from app.services.oss_updates import reset_oss_update_cache

    async def _no_network():
        return None

    monkeypatch.setattr("app.services.oss_updates.fetch_meshloom_latest", _no_network)
    monkeypatch.setattr("app.services.oss_updates.fetch_github_latest", _no_network)
    reset_oss_update_cache()
    yield
    reset_oss_update_cache()


@pytest.fixture(autouse=True)
def _reset_community_breaker():
    """Every test starts with a closed Community circuit breaker."""
    from app.services.meshloom_community import reset_stats_client_for_tests

    reset_stats_client_for_tests()
    yield
    reset_stats_client_for_tests()


@pytest.fixture(autouse=True)
async def _stop_community_status_clear():
    """The retained-status clear worker must not outlive the test that started it."""
    from app.services.community_status_clear import stop_status_clear_worker

    yield
    await stop_status_clear_worker()


@pytest.fixture(autouse=True)
def _reset_radio_workarounds():
    """A workaround switched on by one test must not leak into the next one."""
    from app import radio_workarounds

    radio_workarounds.reset()
    yield
    radio_workarounds.reset()


@pytest.fixture(autouse=True)
def _reset_cors_allowed_origins():
    """An origin allowed by one test must not leak into the next one."""
    from app import origin_policy

    origin_policy.reset()
    yield
    origin_policy.reset()


@pytest.fixture(autouse=True)
def _reset_position_rebind():
    """A SELF_INFO seen by one test must not re-bind in the next one."""
    from app.services.observer_position import reset_position_rebind_for_tests

    reset_position_rebind_for_tests()
    yield
    reset_position_rebind_for_tests()


@pytest.fixture(autouse=True)
def _reset_radio_ingest_gate():
    """Keep the process-wide ingest gate open between tests."""
    from app.radio import radio_manager
    from app.services.radio_ingest_gate import allow_ingest

    allow_ingest()
    radio_manager.connection_desired = True
    yield
    allow_ingest()
    radio_manager.connection_desired = True


@pytest.fixture(autouse=True)
def _reset_expected_reboot():
    """Forget an expected radio reboot (clock fix) recorded by a previous test.

    It lives on the process-wide manager: left set, it turns every later health
    payload into "rebooting" and silences reconnect errors in the same worker.
    """
    from app.radio import radio_manager

    radio_manager.clear_expected_reboot()
    yield
    radio_manager.clear_expected_reboot()


@pytest.fixture(autouse=True)
def _reset_radio_channel_slots():
    """Hand every test an empty channel slot cache.

    The radio holds a handful of channel slots and the manager remembers which
    key sits in which, process-wide. A test that fills slot 0 leaves the next one
    starting at 1, and a test asserting it wrote to slot 0 fails on a number it
    never chose. Which tests share a worker decides whether that happens.
    """
    from app.radio import radio_manager

    def clear() -> None:
        radio_manager._channel_slot_by_key.clear()
        radio_manager._channel_key_by_slot.clear()
        radio_manager._pending_message_channel_key_by_slot.clear()

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

    radio_manager._operation_lock = None
    yield
    radio_manager._operation_lock = None


@pytest.fixture(autouse=True)
def _no_system_dbus(monkeypatch):
    """No test touches the host's system D-Bus or BlueZ.

    BLE pre-pairing then behaves as on a host without the D-Bus socket
    (environmental fallback). Tests that exercise pairing replace `_open_bus`
    with a fake or a private dbus-daemon.
    """
    from app.services import ble_pairing

    async def _unavailable():
        raise ble_pairing.BlePairingError("ble_pair_dbus_unavailable")

    monkeypatch.setattr(ble_pairing, "_open_bus", _unavailable)
    ble_pairing.reset_for_tests()
    yield
    ble_pairing.reset_for_tests()


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


@pytest.fixture
def secret_debug_logs(caplog):
    """DEBUG everywhere with the redaction and the ring buffer attached; use
    ``tests.fixtures.log_capture.assert_no_secret_logged`` to check."""
    from tests.fixtures.log_capture import debug_capture

    with debug_capture(caplog):
        yield caplog


@pytest.fixture
async def backup_db(test_db, monkeypatch):
    """``test_db`` also seen by code that reads ``app.database.db`` directly
    (mesh history check, identity wipe), as the backup restore does."""
    import app.database as database_module
    import app.services.radio_identity as radio_identity

    monkeypatch.setattr(database_module, "db", test_db)
    monkeypatch.setattr(radio_identity, "db", test_db)
    yield test_db
