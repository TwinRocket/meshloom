"""F2 telemetry alerts: thresholds, misses, busy lock, untracked."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import CONTACT_TYPE_REPEATER, TelemetryAlertRules
from app.radio import RadioOperationBusyError
from app.repository import ContactRepository
from app.repository.settings import AppSettingsRepository
from app.repository.telemetry_alert_state import TelemetryAlertStateRepository
from app.telemetry_alerts import (
    RULE_BATTERY,
    RULE_GPS_LOST,
    RULE_SILENCE,
    battery_volts_from_status,
    note_telemetry_poll,
)

KEY = "aa" * 32
SENSOR_KEY = "bb" * 32


async def _insert_contact(
    public_key: str, *, contact_type: int = CONTACT_TYPE_REPEATER, name="Node"
):
    await ContactRepository.upsert(
        {
            "public_key": public_key,
            "name": name,
            "type": contact_type,
            "flags": 0,
            "direct_path": None,
            "direct_path_len": -1,
            "direct_path_hash_mode": -1,
            "last_advert": None,
            "lat": None,
            "lon": None,
            "last_seen": None,
            "on_radio": False,
            "last_contacted": None,
            "first_seen": None,
        }
    )


async def _track_repeater(public_key: str = KEY) -> None:
    await _insert_contact(public_key, contact_type=CONTACT_TYPE_REPEATER, name="Rpt")
    await AppSettingsRepository.update(tracked_telemetry_repeaters=[public_key])


async def _track_sensor(public_key: str = SENSOR_KEY) -> None:
    await _insert_contact(public_key, contact_type=4, name="Sens")
    await AppSettingsRepository.update(tracked_telemetry_contacts=[public_key])


@pytest.fixture
def captured_alerts(monkeypatch):
    sent: list[dict] = []

    async def _capture(data: dict) -> None:
        sent.append(data)

    monkeypatch.setattr("app.push.manager.push_manager.dispatch_event", _capture)
    return sent


class TestBatteryVoltsHelper:
    def test_missing_bat_is_none(self):
        assert battery_volts_from_status({}) is None
        assert battery_volts_from_status({"noise_floor": -110}) is None
        assert battery_volts_from_status(None) is None

    def test_non_numeric_bat_is_none(self):
        assert battery_volts_from_status({"bat": "full"}) is None
        assert battery_volts_from_status({"bat": None}) is None

    def test_present_bat_is_millivolts(self):
        assert battery_volts_from_status({"bat": 3500}) == 3.5
        assert battery_volts_from_status({"bat": 0}) == 0.0


class TestTelemetryAlerts:
    @pytest.mark.asyncio
    async def test_null_or_zero_voltage_is_not_low_battery(self, test_db, captured_alerts):
        await _track_repeater()
        await note_telemetry_poll(
            public_key=KEY,
            name="Rpt",
            outcome="success",
            snapshot={"battery_volts": None},
        )
        await note_telemetry_poll(
            public_key=KEY,
            name="Rpt",
            outcome="success",
            snapshot={"battery_volts": 0},
        )
        await note_telemetry_poll(
            public_key=KEY,
            name="Rpt",
            outcome="success",
            snapshot={},
        )
        assert captured_alerts == []

    @pytest.mark.asyncio
    async def test_voltage_under_threshold_alerts_on_that_poll(self, test_db, captured_alerts):
        await _track_repeater()
        await note_telemetry_poll(
            public_key=KEY,
            name="Rpt",
            outcome="success",
            snapshot={"battery_volts": 3.2},
        )
        assert len(captured_alerts) == 1
        assert captured_alerts[0]["event"] == "telemetry_alert"
        assert captured_alerts[0]["rule_id"] == RULE_BATTERY
        assert captured_alerts[0]["value"] == 3.2

    @pytest.mark.asyncio
    async def test_one_miss_then_success_is_not_silence(self, test_db, captured_alerts):
        await _track_repeater()
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        await note_telemetry_poll(
            public_key=KEY,
            name="Rpt",
            outcome="success",
            snapshot={"battery_volts": 4.1},
        )
        assert captured_alerts == []
        state = await TelemetryAlertStateRepository.get(KEY, RULE_SILENCE)
        assert state is not None
        assert state["consecutive_misses"] == 0

    @pytest.mark.asyncio
    async def test_two_misses_default_fires_silence(self, test_db, captured_alerts):
        await _track_repeater()
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        assert captured_alerts == []
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        assert len(captured_alerts) == 1
        assert captured_alerts[0]["rule_id"] == RULE_SILENCE
        assert captured_alerts[0]["value"] == 2

    @pytest.mark.asyncio
    async def test_misses_before_alert_3_waits_for_third(self, test_db, captured_alerts):
        await _track_repeater()
        await AppSettingsRepository.update(
            telemetry_alert_rules=TelemetryAlertRules(misses_before_alert=3)
        )
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        assert captured_alerts == []
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        assert len(captured_alerts) == 1
        assert captured_alerts[0]["rule_id"] == RULE_SILENCE
        assert captured_alerts[0]["value"] == 3

    @pytest.mark.asyncio
    async def test_busy_lock_is_not_a_miss(self, test_db, captured_alerts):
        await _track_repeater()
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="busy")
        assert captured_alerts == []
        assert await TelemetryAlertStateRepository.get(KEY, RULE_SILENCE) is None
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        assert captured_alerts == []
        state = await TelemetryAlertStateRepository.get(KEY, RULE_SILENCE)
        assert state is not None
        assert state["consecutive_misses"] == 1

    @pytest.mark.asyncio
    async def test_untracked_is_ignored(self, test_db, captured_alerts):
        await _insert_contact(KEY, contact_type=CONTACT_TYPE_REPEATER, name="Rpt")
        await note_telemetry_poll(
            public_key=KEY,
            name="Rpt",
            outcome="success",
            snapshot={"battery_volts": 3.0},
        )
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        await note_telemetry_poll(public_key=KEY, name="Rpt", outcome="miss")
        assert captured_alerts == []
        assert await TelemetryAlertStateRepository.get(KEY, RULE_BATTERY) is None
        assert await TelemetryAlertStateRepository.get(KEY, RULE_SILENCE) is None

    @pytest.mark.asyncio
    async def test_battery_hysteresis_does_not_refire_until_rearm(self, test_db, captured_alerts):
        await _track_repeater()
        await note_telemetry_poll(
            public_key=KEY, name="Rpt", outcome="success", snapshot={"battery_volts": 3.2}
        )
        await note_telemetry_poll(
            public_key=KEY, name="Rpt", outcome="success", snapshot={"battery_volts": 3.6}
        )
        assert len(captured_alerts) == 1
        await note_telemetry_poll(
            public_key=KEY, name="Rpt", outcome="success", snapshot={"battery_volts": 3.8}
        )
        assert len(captured_alerts) == 1
        await note_telemetry_poll(
            public_key=KEY, name="Rpt", outcome="success", snapshot={"battery_volts": 3.2}
        )
        assert len(captured_alerts) == 2

    @pytest.mark.asyncio
    async def test_gps_lost_only_after_previous_fix(self, test_db, captured_alerts):
        await _track_sensor()
        await note_telemetry_poll(
            public_key=SENSOR_KEY,
            name="Sens",
            outcome="success",
            snapshot={"lpp_sensors": [{"channel": 1, "type_name": "temperature", "value": 21}]},
            allow_gps_lost=True,
        )
        assert captured_alerts == []
        await note_telemetry_poll(
            public_key=SENSOR_KEY,
            name="Sens",
            outcome="success",
            snapshot={
                "lpp_sensors": [
                    {
                        "channel": 1,
                        "type_name": "gps",
                        "value": {"latitude": 45.1, "longitude": 5.7},
                    }
                ]
            },
            allow_gps_lost=True,
        )
        assert captured_alerts == []
        await note_telemetry_poll(
            public_key=SENSOR_KEY,
            name="Sens",
            outcome="success",
            snapshot={"lpp_sensors": [{"channel": 1, "type_name": "temperature", "value": 21}]},
            allow_gps_lost=True,
        )
        assert [item["rule_id"] for item in captured_alerts] == [RULE_GPS_LOST]

    @pytest.mark.asyncio
    async def test_miss_is_not_gps_lost(self, test_db, captured_alerts):
        await _track_sensor()
        await note_telemetry_poll(
            public_key=SENSOR_KEY,
            name="Sens",
            outcome="success",
            snapshot={
                "lpp_sensors": [
                    {
                        "channel": 1,
                        "type_name": "gps",
                        "value": {"latitude": 45.1, "longitude": 5.7},
                    }
                ]
            },
            allow_gps_lost=True,
        )
        await note_telemetry_poll(
            public_key=SENSOR_KEY,
            name="Sens",
            outcome="miss",
            allow_gps_lost=True,
        )
        assert captured_alerts == []

    @pytest.mark.asyncio
    async def test_repeater_lpp_does_not_invent_gps_lost(self, test_db, captured_alerts):
        await _track_repeater()
        await note_telemetry_poll(
            public_key=KEY,
            name="Rpt",
            outcome="success",
            snapshot={
                "lpp_sensors": [
                    {
                        "channel": 1,
                        "type_name": "gps",
                        "value": {"latitude": 45.1, "longitude": 5.7},
                    }
                ]
            },
            allow_gps_lost=False,
        )
        await note_telemetry_poll(
            public_key=KEY,
            name="Rpt",
            outcome="success",
            snapshot={"lpp_sensors": []},
            allow_gps_lost=False,
        )
        assert captured_alerts == []
        assert await TelemetryAlertStateRepository.get(KEY, RULE_GPS_LOST) is None


class TestCollectEmptyStatus:
    @pytest.mark.asyncio
    async def test_empty_status_is_miss_and_not_persisted(self):
        from app.radio_sync import _collect_repeater_telemetry

        mc = MagicMock()
        mc.commands.add_contact = AsyncMock()
        mc.commands.req_status_sync = AsyncMock(return_value={})
        contact = MagicMock()
        contact.public_key = KEY
        contact.name = "Rpt"
        contact.to_radio_dict.return_value = {}
        recorded = []

        async def mock_record(public_key, timestamp, data):
            recorded.append(data)

        with (
            patch(
                "app.radio_sync.RepeaterTelemetryRepository.record",
                new_callable=AsyncMock,
                side_effect=mock_record,
            ),
            patch("app.radio_sync.note_telemetry_poll", new_callable=AsyncMock) as note,
        ):
            result = await _collect_repeater_telemetry(mc, contact)

        assert result is False
        assert recorded == []
        note.assert_awaited_once()
        assert note.await_args.kwargs["outcome"] == "miss"


class TestCycleBusyIsNotMiss:
    @pytest.mark.asyncio
    async def test_busy_lock_skips_without_noting_miss(self, test_db):
        from app.radio_sync import _run_telemetry_cycle

        await _track_repeater()

        fake_radio_manager = MagicMock()
        fake_radio_manager.is_connected = True
        fake_radio_manager.radio_operation.side_effect = RadioOperationBusyError("busy")

        with (
            patch("app.radio_sync.radio_manager", fake_radio_manager),
            patch("app.radio_sync.note_telemetry_poll", new_callable=AsyncMock) as note,
        ):
            await _run_telemetry_cycle()

        note.assert_not_called()
        assert await TelemetryAlertStateRepository.get(KEY, RULE_SILENCE) is None
