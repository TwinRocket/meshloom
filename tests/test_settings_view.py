"""``GET /api/settings/view``: the allowlisted settings slice every role may read (MSHL-299)."""

import pytest

from app.models import (
    AppSettings,
    AppSettingsView,
    NotificationDestinationsUpdate,
    ServerLabel,
    UiPreferences,
)
from app.repository import AppSettingsRepository

DM_KEY = "contact-" + "ab" * 32
CHANNEL_KEY = "channel-" + "CD" * 16


@pytest.fixture
async def seeded(test_db):
    await AppSettingsRepository.update(
        ui_preferences=UiPreferences(theme="dark", nav_rail=["chat"]),
        blocked_keys=["ef" * 32],
        blocked_names=["spammer"],
        known_regions=["west"],
        flood_scope="west",
        last_message_times={DM_KEY: 10, CHANNEL_KEY: 20},
        # Admin-only configuration the view must never carry.
        notification_destinations=NotificationDestinationsUpdate.model_validate(
            {"webhook": {"enabled": True, "url": "https://example.org/hook-secret-path"}}
        ),
        auth_allowed_origins=["https://other.example"],
        tracked_telemetry_repeaters=["12" * 32],
    )


@pytest.mark.parametrize("role", ["admin", "member", "viewer", "kiosk"])
async def test_every_role_reads_the_view(seeded, sign_in_as, client, role):
    sign_in_as(role)
    response = await client.get("/api/settings/view")
    assert response.status_code == 200
    body = response.json()
    assert body["ui_preferences"]["theme"] == "dark"
    assert body["blocked_keys"] == ["ef" * 32]
    assert body["blocked_names"] == ["spammer"]
    assert body["known_regions"] == ["west"]
    assert body["flood_scope"] == "west"
    assert body["last_message_times"][CHANNEL_KEY] == 20


@pytest.mark.parametrize("role", ["admin", "member", "viewer"])
async def test_direct_conversation_times_need_read_private(seeded, sign_in_as, client, role):
    sign_in_as(role)
    body = (await client.get("/api/settings/view")).json()
    assert body["last_message_times"][DM_KEY] == 10


async def test_kiosk_gets_no_direct_conversation_key(seeded, sign_in_as, client):
    sign_in_as("kiosk")
    body = (await client.get("/api/settings/view")).json()
    assert body["last_message_times"] == {CHANNEL_KEY: 20}


async def test_below_read_private_only_channel_keys_survive(test_db, sign_in_as, client):
    await AppSettingsRepository.update(
        last_message_times={CHANNEL_KEY: 1, "CONTACT-" + "ab" * 32: 2, "other-x": 3}
    )
    sign_in_as("kiosk")
    body = (await client.get("/api/settings/view")).json()
    assert body["last_message_times"] == {CHANNEL_KEY: 1}


async def test_scope_without_read_private_gets_no_direct_conversation_key(
    seeded, sign_in_as, client
):
    sign_in_as("member", scopes=frozenset({"read"}))
    body = (await client.get("/api/settings/view")).json()
    assert DM_KEY not in body["last_message_times"]


@pytest.mark.parametrize("role", ["admin", "member", "viewer", "kiosk"])
async def test_the_view_carries_no_administration_setting(seeded, sign_in_as, client, role):
    sign_in_as(role)
    response = await client.get("/api/settings/view")
    assert set(response.json()) == VIEW_FIELDS
    for leaked in ("hook-secret-path", "other.example", "notification_destinations"):
        assert leaked not in response.text


# The exact set every role receives. Adding a name here widens what a kiosk can read:
# it needs a security review, never just a test edit.
VIEW_FIELDS = {
    "ui_preferences",
    "last_message_times",
    "flood_scope",
    "known_regions",
    "blocked_keys",
    "blocked_names",
    "directory_available",
}
UI_PREFERENCES_FIELDS = {"nav_rail", "theme", "start_view", "server_label"}
SERVER_LABEL_FIELDS = {"text", "color", "size_px", "bold", "italic", "font"}


def test_the_exposed_field_set_is_pinned():
    assert set(AppSettingsView.model_fields) == VIEW_FIELDS
    assert set(UiPreferences.model_fields) == UI_PREFERENCES_FIELDS
    assert set(ServerLabel.model_fields) == SERVER_LABEL_FIELDS


def test_the_view_is_a_subset_of_the_settings():
    assert set(AppSettingsView.model_fields) <= set(AppSettings.model_fields)


@pytest.mark.parametrize("role", ["member", "viewer", "kiosk"])
async def test_full_settings_stay_admin_only(seeded, sign_in_as, client, role):
    sign_in_as(role)
    assert (await client.get("/api/settings")).status_code == 403
