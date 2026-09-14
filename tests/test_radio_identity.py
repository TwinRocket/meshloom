from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import RadioTransportSnapshot
from app.services.radio_identity import evaluate_connected_identity
from app.services.radio_ingest_gate import allow_ingest, begin_connection_session, deny_ingest


@pytest.mark.asyncio
async def test_first_radio_on_empty_db_binds(monkeypatch):
    snapshot = RadioTransportSnapshot(transport="serial")
    monkeypatch.setattr(
        "app.services.radio_identity.get_transport", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        "app.services.radio_identity.read_radio_public_key",
        AsyncMock(return_value=("aa" * 32, "Alice")),
    )
    monkeypatch.setattr(
        "app.services.radio_transport.database_has_mesh_history",
        AsyncMock(return_value=False),
    )
    bind = AsyncMock()
    monkeypatch.setattr(
        "app.services.radio_identity.RadioTransportRepository.bind_public_key",
        bind,
    )
    mc = MagicMock()
    assert await evaluate_connected_identity(mc) == "continue"
    bind.assert_awaited_once_with("aa" * 32)


@pytest.mark.asyncio
async def test_same_key_continues(monkeypatch):
    snapshot = RadioTransportSnapshot(transport="serial", bound_public_key="bb" * 32)
    monkeypatch.setattr(
        "app.services.radio_identity.get_transport", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        "app.services.radio_identity.read_radio_public_key",
        AsyncMock(return_value=("bb" * 32, "Bob")),
    )
    gate = AsyncMock()
    monkeypatch.setattr(
        "app.services.radio_identity.RadioTransportRepository.set_identity_gate",
        gate,
    )
    assert await evaluate_connected_identity(MagicMock()) == "continue"
    gate.assert_not_awaited()


@pytest.mark.asyncio
async def test_mismatch_stops_without_allowing_ingest(monkeypatch):
    snapshot = RadioTransportSnapshot(transport="serial", bound_public_key="aa" * 32)
    monkeypatch.setattr(
        "app.services.radio_identity.get_transport", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        "app.services.radio_identity.read_radio_public_key",
        AsyncMock(return_value=("cc" * 32, "Carol")),
    )
    gate = AsyncMock()
    monkeypatch.setattr(
        "app.services.radio_identity.RadioTransportRepository.set_identity_gate",
        gate,
    )
    deny_ingest()
    session = begin_connection_session()
    allow_ingest(session)
    assert await evaluate_connected_identity(MagicMock()) == "stop"
    gate.assert_awaited_once()
    from app.services.radio_ingest_gate import ingest_allowed

    assert ingest_allowed() is False


@pytest.mark.asyncio
async def test_unbound_legacy_stops(monkeypatch):
    snapshot = RadioTransportSnapshot(transport="serial")
    monkeypatch.setattr(
        "app.services.radio_identity.get_transport", AsyncMock(return_value=snapshot)
    )
    monkeypatch.setattr(
        "app.services.radio_identity.read_radio_public_key",
        AsyncMock(return_value=("dd" * 32, "Dana")),
    )
    monkeypatch.setattr(
        "app.services.radio_transport.database_has_mesh_history",
        AsyncMock(return_value=True),
    )
    gate = AsyncMock()
    monkeypatch.setattr(
        "app.services.radio_identity.RadioTransportRepository.set_identity_gate",
        gate,
    )
    assert await evaluate_connected_identity(MagicMock()) == "stop"
    assert gate.await_args.kwargs["state"] == "identity_unbound_legacy"


@pytest.mark.asyncio
async def test_library_reconnect_reruns_identity_gate():
    from app.event_handlers import on_library_connected

    event = MagicMock()
    event.payload = {"reconnected": True}
    with (
        patch(
            "app.services.radio_identity.evaluate_connected_identity",
            new=AsyncMock(return_value="stop"),
        ) as evaluate,
        patch("app.services.radio_ingest_gate.deny_ingest") as deny,
        patch("app.services.radio_runtime.radio_runtime") as runtime,
        patch("app.websocket.broadcast_health"),
    ):
        runtime.meshcore = MagicMock()
        runtime.pause_connection = AsyncMock()
        await on_library_connected(event)
        deny.assert_called_once()
        assert runtime._setup_complete is False
        evaluate.assert_awaited_once()
        runtime.pause_connection.assert_awaited_once()


@pytest.mark.asyncio
async def test_library_handle_disconnect_closes_ingest_before_reconnect():
    from app.radio import RadioManager
    from app.services.radio_ingest_gate import allow_ingest, ingest_allowed

    allow_ingest()
    rm = RadioManager()
    rm._setup_complete = True
    original = AsyncMock()
    connection = MagicMock()
    cm = MagicMock()
    cm.handle_disconnect = original
    cm.connection = connection
    cm._meshloom_disconnect_gated = False
    mc = MagicMock()
    mc.connection_manager = cm

    rm._install_library_reconnect_gate(mc)
    await cm.handle_disconnect("serial_disconnect")

    assert ingest_allowed() is False
    assert rm._setup_complete is False
    original.assert_awaited_once_with("serial_disconnect")
    connection.set_disconnect_callback.assert_called()


@pytest.mark.asyncio
async def test_library_disconnect_closes_ingest():
    from app.event_handlers import on_library_disconnected
    from app.services.radio_ingest_gate import allow_ingest, ingest_allowed

    allow_ingest()
    with patch("app.services.radio_runtime.radio_runtime") as runtime:
        await on_library_disconnected(MagicMock())
        assert runtime._setup_complete is False
    assert ingest_allowed() is False


@pytest.mark.asyncio
async def test_ack_skipped_when_ingest_closed():
    from app.event_handlers import on_ack
    from app.services.radio_ingest_gate import deny_ingest

    deny_ingest()
    event = MagicMock()
    event.payload = {"code": "abcd"}
    with patch(
        "app.event_handlers.apply_dm_ack_code",
        new=AsyncMock(return_value=True),
    ) as apply_ack:
        await on_ack(event)
    apply_ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_wipe_clears_contact_groups_and_directory_cache(test_db, monkeypatch):
    import app.services.radio_identity as identity

    monkeypatch.setattr(identity, "db", test_db)
    async with test_db.tx() as conn:
        await conn.execute(
            "INSERT INTO contacts (public_key, name) VALUES (?, ?)",
            ("a" * 64, "Alice"),
        )
        await conn.execute(
            "INSERT INTO contact_groups (name, sort_order, created_at) VALUES ('group-a', 0, 1)"
        )
        await conn.execute(
            """
            INSERT INTO directory_hop_cache (prefix, hash_width, name, source, expires_at)
            VALUES ('ab', 2, 'node', 'corescope', 1)
            """
        )
    await identity.wipe_mesh_identity_data()
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT COUNT(*) FROM contacts") as cursor:
            assert (await cursor.fetchone())[0] == 0
        async with conn.execute("SELECT COUNT(*) FROM contact_groups") as cursor:
            assert (await cursor.fetchone())[0] == 0


def test_all_zero_key_is_not_an_identity():
    """A peer that does not know its own key must not look like a different radio.

    The proxy answered with 64 zeros while its own radio was still coming up. That
    is a well-formed hex string, so it passed every shape check, compared unequal
    to the bound key, and produced an offer to erase 138 contacts and 1485 messages
    in order to adopt a node that does not exist.
    """
    from app.services.radio_identity import _normalize_key

    assert _normalize_key("00" * 32) is None
    assert _normalize_key("0" * 64) is None
    # A real key that merely starts with zeros is untouched.
    assert _normalize_key("00" * 31 + "a1") == "00" * 31 + "a1"


@pytest.mark.asyncio
async def test_zero_key_fails_closed_instead_of_claiming_a_swap(monkeypatch):
    snapshot = RadioTransportSnapshot(transport="tcp", bound_public_key="aa" * 32)
    monkeypatch.setattr(
        "app.services.radio_identity.get_transport", AsyncMock(return_value=snapshot)
    )
    mc = MagicMock()
    mc.send_appstart = AsyncMock(return_value={"public_key": "00" * 32, "name": "Meshloom"})
    mc.self_info = {"public_key": "00" * 32, "name": "Meshloom"}
    gate = AsyncMock()
    monkeypatch.setattr(
        "app.services.radio_identity.RadioTransportRepository.set_identity_gate",
        gate,
    )

    assert await evaluate_connected_identity(mc) == "stop"
    # Crucially: it stops without recording a mismatch, so nothing offers to wipe.
    gate.assert_not_awaited()
