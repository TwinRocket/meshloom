"""Tests for RadioInstance, RadioRegistry, and multi-radio runtime dispatch."""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.models import RadioTransportSnapshot
from app.services.radio_instance import (
    RadioInstance,
    RadioOperationBusyError,
)
from app.services.radio_registry import (
    RadioNotFoundError,
    RadioRegistry,
)
from app.services.radio_runtime import RadioRuntime


class TestRadioInstanceDomain:
    """Validate RadioInstance encapsulation and behavior."""

    def test_default_attributes(self):
        instance = RadioInstance()
        assert instance.radio_id == "default"
        assert instance.name == "Primary Radio"
        assert instance.meshcore is None
        assert instance.connection_info is None
        assert instance.is_connected is False
        assert instance.is_reconnecting is False
        assert instance.is_setup_in_progress is False
        assert instance.is_setup_complete is False
        assert instance.connection_desired is True

        # Locks must be None on init (PR #22 & #30: lazy creation in running loop only)
        assert instance._operation_lock is None
        assert instance._reconnect_lock is None
        assert instance._setup_lock is None
        assert instance._lifecycle_lock is None

        # Ingest gate initial state
        assert instance.ingest_allowed() is False
        assert instance.current_session == 0

        # Keystore initial state
        assert instance.get_private_key() is None
        assert instance.get_public_key() is None
        assert instance.has_private_key() is False

        # Channel cache initial state
        assert instance.get_channel_send_cache_snapshot() == []

    def test_custom_attributes(self):
        snapshot = RadioTransportSnapshot(transport="tcp", tcp_host="10.0.0.5", tcp_port=5000)
        instance = RadioInstance(
            radio_id="radio_east",
            name="East Coast Node",
            transport_snapshot=snapshot,
        )
        assert instance.radio_id == "radio_east"
        assert instance.name == "East Coast Node"
        assert instance._transport_snapshot == snapshot

    def test_ingest_gate_lifecycle(self):
        instance = RadioInstance(radio_id="test_ingest")
        assert instance.ingest_allowed() is False
        assert instance.current_session == 0

        gen1 = instance.begin_connection_session()
        assert gen1 == 1
        assert instance.current_session == 1
        assert instance.ingest_allowed() is False

        # Stale session allow is ignored
        instance.allow_ingest(session=999)
        assert instance.ingest_allowed() is False

        # Correct session allow succeeds
        instance.allow_ingest(session=1)
        assert instance.ingest_allowed() is True

        # Deny closes ingest
        instance.deny_ingest()
        assert instance.ingest_allowed() is False

        # Unconditional allow succeeds
        instance.allow_ingest()
        assert instance.ingest_allowed() is True

    def test_keystore_lifecycle(self):
        instance = RadioInstance(radio_id="test_keystore")

        # Valid 64-byte key
        scalar = bytearray(b"\x01" * 32)
        scalar[0] &= 0xF8
        scalar[31] &= 0x7F
        scalar[31] |= 0x40
        prefix = b"\x02" * 32
        valid_key = bytes(scalar) + prefix

        with pytest.raises(ValueError, match="64 bytes"):
            instance.set_private_key(b"\x00" * 32)

        instance.set_private_key(valid_key)
        assert instance.get_private_key() == valid_key
        assert instance.has_private_key() is True
        pub = instance.get_public_key()
        assert pub is not None
        assert len(pub) == 32

        instance.clear_keys()
        assert instance.get_private_key() is None
        assert instance.get_public_key() is None
        assert instance.has_private_key() is False

    def test_stats_and_noise_floor(self):
        instance = RadioInstance(radio_id="test_stats")
        history = instance.get_noise_floor_history()
        assert history["samples"] == []
        assert history["latest_noise_floor_dbm"] is None

        instance._noise_floor_samples.append((1000, -110))
        instance._latest_stats = {"battery_mv": 3900}

        assert instance.get_latest_radio_stats() == {"battery_mv": 3900}

        instance.clear_latest_radio_stats()
        assert instance.get_latest_radio_stats() == {}
        assert len(instance._noise_floor_samples) == 0

    def test_channel_slot_cache(self):
        instance = RadioInstance(radio_id="test_slots")
        instance.max_channels = 8

        # Plan slot for channel A
        key_a = "AA" * 16
        slot_a, needs_configure, evicted = instance.plan_channel_send_slot(key_a, preferred_slot=0)
        assert slot_a == 0
        assert needs_configure is True
        assert evicted is None

        # Note loaded
        instance.note_channel_slot_loaded(key_a, 0)
        assert instance.get_cached_channel_slot(key_a) == 0

        # Plan again returns cached without needing configuration
        slot_a_cached, needs_configure, evicted = instance.plan_channel_send_slot(key_a)
        assert slot_a_cached == 0
        assert needs_configure is False

        # Invalidate
        instance.invalidate_cached_channel_slot(key_a)
        assert instance.get_cached_channel_slot(key_a) is None

    @pytest.mark.asyncio
    async def test_operation_lock_isolation_between_instances(self):
        """Holding lock on radio A does not block radio B."""
        radio_a = RadioInstance(radio_id="radio_a")
        radio_b = RadioInstance(radio_id="radio_b")

        mc_a = MagicMock()
        mc_b = MagicMock()
        radio_a._meshcore = mc_a
        radio_b._meshcore = mc_b

        radio_a_held = asyncio.Event()
        radio_a_release = asyncio.Event()

        async def holder():
            async with radio_a.radio_operation("holder_a"):
                radio_a_held.set()
                await radio_a_release.wait()

        task = asyncio.create_task(holder())
        await radio_a_held.wait()

        # radio A is busy
        with pytest.raises(RadioOperationBusyError):
            async with radio_a.radio_operation("busy_check", blocking=False):
                pass

        # radio B acquires lock without being blocked by radio A
        acquired_b = False
        async with radio_b.radio_operation("op_b", blocking=False) as mc:
            assert mc is mc_b
            acquired_b = True

        assert acquired_b is True

        radio_a_release.set()
        await task


class TestRadioRegistry:
    """Validate RadioRegistry registration and lookup semantics."""

    def test_register_and_get(self):
        registry = RadioRegistry(default_radio_id="default")
        radio1 = RadioInstance(radio_id="default", name="Default Radio")
        radio2 = RadioInstance(radio_id="secondary", name="Secondary Radio")

        registry.register(radio1)
        registry.register(radio2)

        assert registry.get() is radio1
        assert registry.get(None) is radio1
        assert registry.get("default") is radio1
        assert registry.get_default() is radio1
        assert registry.get("secondary") is radio2
        assert registry.has("default") is True
        assert registry.has("secondary") is True
        assert registry.has("unknown") is False

        all_radios = registry.all()
        assert len(all_radios) == 2
        assert radio1 in all_radios
        assert radio2 in all_radios

    def test_get_unknown_raises(self):
        registry = RadioRegistry()
        with pytest.raises(RadioNotFoundError) as exc:
            registry.get("nonexistent")
        assert issubclass(RadioNotFoundError, KeyError)
        assert "nonexistent" in str(exc.value)

    def test_unregister(self):
        registry = RadioRegistry()
        radio = RadioInstance(radio_id="temp", name="Temporary")
        registry.register(radio)
        assert registry.has("temp") is True

        unregistered = registry.unregister("temp")
        assert unregistered is radio
        assert registry.has("temp") is False
        assert registry.unregister("temp") is None

    def test_register_empty_id_raises(self):
        registry = RadioRegistry()
        with pytest.raises(ValueError, match="non-empty radio_id"):
            registry.register(RadioInstance(radio_id=""))


class TestRadioRuntimeDispatch:
    """Validate RadioRuntime multi-radio forwarding."""

    def test_runtime_get_forwards_to_registry(self):
        registry = RadioRegistry()
        default_inst = RadioInstance(radio_id="default", name="Default")
        east_inst = RadioInstance(radio_id="east", name="East")
        registry.register(default_inst)
        registry.register(east_inst)

        runtime = RadioRuntime(lambda: registry.get_default())
        assert runtime.get() is default_inst

        # Resolve explicit radio_id through global registry
        from unittest.mock import patch

        with patch("app.services.radio_registry.radio_registry", registry):
            assert runtime.get("east") is east_inst
            assert runtime.get("default") is default_inst

    @pytest.mark.asyncio
    async def test_runtime_radio_operation_targets_specific_radio(self):
        registry = RadioRegistry()
        r1 = RadioInstance(radio_id="default")
        r2 = RadioInstance(radio_id="r2")
        mc1 = MagicMock()
        mc2 = MagicMock()
        r1._meshcore = mc1
        r2._meshcore = mc2
        registry.register(r1)
        registry.register(r2)

        runtime = RadioRuntime()
        from unittest.mock import patch

        with patch("app.services.radio_registry.radio_registry", registry):
            async with runtime.radio_operation("op_default") as mc:
                assert mc is mc1

            async with runtime.radio_operation("op_r2", radio_id="r2") as mc:
                assert mc is mc2

    def test_runtime_require_connected_targets_specific_radio(self):
        registry = RadioRegistry()
        r1 = RadioInstance(radio_id="default")
        r2 = RadioInstance(radio_id="r2")
        mc2 = MagicMock()
        r2._meshcore = mc2
        registry.register(r1)
        registry.register(r2)

        runtime = RadioRuntime()
        from unittest.mock import patch

        with patch("app.services.radio_registry.radio_registry", registry):
            # r1 is not connected -> 423
            with pytest.raises(HTTPException) as exc:
                runtime.require_connected()
            assert exc.value.status_code == 423

            # r2 has mc2 connected
            r2._setup_complete = True
            assert runtime.require_connected(radio_id="r2") is mc2

    def test_runtime_manager_setter_and_attribute_forwarding(self):
        """RadioRuntime forwards private attrs to manager and allows rebinding manager via setter."""
        custom_radio = RadioInstance(radio_id="custom")
        runtime = RadioRuntime()
        runtime.manager = custom_radio

        assert runtime.manager is custom_radio

        # Setting private radio attribute on runtime forwards to manager
        mock_mc = MagicMock()
        runtime._meshcore = mock_mc
        assert custom_radio._meshcore is mock_mc

        # Setting local runtime attribute does not affect manager
        runtime._radio_id = "new_id"
        assert runtime._radio_id == "new_id"


class TestMultiRadioIsolationAndConcurrency:
    """Validate concurrency, isolation, and lifecycle safety between radio instances."""

    @pytest.mark.asyncio
    async def test_concurrent_operations_on_separate_radios(self):
        """Concurrent radio_operation calls on different radios run simultaneously."""
        radio_a = RadioInstance(radio_id="radio_a")
        radio_b = RadioInstance(radio_id="radio_b")
        mc_a = MagicMock()
        mc_b = MagicMock()
        radio_a._meshcore = mc_a
        radio_b._meshcore = mc_b

        timeline: list[str] = []

        async def worker_a():
            async with radio_a.radio_operation("op_a"):
                timeline.append("a_start")
                await asyncio.sleep(0.02)
                timeline.append("a_end")

        async def worker_b():
            async with radio_b.radio_operation("op_b"):
                timeline.append("b_start")
                await asyncio.sleep(0.02)
                timeline.append("b_end")

        await asyncio.gather(worker_a(), worker_b())
        assert "a_start" in timeline and "b_start" in timeline
        assert "a_end" in timeline and "b_end" in timeline

    def test_channel_slot_cache_isolation_between_radios(self):
        """Channel slot caches on distinct radio instances are isolated."""
        radio_a = RadioInstance(radio_id="radio_a")
        radio_b = RadioInstance(radio_id="radio_b")
        radio_a.max_channels = 8
        radio_b.max_channels = 8

        key = "11" * 16
        slot_a, _, _ = radio_a.plan_channel_send_slot(key, preferred_slot=2)
        radio_a.note_channel_slot_loaded(key, slot_a)

        assert radio_a.get_cached_channel_slot(key) == 2
        # radio_b does not see radio_a's cached slot
        assert radio_b.get_cached_channel_slot(key) is None

        # radio_b can assign slot 0 independently
        slot_b, _, _ = radio_b.plan_channel_send_slot(key, preferred_slot=0)
        assert slot_b == 0

    @pytest.mark.asyncio
    async def test_subscription_cleanup_isolation(self):
        """Disconnecting radio A unsubscribes only radio A's subscriptions."""
        from app.event_handlers import register_event_handlers

        radio_a = RadioInstance(radio_id="radio_a")
        radio_b = RadioInstance(radio_id="radio_b")

        sub_a1 = MagicMock()
        sub_b1 = MagicMock()

        mc_a = MagicMock()
        mc_a.subscribe.return_value = sub_a1
        mc_b = MagicMock()
        mc_b.subscribe.return_value = sub_b1

        radio_a._meshcore = mc_a
        radio_b._meshcore = mc_b

        register_event_handlers(mc_a, radio_instance=radio_a)
        register_event_handlers(mc_b, radio_instance=radio_b)

        assert len(radio_a._subscriptions) > 0
        assert len(radio_b._subscriptions) > 0

        # Disconnect radio_a
        await radio_a.disconnect()

        # radio_a subscriptions unsubscribed and cleared
        assert len(radio_a._subscriptions) == 0
        assert sub_a1.unsubscribe.called

        # radio_b subscriptions remain active
        assert len(radio_b._subscriptions) > 0
        assert not sub_b1.unsubscribe.called

    def test_registry_register_default_flag(self):
        """Registering with default=True switches the default radio ID."""
        registry = RadioRegistry(default_radio_id="primary")
        r1 = RadioInstance(radio_id="primary")
        r2 = RadioInstance(radio_id="secondary")

        registry.register(r1)
        assert registry.get_default() is r1

        registry.register(r2, default=True)
        assert registry.get_default() is r2
        assert registry.default_radio_id == "secondary"

    def test_registry_mock_detection(self):
        """Mock detection in RadioRegistry.get honors NonCallableMock or replaced manager."""
        from unittest.mock import patch

        registry = RadioRegistry(default_radio_id="default")
        real_radio = RadioInstance(radio_id="default")
        registry.register(real_radio)

        mock_manager = MagicMock()
        fake_modules = dict(__import__("sys").modules)
        fake_modules["app.radio"] = MagicMock(radio_manager=mock_manager)
        with patch("sys.modules", fake_modules):
            assert registry.get("default") is mock_manager
            assert registry.get() is mock_manager

    @pytest.mark.asyncio
    async def test_locks_rebind_on_different_event_loop_error(self):
        """RadioInstance locks rebind when encountering a different event loop error."""
        radio = RadioInstance(radio_id="test_rebind")

        # Simulate a lock bound to a dead loop that raises RuntimeError("different event loop")
        stale_lock = MagicMock()
        call_count = 0

        async def fake_acquire(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Task <Task-1> got Future attached to a different event loop")
            return True

        stale_lock.acquire = fake_acquire
        radio._operation_lock = stale_lock

        # _acquire_operation_lock catches RuntimeError and creates a new lock
        await radio._acquire_operation_lock("test_rebind_op", blocking=True)
        assert radio._operation_lock is not stale_lock
        radio._release_operation_lock("test_rebind_op")

        # Reconnect lock rebind test
        stale_reconnect_lock = MagicMock()
        call_count_rec = 0

        async def fake_reconnect_acquire(*args, **kwargs):
            nonlocal call_count_rec
            call_count_rec += 1
            if call_count_rec == 1:
                raise RuntimeError("Task <Task-2> got Future attached to a different event loop")
            return True

        stale_reconnect_lock.acquire = fake_reconnect_acquire
        stale_reconnect_lock.locked.return_value = False
        radio._reconnect_lock = stale_reconnect_lock
        radio._connection_desired = False

        res = await radio.reconnect()
        assert res is False
        assert radio._reconnect_lock is not stale_reconnect_lock

        # Setup lock rebind test
        stale_setup_lock = MagicMock()
        call_count_setup = 0

        async def fake_setup_acquire(*args, **kwargs):
            nonlocal call_count_setup
            call_count_setup += 1
            if call_count_setup == 1:
                raise RuntimeError("Task <Task-3> got Future attached to a different event loop")
            return True

        stale_setup_lock.acquire = fake_setup_acquire
        stale_setup_lock.locked.return_value = False
        radio._setup_lock = stale_setup_lock

        async with radio.setup_lock_context():
            pass
        assert radio._setup_lock is not stale_setup_lock
