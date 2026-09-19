"""Tests for settings router endpoints and validation behavior."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from meshcore import EventType

from app.models import (
    CONTACT_TYPE_REPEATER,
    AppSettings,
    ContactUpsert,
    TelemetryAlertRuleOverride,
    TelemetryAlertRules,
)
from app.repository import AppSettingsRepository, ContactRepository
from app.routers.settings import (
    AppSettingsUpdate,
    FavoriteRequest,
    TrackedTelemetryRequest,
    get_telemetry_schedule,
    toggle_favorite,
    toggle_tracked_telemetry,
    update_settings,
)
from app.services.flood_scope import FORCE_UNSCOPED_FRAME


class TestUpdateSettings:
    @pytest.mark.asyncio
    async def test_forwards_only_provided_fields(self, test_db):
        result = await update_settings(
            AppSettingsUpdate(
                max_radio_contacts=321,
                advert_interval=3600,
            )
        )

        assert result.max_radio_contacts == 321
        assert result.advert_interval == 3600

    @pytest.mark.asyncio
    async def test_advert_interval_below_minimum_is_clamped_to_one_hour(self, test_db):
        result = await update_settings(AppSettingsUpdate(advert_interval=600))
        assert result.advert_interval == 3600

    @pytest.mark.asyncio
    async def test_advert_interval_zero_stays_disabled(self, test_db):
        result = await update_settings(AppSettingsUpdate(advert_interval=0))
        assert result.advert_interval == 0

    @pytest.mark.asyncio
    async def test_advert_interval_above_minimum_is_preserved(self, test_db):
        result = await update_settings(AppSettingsUpdate(advert_interval=86400))
        assert result.advert_interval == 86400

    @pytest.mark.asyncio
    async def test_empty_patch_returns_current_settings(self, test_db):
        result = await update_settings(AppSettingsUpdate())

        # Should return default settings without error
        assert isinstance(result, AppSettings)
        assert result.max_radio_contacts == 200  # default

    @pytest.mark.asyncio
    async def test_directory_available_when_community_on(self, test_db):
        from app.routers.settings import get_settings
        from app.services.meshloom_community import update_community

        await update_community(enabled=True, iata="LYS")
        result = await get_settings()
        assert result.directory_available is True

    @pytest.mark.asyncio
    async def test_directory_available_false_by_default(self, test_db):
        from app.routers.settings import get_settings

        result = await get_settings()
        assert result.directory_available is False

    @pytest.mark.asyncio
    async def test_flood_scope_round_trip(self, test_db):
        """Flood scope should be saved and retrieved correctly."""
        result = await update_settings(AppSettingsUpdate(flood_scope="MyRegion"))
        assert result.flood_scope == "#MyRegion"

        fresh = await AppSettingsRepository.get()
        assert fresh.flood_scope == "#MyRegion"

    @pytest.mark.asyncio
    async def test_flood_scope_default_empty(self, test_db):
        """Fresh DB should have flood_scope as empty string."""
        settings = await AppSettingsRepository.get()
        assert settings.flood_scope == ""

    @pytest.mark.asyncio
    async def test_flood_scope_whitespace_stripped(self, test_db):
        """Flood scope should be stripped of whitespace."""
        result = await update_settings(AppSettingsUpdate(flood_scope="  MyRegion  "))
        assert result.flood_scope == "#MyRegion"

    @pytest.mark.asyncio
    async def test_flood_scope_existing_hash_is_not_doubled(self, test_db):
        """Existing leading hash should be preserved for backward compatibility."""
        result = await update_settings(AppSettingsUpdate(flood_scope="#MyRegion"))
        assert result.flood_scope == "#MyRegion"

    @pytest.mark.asyncio
    async def test_known_regions_round_trip(self, test_db):
        """Known regions should be saved and retrieved as a clean list."""
        result = await update_settings(AppSettingsUpdate(known_regions=["nl-gr", "de-by"]))
        assert result.known_regions == ["nl-gr", "de-by"]

        fresh = await AppSettingsRepository.get()
        assert fresh.known_regions == ["nl-gr", "de-by"]

    @pytest.mark.asyncio
    async def test_known_regions_default_empty(self, test_db):
        """Fresh DB should have known_regions as an empty list."""
        settings = await AppSettingsRepository.get()
        assert settings.known_regions == []

    @pytest.mark.asyncio
    async def test_known_regions_cleaned_and_deduped(self, test_db):
        """Leading hashes, whitespace, blanks, and case-insensitive dupes are normalized."""
        result = await update_settings(
            AppSettingsUpdate(known_regions=["  #nl-gr ", "", "nl-gr", "DE-BY", "de-by"])
        )
        assert result.known_regions == ["nl-gr", "DE-BY"]

    @pytest.mark.asyncio
    async def test_known_regions_change_triggers_backfill(self, test_db):
        """Changing the region list schedules a background message re-tag."""
        import asyncio

        with patch(
            "app.services.messages.backfill_message_regions", new_callable=AsyncMock
        ) as mock_backfill:
            await update_settings(AppSettingsUpdate(known_regions=["nl-gr", "de-by"]))
            await asyncio.sleep(0)  # let the scheduled task run

        mock_backfill.assert_awaited_once_with(["nl-gr", "de-by"])

    @pytest.mark.asyncio
    async def test_known_regions_unchanged_skips_backfill(self, test_db):
        """Saving the same region list does not re-run the backfill."""
        import asyncio

        await update_settings(AppSettingsUpdate(known_regions=["nl-gr"]))
        with patch(
            "app.services.messages.backfill_message_regions", new_callable=AsyncMock
        ) as mock_backfill:
            await update_settings(AppSettingsUpdate(known_regions=["#nl-gr"]))  # same after cleanup
            await asyncio.sleep(0)

        mock_backfill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_flood_scope_applies_to_radio(self, test_db):
        """When radio is connected, setting flood_scope calls set_flood_scope on radio."""
        mock_mc = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()

        mock_rm = AsyncMock()
        mock_rm.is_connected = True
        mock_rm.meshcore = mock_mc
        mock_rm.firmware_ver_code = 13  # supports mode-1 unscoped

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_radio_op(name):
            yield mock_mc

        mock_rm.radio_operation = mock_radio_op

        with patch("app.radio.radio_manager", mock_rm):
            await update_settings(AppSettingsUpdate(flood_scope="TestRegion"))

        mock_mc.commands.set_flood_scope.assert_awaited_once_with("#TestRegion")

    @pytest.mark.asyncio
    async def test_flood_scope_empty_resets_radio(self, test_db):
        """Setting flood_scope to empty calls set_flood_scope("") on radio."""
        # First set a non-empty scope
        await update_settings(AppSettingsUpdate(flood_scope="#TestRegion"))

        mock_mc = AsyncMock()
        mock_mc.commands.set_flood_scope = AsyncMock()

        mock_rm = AsyncMock()
        mock_rm.is_connected = True
        mock_rm.meshcore = mock_mc
        mock_rm.firmware_ver_code = 13  # supports mode-1 unscoped

        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def mock_radio_op(name):
            yield mock_mc

        mock_rm.radio_operation = mock_radio_op

        with patch("app.radio.radio_manager", mock_rm):
            await update_settings(AppSettingsUpdate(flood_scope=""))

        mock_mc.commands.send.assert_awaited_once_with(
            FORCE_UNSCOPED_FRAME, [EventType.OK, EventType.ERROR]
        )
        mock_mc.commands.set_flood_scope.assert_not_awaited()


class TestToggleFavorite:
    @pytest.mark.asyncio
    async def test_adds_when_not_favorited(self, test_db):
        await ContactRepository.upsert(ContactUpsert(public_key="aa" * 32, name="Alice"))
        request = FavoriteRequest(type="contact", id="aa" * 32)
        with (
            patch("app.radio_sync.ensure_contact_on_radio", new_callable=AsyncMock) as mock_sync,
            patch("app.routers.settings.asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = lambda coro: coro.close()
            result = await toggle_favorite(request)

        assert result.favorite is True
        assert result.type == "contact"
        assert result.id == "aa" * 32
        mock_sync.assert_called_once_with("aa" * 32, force=True)
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_removes_when_already_favorited(self, test_db):
        await ContactRepository.upsert(ContactUpsert(public_key="aa" * 32, name="Alice"))
        await ContactRepository.set_favorite("aa" * 32, True)

        request = FavoriteRequest(type="contact", id="aa" * 32)
        with (
            patch("app.radio_sync.ensure_contact_on_radio", new_callable=AsyncMock) as mock_sync,
            patch("app.routers.settings.asyncio.create_task") as mock_create_task,
        ):
            mock_create_task.side_effect = lambda coro: coro.close()
            result = await toggle_favorite(request)

        assert result.favorite is False
        mock_sync.assert_not_called()
        mock_create_task.assert_not_called()


class TestMuteChannel:
    @pytest.mark.asyncio
    async def test_duration_mutes_until_timestamp(self, test_db):
        from app.repository import ChannelRepository
        from app.routers.settings import MuteChannelRequest, toggle_muted_channel

        key = "ab" * 16
        await ChannelRepository.upsert(key, "#ops", is_hashtag=True)
        result = await toggle_muted_channel(MuteChannelRequest(key=key, duration_seconds=900))
        assert result.muted is True
        assert result.muted_until is not None
        stored = await ChannelRepository.get_by_key(key)
        assert stored is not None
        assert stored.muted is True
        assert stored.muted_until == result.muted_until

    @pytest.mark.asyncio
    async def test_zero_unmutes(self, test_db):
        from app.repository import ChannelRepository
        from app.routers.settings import MuteChannelRequest, toggle_muted_channel

        key = "cd" * 16
        await ChannelRepository.upsert(key, "#ops", is_hashtag=True)
        await ChannelRepository.set_muted(key, True)
        result = await toggle_muted_channel(MuteChannelRequest(key=key, duration_seconds=0))
        assert result.muted is False
        assert result.muted_until is None

    @pytest.mark.asyncio
    async def test_expired_mute_is_not_effective(self, test_db):
        import time

        from app.repository import ChannelRepository

        key = "ef" * 16
        await ChannelRepository.upsert(key, "#ops", is_hashtag=True)
        await ChannelRepository.set_muted(key, True, int(time.time()) - 30)
        stored = await ChannelRepository.get_by_key(key)
        assert stored is not None
        assert stored.muted is False
        assert stored.muted_until is None


class TestToggleTrackedTelemetry:
    """Tests for POST /settings/tracked-telemetry/toggle."""

    async def _create_repeater(self, key: str, name: str = "TestRepeater") -> None:
        await ContactRepository.upsert(
            ContactUpsert(public_key=key, name=name, type=CONTACT_TYPE_REPEATER)
        )

    @pytest.mark.asyncio
    async def test_add_repeater_to_tracking(self, test_db):
        key = "aa" * 32
        await self._create_repeater(key)

        result = await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key=key))

        assert key in result.tracked_telemetry_repeaters
        assert result.names[key] == "TestRepeater"

        # Verify persisted
        settings = await AppSettingsRepository.get()
        assert key in settings.tracked_telemetry_repeaters

    @pytest.mark.asyncio
    async def test_remove_repeater_from_tracking(self, test_db):
        key = "bb" * 32
        await self._create_repeater(key)
        await AppSettingsRepository.update(tracked_telemetry_repeaters=[key])

        result = await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key=key))

        assert key not in result.tracked_telemetry_repeaters

    @pytest.mark.asyncio
    async def test_rejects_non_repeater_contact(self, test_db):
        key = "cc" * 32
        await ContactRepository.upsert(ContactUpsert(public_key=key, name="Client", type=1))

        with pytest.raises(HTTPException) as exc_info:
            await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key=key))
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_rejects_unknown_contact(self, test_db):
        with pytest.raises(HTTPException) as exc_info:
            await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key="dd" * 32))
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_rejects_when_limit_reached(self, test_db):
        existing_keys = []
        for i in range(8):
            key = f"{i:02x}" * 32
            await self._create_repeater(key, name=f"Repeater{i}")
            existing_keys.append(key)
        await AppSettingsRepository.update(tracked_telemetry_repeaters=existing_keys)

        new_key = "ff" * 32
        await self._create_repeater(new_key, name="NewRepeater")

        with pytest.raises(HTTPException) as exc_info:
            await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key=new_key))
        assert exc_info.value.status_code == 409
        detail = exc_info.value.detail
        assert len(detail["tracked_telemetry_repeaters"]) == 8

    @pytest.mark.asyncio
    async def test_remove_still_works_when_limit_reached(self, test_db):
        """Toggling OFF an already-tracked repeater should work even at max capacity."""
        keys = []
        for i in range(8):
            key = f"{i:02x}" * 32
            await self._create_repeater(key)
            keys.append(key)
        await AppSettingsRepository.update(tracked_telemetry_repeaters=keys)

        result = await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key=keys[0]))
        assert keys[0] not in result.tracked_telemetry_repeaters
        assert len(result.tracked_telemetry_repeaters) == 7

    @pytest.mark.asyncio
    async def test_toggle_response_includes_schedule(self, test_db):
        """After toggle, response must carry the schedule derivation so the UI
        can update the interval dropdown without a follow-up fetch."""
        key = "aa" * 32
        await self._create_repeater(key)

        result = await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key=key))

        assert result.schedule.tracked_count == 1
        # N=1 unlocks the full menu including 1h
        assert 1 in result.schedule.options
        assert result.schedule.max_tracked == 8


class TestTelemetryIntervalValidation:
    """PATCH /settings validation for telemetry_interval_hours."""

    @pytest.mark.asyncio
    async def test_accepts_valid_interval(self, test_db):
        result = await update_settings(AppSettingsUpdate(telemetry_interval_hours=4))
        assert result.telemetry_interval_hours == 4

    @pytest.mark.asyncio
    async def test_invalid_interval_falls_back_to_default(self, test_db):
        """Non-menu values are defaulted rather than 400-ing to keep stale
        clients from getting stuck on a save error."""
        result = await update_settings(AppSettingsUpdate(telemetry_interval_hours=99))
        assert result.telemetry_interval_hours == 8  # DEFAULT_TELEMETRY_INTERVAL_HOURS

    @pytest.mark.asyncio
    async def test_preference_is_preserved_even_when_illegal_for_count(self, test_db):
        """User picks 1h at N=5 tracked: stored pref must stay 1h. Scheduler
        handles the clamping at run time; storage is verbatim."""
        # Seed 5 tracked repeaters
        keys = [f"{i:02x}" * 32 for i in range(5)]
        for k in keys:
            await ContactRepository.upsert(
                ContactUpsert(public_key=k, name=f"R{k[:4]}", type=CONTACT_TYPE_REPEATER)
            )
        await AppSettingsRepository.update(tracked_telemetry_repeaters=keys)

        result = await update_settings(AppSettingsUpdate(telemetry_interval_hours=1))
        assert result.telemetry_interval_hours == 1

        # But the GET schedule endpoint should report the clamped effective value.
        schedule = await get_telemetry_schedule()
        assert schedule.preferred_hours == 1
        assert schedule.effective_hours == 6  # N=5 -> shortest legal = 6h


class TestTelemetryScheduleEndpoint:
    """GET /settings/tracked-telemetry/schedule."""

    @pytest.mark.asyncio
    async def test_schedule_with_no_tracked_repeaters(self, test_db):
        """No tracked repeaters means nothing to schedule; next_run_at is None.

        At N=0 the clamp helper returns the default 8h, which is a fine
        display value for an empty state. Options start at 8h for the same
        reason — any lower shortest-legal only makes sense once the user
        has at least one repeater tracked.
        """
        schedule = await get_telemetry_schedule()

        assert schedule.tracked_count == 0
        assert schedule.next_run_at is None
        # At N=0 shortest-legal defaults to 8h.
        assert schedule.options == [8, 12, 24]

    @pytest.mark.asyncio
    async def test_schedule_filters_options_by_tracked_count(self, test_db):
        keys = [f"{i:02x}" * 32 for i in range(5)]
        for k in keys:
            await ContactRepository.upsert(
                ContactUpsert(public_key=k, name=f"R{k[:4]}", type=CONTACT_TYPE_REPEATER)
            )
        await AppSettingsRepository.update(tracked_telemetry_repeaters=keys)

        schedule = await get_telemetry_schedule()

        assert schedule.tracked_count == 5
        assert schedule.options == [6, 8, 12, 24]
        assert schedule.next_run_at is not None


class TestRoutedHourlySetting:
    """Tests for the telemetry_routed_hourly setting."""

    @pytest.mark.asyncio
    async def test_defaults_to_false(self, test_db):
        settings = await AppSettingsRepository.get()
        assert settings.telemetry_routed_hourly is False

    @pytest.mark.asyncio
    async def test_round_trip_via_patch(self, test_db):
        result = await update_settings(AppSettingsUpdate(telemetry_routed_hourly=True))
        assert result.telemetry_routed_hourly is True

        result = await update_settings(AppSettingsUpdate(telemetry_routed_hourly=False))
        assert result.telemetry_routed_hourly is False

    @pytest.mark.asyncio
    async def test_schedule_includes_routed_fields_when_enabled(self, test_db):
        key = "aa" * 32
        await ContactRepository.upsert(
            ContactUpsert(public_key=key, name="R1", type=CONTACT_TYPE_REPEATER)
        )
        await AppSettingsRepository.update(
            tracked_telemetry_repeaters=[key],
            telemetry_routed_hourly=True,
        )

        schedule = await get_telemetry_schedule()

        assert schedule.routed_hourly is True
        assert schedule.next_routed_run_at is not None
        assert schedule.next_run_at is not None

    @pytest.mark.asyncio
    async def test_schedule_omits_routed_run_when_disabled(self, test_db):
        key = "aa" * 32
        await ContactRepository.upsert(
            ContactUpsert(public_key=key, name="R1", type=CONTACT_TYPE_REPEATER)
        )
        await AppSettingsRepository.update(
            tracked_telemetry_repeaters=[key],
            telemetry_routed_hourly=False,
        )

        schedule = await get_telemetry_schedule()

        assert schedule.routed_hourly is False
        assert schedule.next_routed_run_at is None

    @pytest.mark.asyncio
    async def test_toggle_response_carries_routed_hourly(self, test_db):
        key = "bb" * 32
        await ContactRepository.upsert(
            ContactUpsert(public_key=key, name="R2", type=CONTACT_TYPE_REPEATER)
        )
        await AppSettingsRepository.update(telemetry_routed_hourly=True)

        result = await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key=key))

        assert result.schedule.routed_hourly is True
        assert result.schedule.next_routed_run_at is not None


class TestTelemetryAlertRulesPatchMerge:
    """PATCH of globals must not wipe stored per-public_key overrides."""

    @pytest.mark.asyncio
    async def test_globals_only_keeps_overrides_explicit_write_wins(self, test_db):
        key = "aa" * 32
        other = "cc" * 32
        await AppSettingsRepository.update(
            telemetry_alert_rules=TelemetryAlertRules(
                battery_volts_min=3.5,
                noise_floor_max_dbm=-90,
                misses_before_alert=2,
                overrides={key: TelemetryAlertRuleOverride(battery_volts_min=3.1)},
            )
        )

        globals_only = await update_settings(
            AppSettingsUpdate.model_validate(
                {
                    "telemetry_alert_rules": {
                        "battery_volts_min": 3.6,
                        "noise_floor_max_dbm": -85,
                        "misses_before_alert": 3,
                    }
                }
            )
        )
        assert globals_only.telemetry_alert_rules.battery_volts_min == 3.6
        assert globals_only.telemetry_alert_rules.noise_floor_max_dbm == -85
        assert globals_only.telemetry_alert_rules.misses_before_alert == 3
        assert globals_only.telemetry_alert_rules.overrides[key].battery_volts_min == 3.1

        null_overrides = await update_settings(
            AppSettingsUpdate.model_validate(
                {
                    "telemetry_alert_rules": {
                        "battery_volts_min": 3.7,
                        "overrides": None,
                    }
                }
            )
        )
        assert null_overrides.telemetry_alert_rules.battery_volts_min == 3.7
        assert null_overrides.telemetry_alert_rules.overrides[key].battery_volts_min == 3.1

        replaced = await update_settings(
            AppSettingsUpdate.model_validate(
                {
                    "telemetry_alert_rules": {
                        "battery_volts_min": 3.5,
                        "noise_floor_max_dbm": -90,
                        "misses_before_alert": 2,
                        "overrides": {other: {"noise_floor_max_dbm": -70}},
                    }
                }
            )
        )
        assert replaced.telemetry_alert_rules.overrides[key].battery_volts_min == 3.1
        assert replaced.telemetry_alert_rules.overrides[other].noise_floor_max_dbm == -70

        cleared = await update_settings(
            AppSettingsUpdate.model_validate(
                {
                    "telemetry_alert_rules": {
                        "battery_volts_min": 3.5,
                        "noise_floor_max_dbm": -90,
                        "misses_before_alert": 2,
                        "overrides": {},
                    }
                }
            )
        )
        assert cleared.telemetry_alert_rules.overrides == {}

    @pytest.mark.asyncio
    async def test_full_v2_document_replaces_overrides(self, test_db):
        from app.models import TelemetryAlertChannels, TelemetryRuleSpec

        keep = "aa" * 32
        drop = "bb" * 32
        await AppSettingsRepository.update(
            telemetry_alert_rules=TelemetryAlertRules(
                channels=TelemetryAlertChannels(push=True),
                rules={"battery": TelemetryRuleSpec(enabled=True, op="lt", threshold=3.5)},
                overrides={
                    keep: TelemetryAlertRuleOverride(alerting=True),
                    drop: TelemetryAlertRuleOverride(alerting=False),
                },
            )
        )
        updated = await update_settings(
            AppSettingsUpdate.model_validate(
                {
                    "telemetry_alert_rules": {
                        "channels": {"push": True, "email": False, "webhook": False},
                        "rules": {"battery": {"enabled": True, "op": "lt", "threshold": 3.5}},
                        "overrides": {keep: {"alerting": False}},
                    }
                }
            )
        )
        overrides = updated.telemetry_alert_rules.overrides or {}
        assert drop not in overrides
        assert overrides[keep].alerting is False

    @pytest.mark.asyncio
    async def test_legacy_three_field_patch_keeps_channels_and_lpp(self, test_db):
        from app.models import TelemetryAlertChannels, TelemetryRuleSpec

        await AppSettingsRepository.update(
            telemetry_alert_rules=TelemetryAlertRules(
                channels=TelemetryAlertChannels(push=True, email=True, webhook=False),
                rules={
                    "lpp:temperature": TelemetryRuleSpec(
                        enabled=True, op="gt", threshold=40, hysteresis=1
                    )
                },
            )
        )
        updated = await update_settings(
            AppSettingsUpdate.model_validate(
                {
                    "telemetry_alert_rules": {
                        "battery_volts_min": 3.2,
                        "noise_floor_max_dbm": -80,
                        "misses_before_alert": 4,
                    }
                }
            )
        )
        rules = updated.telemetry_alert_rules
        assert rules.battery_volts_min == 3.2
        assert rules.noise_floor_max_dbm == -80
        assert rules.misses_before_alert == 4
        assert rules.channels.email is True
        assert rules.channels.webhook is False
        assert rules.rules["lpp:temperature"].enabled is True
        assert rules.rules["lpp:temperature"].threshold == 40

    @pytest.mark.asyncio
    async def test_notification_destinations_redact_and_secret_merge(self, test_db):
        from app.models import NotificationDestinationsUpdate, NotificationEmailDestUpdate
        from app.routers.settings import get_settings

        await update_settings(
            AppSettingsUpdate(
                notification_destinations=NotificationDestinationsUpdate(
                    email=NotificationEmailDestUpdate(host="smtp.example", password="secret")
                )
            )
        )
        shown = await get_settings()
        assert shown.notification_destinations.email.host == "smtp.example"
        assert shown.notification_destinations.email.password == "********"
        stored = await AppSettingsRepository.get()
        assert stored.notification_destinations.email.password == "secret"

        await update_settings(
            AppSettingsUpdate(
                notification_destinations=NotificationDestinationsUpdate(
                    email=NotificationEmailDestUpdate(host="smtp.other")
                )
            )
        )
        stored = await AppSettingsRepository.get()
        assert stored.notification_destinations.email.host == "smtp.other"
        assert stored.notification_destinations.email.password == "secret"

        await update_settings(
            AppSettingsUpdate(
                notification_destinations=NotificationDestinationsUpdate(
                    email=NotificationEmailDestUpdate(password="")
                )
            )
        )
        stored = await AppSettingsRepository.get()
        assert stored.notification_destinations.email.password == ""

    @pytest.mark.asyncio
    async def test_notification_dest_test_does_not_latch(self, test_db):
        from app.models import NotificationDestinationsUpdate, NotificationWebhookDestUpdate
        from app.repository.telemetry_alert_state import TelemetryAlertStateRepository
        from app.routers.settings import (
            NotificationDestTestRequest,
            test_notification_destinations,
        )

        await update_settings(
            AppSettingsUpdate(
                notification_destinations=NotificationDestinationsUpdate(
                    webhook=NotificationWebhookDestUpdate(url="https://example.test/hook")
                )
            )
        )
        with patch("app.notify.send_webhook_alert", new_callable=AsyncMock) as send:
            result = await test_notification_destinations(
                NotificationDestTestRequest(channel="webhook")
            )
        assert result.status == "ok"
        send.assert_awaited_once()
        assert await TelemetryAlertStateRepository.list_latched(["aa" * 32]) == []

    @pytest.mark.asyncio
    async def test_new_tracked_node_alerting_false(self, test_db):
        key = "dd" * 32
        await ContactRepository.upsert(
            ContactUpsert(public_key=key, name="R3", type=CONTACT_TYPE_REPEATER)
        )
        await toggle_tracked_telemetry(TrackedTelemetryRequest(public_key=key))
        stored = await AppSettingsRepository.get()
        assert stored.telemetry_alert_rules.overrides[key].alerting is False
