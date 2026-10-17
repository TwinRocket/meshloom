"""Radio clock stuck in the future: corrective reboot, then the clock wraparound.

``FakeClockRadio`` emulates the MeshCore companion firmware clock: a uint32 epoch
that runs with a controlled clock, ``set_time(t)`` accepted only when
``t >= current``, and a reboot that resets a volatile clock or keeps a
persistent (battery-backed) one. Each ``FakeConnection`` is a new ``MeshCore``
object on the same radio, as after a reconnect.
"""

import logging
import types
from unittest.mock import MagicMock, patch

import pytest
from meshcore import EventType
from meshcore.events import Event

from app import radio_sync, radio_workarounds
from app.radio_sync import log_radio_clock, sync_radio_time, sync_radio_time_outcome
from app.radio_workarounds import RadioWorkarounds

UINT32_MAX = 0xFFFFFFFF
HOST_NOW = 1_790_000_000  # 2026-09-21
RADIO_2082 = 3_550_000_000  # 2082-06-29
KEY_A = "aa" * 32
KEY_B = "bb" * 32


class FakeClock:
    def __init__(self, t: float) -> None:
        self.t = t

    def time(self) -> float:
        return self.t

    def monotonic(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        self.t += seconds


class FakeClockRadio:
    def __init__(
        self,
        clock: FakeClock,
        radio_time: int,
        *,
        public_key: str = KEY_A,
        persistent: bool = True,
        boot_epoch: int = 1_715_770_351,  # firmware default epoch (2024)
        wraps: bool = True,
        reject_wrap_target: bool = False,
        ignore_set_after_wrap: bool = False,
    ) -> None:
        self.clock = clock
        self.public_key = public_key
        self.persistent = persistent
        self.boot_epoch = boot_epoch
        self.wraps = wraps
        self.reject_wrap_target = reject_wrap_target
        self.ignore_set_after_wrap = ignore_set_after_wrap
        self.writes: list[int] = []
        self.reboots = 0
        self._wrapped = False
        self._set(radio_time)

    def _set(self, epoch: int) -> None:
        self._base = epoch
        self._at = self.clock.t

    def now(self) -> int:
        value = self._base + int(self.clock.t - self._at)
        if value > UINT32_MAX:
            if not self.wraps:
                return UINT32_MAX
            self._wrapped = True
        return value % (UINT32_MAX + 1)

    async def get_time(self) -> Event:
        return Event(EventType.CURRENT_TIME, {"time": self.now()})

    async def set_time(self, epoch: int) -> Event:
        self.writes.append(epoch)
        if epoch == UINT32_MAX and self.reject_wrap_target:
            return Event(EventType.ERROR, {"reason": "illegal_arg"})
        if epoch < self.now():
            return Event(EventType.ERROR, {"reason": "illegal_arg"})
        if self.ignore_set_after_wrap and self._wrapped:
            return Event(EventType.OK, {})
        self._set(epoch)
        return Event(EventType.OK, {})

    async def reboot(self) -> None:
        self.reboots += 1
        if not self.persistent:
            self._set(self.boot_epoch)


class FakeConnection:
    """A new MeshCore object per connection, on the same radio."""

    def __init__(self, radio: FakeClockRadio) -> None:
        self.commands = radio
        self.self_info = {"public_key": radio.public_key}


@pytest.fixture
def clock():
    clock = FakeClock(HOST_NOW)
    fake_time = types.SimpleNamespace(time=clock.time, monotonic=clock.monotonic)
    fake_asyncio = types.SimpleNamespace(sleep=clock.sleep)
    radio_sync.reset_clock_sync_guards()
    radio_workarounds.reset()
    with (
        patch.object(radio_sync, "time", fake_time),
        patch.object(radio_sync, "asyncio", fake_asyncio),
        patch.object(radio_sync, "radio_manager", MagicMock()),
    ):
        yield clock
    radio_sync.reset_clock_sync_guards()
    radio_workarounds.reset()


_connections: list[FakeConnection] = []  # keeps ids unique across a test


async def connect(radio: FakeClockRadio, *, setup: bool = True) -> tuple[FakeConnection, str]:
    """One connection: the post-connect setup sync (or a periodic sync)."""
    mc = FakeConnection(radio)
    _connections.append(mc)
    outcome = await sync_radio_time_outcome(mc, allow_wraparound=setup)  # type: ignore[arg-type]
    return mc, outcome


def wrap_writes(radio: FakeClockRadio) -> int:
    return radio.writes.count(UINT32_MAX)


@pytest.mark.asyncio
async def test_first_connection_reboots_without_wraparound(clock):
    radio = FakeClockRadio(clock, RADIO_2082)
    _, outcome = await connect(radio)
    assert outcome == "rebooted"
    assert radio.reboots == 1
    assert wrap_writes(radio) == 0


@pytest.mark.asyncio
async def test_volatile_clock_is_fixed_by_the_reboot(clock):
    radio = FakeClockRadio(clock, RADIO_2082, persistent=False)
    assert (await connect(radio))[1] == "rebooted"
    assert (await connect(radio))[1] == "synced"
    assert wrap_writes(radio) == 0
    assert radio.now() == HOST_NOW


@pytest.mark.asyncio
async def test_persistent_clock_is_fixed_by_wraparound_after_the_reboot(clock, caplog):
    radio = FakeClockRadio(clock, RADIO_2082)
    assert (await connect(radio))[1] == "rebooted"
    with caplog.at_level(logging.INFO, logger="app.radio_sync"):
        assert (await connect(radio))[1] == "synced"
    assert wrap_writes(radio) == 1
    assert radio.writes[-2] == UINT32_MAX
    assert abs(radio.now() - int(clock.t)) <= 1
    assert "Radio clock still ahead after reboot" in caplog.text
    assert "Radio clock fixed by wraparound" in caplog.text
    # The clock is fine now: the next connection is a plain sync.
    assert (await connect(radio))[1] == "synced"
    assert wrap_writes(radio) == 1


@pytest.mark.asyncio
async def test_setting_off_never_writes_the_wrap_target(clock):
    radio_workarounds._current = RadioWorkarounds(auto_clock_wraparound=False)
    radio = FakeClockRadio(clock, RADIO_2082)
    assert (await connect(radio))[1] == "rebooted"
    assert (await connect(radio))[1] == "rejected"
    assert wrap_writes(radio) == 0


@pytest.mark.asyncio
async def test_periodic_sync_never_applies_the_wraparound(clock):
    radio = FakeClockRadio(clock, RADIO_2082)
    # The periodic sync may send the first reboot, as before...
    assert (await connect(radio, setup=False))[1] == "rebooted"
    # ...but never the wraparound, even after the reboot.
    assert (await connect(radio, setup=False))[1] == "rejected"
    mc = FakeConnection(radio)
    assert await sync_radio_time(mc) is False  # type: ignore[arg-type]
    assert wrap_writes(radio) == 0
    # The next setup gets its chance.
    assert (await connect(radio))[1] == "synced"


@pytest.mark.asyncio
async def test_at_most_once_per_connection(clock):
    radio = FakeClockRadio(clock, RADIO_2082, reject_wrap_target=True)
    await connect(radio)  # reboot
    mc, outcome = await connect(radio)
    assert outcome == "rejected"
    assert await sync_radio_time_outcome(mc, allow_wraparound=True) == "rejected"  # type: ignore[arg-type]
    assert wrap_writes(radio) == 1


@pytest.mark.asyncio
async def test_a_failed_wraparound_is_not_retried_on_reconnect(clock):
    radio = FakeClockRadio(clock, RADIO_2082, reject_wrap_target=True)
    await connect(radio)  # reboot
    assert (await connect(radio))[1] == "rejected"
    assert (await connect(radio))[1] == "rejected"
    assert wrap_writes(radio) == 1
    assert radio.reboots == 1


@pytest.mark.asyncio
async def test_another_radio_gets_its_own_reboot_first(clock):
    radio_a = FakeClockRadio(clock, RADIO_2082, public_key=KEY_A, reject_wrap_target=True)
    await connect(radio_a)
    await connect(radio_a)
    radio_b = FakeClockRadio(clock, RADIO_2082, public_key=KEY_B)
    assert (await connect(radio_b))[1] == "rebooted"
    assert wrap_writes(radio_b) == 0
    assert (await connect(radio_b))[1] == "synced"


@pytest.mark.asyncio
async def test_radio_that_never_wraps_is_left_alone(clock, caplog):
    radio = FakeClockRadio(clock, RADIO_2082, wraps=False)
    await connect(radio)  # reboot
    with caplog.at_level(logging.ERROR, logger="app.radio_sync"):
        assert (await connect(radio))[1] == "rejected"
    assert "did not wrap its clock" in caplog.text
    assert radio.writes[-1] == UINT32_MAX  # no write after the target
    assert (await connect(radio))[1] == "rejected"
    assert wrap_writes(radio) == 1


@pytest.mark.asyncio
async def test_verification_mismatch_is_a_failure(clock, caplog):
    radio = FakeClockRadio(clock, RADIO_2082, ignore_set_after_wrap=True)
    await connect(radio)  # reboot
    with caplog.at_level(logging.WARNING, logger="app.radio_sync"):
        assert (await connect(radio))[1] == "rejected"
    assert "was accepted but the radio now reads" in caplog.text
    assert "Radio clock fixed by wraparound" not in caplog.text


@pytest.mark.asyncio
async def test_rejected_wrap_target_writes_nothing_else(clock):
    radio = FakeClockRadio(clock, RADIO_2082, reject_wrap_target=True)
    await connect(radio)  # reboot
    before = len(radio.writes)
    assert (await connect(radio))[1] == "rejected"
    # The normal set_time, then the wrap target; nothing after it.
    assert radio.writes[before:] == [HOST_NOW, UINT32_MAX]


@pytest.mark.asyncio
async def test_an_exception_mid_wraparound_latches_the_radio(clock):
    radio = FakeClockRadio(clock, RADIO_2082)
    await connect(radio)  # reboot
    real_set_time = radio.set_time

    async def set_time_then_drop(epoch: int) -> Event:
        if epoch == UINT32_MAX:
            radio.writes.append(epoch)
            raise ConnectionError("link lost")
        return await real_set_time(epoch)

    radio.set_time = set_time_then_drop  # type: ignore[method-assign]
    assert (await connect(radio))[1] == "rejected"
    assert (await connect(radio))[1] == "rejected"
    assert wrap_writes(radio) == 1


@pytest.mark.asyncio
async def test_radio_already_at_the_edge_is_not_set_again(clock):
    radio = FakeClockRadio(clock, UINT32_MAX - 1)
    await connect(radio)  # reboot (persistent: still near the edge)
    assert (await connect(radio))[1] == "synced"
    assert wrap_writes(radio) == 0


@pytest.mark.asyncio
async def test_small_skew_does_nothing(clock):
    radio = FakeClockRadio(clock, HOST_NOW + 20)
    assert (await connect(radio))[1] == "rejected"
    assert (await connect(radio))[1] == "rejected"
    assert radio.reboots == 0
    assert wrap_writes(radio) == 0


@pytest.mark.asyncio
async def test_wrong_host_clock_neither_reboots_nor_wraps(clock, caplog):
    clock.t = 0  # a board booted without RTC or NTP
    radio = FakeClockRadio(clock, HOST_NOW)
    with caplog.at_level(logging.WARNING, logger="app.radio_sync"):
        assert (await connect(radio))[1] == "rejected"
        assert (await connect(radio))[1] == "rejected"
    assert radio.reboots == 0
    assert wrap_writes(radio) == 0
    assert "System clock looks wrong" in caplog.text


@pytest.mark.asyncio
async def test_log_radio_clock_reports_the_faulty_value(clock, caplog):
    radio = FakeClockRadio(clock, RADIO_2082)
    with caplog.at_level(logging.INFO, logger="app.radio_sync"):
        value = await log_radio_clock(FakeConnection(radio), "Radio clock at connect")  # type: ignore[arg-type]
    assert value == RADIO_2082
    assert "Radio clock at connect: epoch=3550000000 utc=2082-06-29" in caplog.text
    assert f"delta=+{RADIO_2082 - HOST_NOW}s" in caplog.text


def test_format_epoch_handles_the_whole_uint32_range():
    assert radio_sync.format_epoch(0) == "1970-01-01 00:00:00 UTC"
    assert radio_sync.format_epoch(UINT32_MAX) == "2106-02-07 06:28:15 UTC"


@pytest.mark.asyncio
async def test_host_clock_behind_never_rolls_back_a_correct_radio(clock, caplog):
    # Offline Pi: fake-hwclock restored a date 46 days old, no NTP yet. The radio
    # (battery RTC or GPS) has the right time and reads ahead of the host.
    radio = FakeClockRadio(clock, HOST_NOW + 46 * 86400)
    assert (await connect(radio))[1] == "rebooted"  # unchanged: one reboot
    with caplog.at_level(logging.WARNING, logger="app.radio_sync"):
        assert (await connect(radio))[1] == "rejected"
        assert (await connect(radio))[1] == "rejected"
    assert wrap_writes(radio) == 0
    assert radio.reboots == 1
    assert "no clock wraparound" in caplog.text


@pytest.mark.asyncio
async def test_a_fixed_radio_is_not_wrapped_again_when_it_goes_back_ahead(clock):
    radio = FakeClockRadio(clock, RADIO_2082)
    await connect(radio)  # reboot
    assert (await connect(radio))[1] == "synced"
    assert wrap_writes(radio) == 1
    radio._set(RADIO_2082)  # GPS or RTC puts the radio far ahead again
    assert (await connect(radio))[1] == "rejected"
    assert (await connect(radio))[1] == "rejected"
    assert wrap_writes(radio) == 1
    assert radio.reboots == 1


@pytest.mark.asyncio
async def test_a_reboot_that_raised_does_not_count_as_tried(clock):
    radio = FakeClockRadio(clock, RADIO_2082)
    real_reboot = radio.reboot

    async def reboot_fails() -> None:
        raise ConnectionError("transport hiccup")

    radio.reboot = reboot_fails  # type: ignore[method-assign]
    assert (await connect(radio))[1] == "rejected"
    radio.reboot = real_reboot  # type: ignore[method-assign]
    # The next setup reboots instead of going straight to the wraparound.
    assert (await connect(radio))[1] == "rebooted"
    assert wrap_writes(radio) == 0
    assert radio.reboots == 1


@pytest.mark.asyncio
async def test_radio_without_public_key_never_gets_the_wraparound(clock, caplog):
    radio = FakeClockRadio(clock, RADIO_2082)

    async def connect_without_self_info() -> str:
        mc = FakeConnection(radio)
        mc.self_info = None  # type: ignore[assignment]
        _connections.append(mc)
        return await sync_radio_time_outcome(mc, allow_wraparound=True)  # type: ignore[arg-type]

    assert await connect_without_self_info() == "rebooted"
    with caplog.at_level(logging.WARNING, logger="app.radio_sync"):
        assert await connect_without_self_info() == "rejected"
    assert wrap_writes(radio) == 0
    assert "public key is unknown" in caplog.text
