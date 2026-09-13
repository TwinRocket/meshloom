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
        radio_transport,
        raw_packets,
        repeater_pane_cache,
        repeater_telemetry,
        settings,
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
        radio_transport,
        raw_packets,
        settings,
        fanout_repo,
        repeater_telemetry,
        repeater_pane_cache,
        contact_telemetry,
        push_subscriptions,
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
def _reset_radio_ingest_gate():
    """Keep the process-wide ingest gate open between tests."""
    from app.radio import radio_manager
    from app.services.radio_ingest_gate import allow_ingest

    allow_ingest()
    radio_manager.connection_desired = True
    yield
    allow_ingest()
    radio_manager.connection_desired = True


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
