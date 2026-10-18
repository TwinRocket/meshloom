"""Write-only secrets (MSHL-106): fanout configs, notification destinations, v1 backups."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.fanout.base import FanoutModule
from app.fanout.manager import fanout_manager
from app.models import BackupFanoutConfig, MeshloomExtra
from app.repository import AppSettingsRepository
from app.repository.fanout import FanoutConfigRepository
from app.secret_fields import (
    REDACTED,
    config_secret_values,
    merge_secrets,
    redact_config,
    scrub_error,
)
from app.services.backup import restore_meshloom_extra

TGRAM = "tgram://123:ABC/456"
TOKEN_PARTS = ("123:ABC", "ABC/456")


def _assert_no_token(text: str) -> None:
    for part in TOKEN_PARTS:
        assert part not in text


class _FailingModule(FanoutModule):
    """A running module whose last error quotes its URL, as an Apprise exception can."""

    @property
    def status(self) -> str:
        return "error"


@pytest.fixture
def no_module_start(monkeypatch):
    monkeypatch.setattr(fanout_manager, "reload_config", AsyncMock())


# -- helpers ------------------------------------------------------------------


def test_redact_config_masks_set_secrets_and_summarises_apprise_urls():
    redacted = redact_config("apprise", {"urls": f"{TGRAM}\njson://example.org/x"})
    assert redacted["urls"] == REDACTED
    assert redacted["urls_redacted"] and len(redacted["urls_redacted"]) == 2
    _assert_no_token(repr(redacted))
    assert redact_config("webhook", {"hmac_secret": ""})["hmac_secret"] == ""
    assert redact_config("sqs", {"secret_access_key": "s", "access_key_id": "AKIA"}) == {
        "secret_access_key": REDACTED,
        "access_key_id": "AKIA",
    }


def test_redaction_keeps_the_private_network_flags():
    config = {"urls": TGRAM, "allow_private_network": True, "private_network_grandfathered": True}
    redacted = redact_config("apprise", config)
    assert redacted["allow_private_network"] is True
    assert redacted["private_network_grandfathered"] is True


@pytest.mark.parametrize(
    ("incoming", "expected"),
    [
        ({"password": REDACTED}, "stored"),
        ({"password": None}, "stored"),
        ({}, "stored"),
        ({"password": "new"}, "new"),
        ({"password": ""}, ""),
    ],
)
def test_merge_secrets_keep_replace_clear(incoming, expected):
    merged = merge_secrets("mqtt_private", {"password": "stored"}, {"broker_host": "h", **incoming})
    assert merged["password"] == expected


def test_merge_secrets_without_a_stored_value_and_derived_keys():
    assert merge_secrets("webhook", None, {"hmac_secret": REDACTED})["hmac_secret"] == ""
    merged = merge_secrets("apprise", {"urls": TGRAM}, {"urls": REDACTED, "urls_redacted": ["x"]})
    assert merged == {"urls": TGRAM}


def test_scrub_error_removes_url_fragments():
    secrets = config_secret_values("apprise", {"urls": TGRAM})
    scrubbed = scrub_error(f"failed to reach {TGRAM}: token ABC rejected", secrets)
    assert scrubbed is not None
    _assert_no_token(scrubbed)
    assert "ABC" not in scrubbed
    assert scrub_error(None, secrets) is None


# -- acceptance: the Apprise token never leaves --------------------------------


@pytest.mark.asyncio
async def test_apprise_token_absent_from_fanout_health_and_debug(
    test_db, client, monkeypatch, no_module_start
):
    row = await FanoutConfigRepository.create(
        config_type="apprise",
        name="Telegram",
        config={"urls": TGRAM},
        scope={"messages": "all"},
        enabled=True,
    )
    module = _FailingModule(row["id"], row["config"], name="Telegram")
    module._last_error = f"Apprise could not reach {TGRAM}"
    monkeypatch.setitem(fanout_manager._modules, row["id"], (module, {"messages": "all"}))

    listed = await client.get("/api/fanout")
    single = await client.get(f"/api/fanout/{row['id']}")
    health = await client.get("/api/health")
    debug = await client.get("/api/debug")

    for response in (listed, single, health, debug):
        assert response.status_code == 200, response.text
        _assert_no_token(response.text)
    assert single.json()["config"]["urls"] == REDACTED
    (summary,) = single.json()["config"]["urls_redacted"]
    assert summary.startswith("tgram://")
    statuses = health.json()["fanout_statuses"]
    assert statuses[row["id"]]["status"] == "error"
    assert "****" in statuses[row["id"]]["last_error"]


@pytest.mark.asyncio
async def test_apprise_send_error_is_scrubbed_in_log_and_status(caplog, monkeypatch):
    from app.fanout import apprise_mod

    monkeypatch.setattr(apprise_mod, "_RETRY_DELAY_S", 0)
    monkeypatch.setattr(apprise_mod, "_validated_servers", AsyncMock(return_value=[]))
    module = apprise_mod.AppriseModule("tg", {"urls": TGRAM})
    with (
        patch.object(apprise_mod, "_send_sync", side_effect=RuntimeError(f"bad {TGRAM}")),
        caplog.at_level(logging.WARNING, logger="app.fanout.apprise_mod"),
    ):
        await module.on_message({"type": "PRIV", "text": "hi", "sender_name": "A"})
    assert "Apprise send error" in caplog.text
    _assert_no_token(caplog.text)
    assert module.last_error is not None
    _assert_no_token(module.last_error)


@pytest.mark.asyncio
async def test_unknown_or_reserved_fanout_id_is_404(test_db, client):
    assert (await client.get("/api/fanout/missing")).status_code == 404
    assert (await client.get("/api/fanout/system:meshloom_stats")).status_code == 404


# -- PATCH semantics -----------------------------------------------------------


@pytest.mark.parametrize(
    ("config_type", "config", "key", "new_value"),
    [
        ("apprise", {"urls": TGRAM}, "urls", "tgram://999:NEW/1"),
        ("webhook", {"url": "https://example.org/h", "hmac_secret": "s3cret"}, "hmac_secret", "n3w"),
        (
            "mqtt_private",
            {"broker_host": "mqtt.example.org", "broker_port": 1883, "password": "pw0"},
            "password",
            "pw1",
        ),
        (
            "sqs",
            {
                "queue_url": "https://sqs.eu-west-1.amazonaws.com/1/q",
                "region_name": "eu-west-1",
                "access_key_id": "AKIA",
                "secret_access_key": "sk0",
            },
            "secret_access_key",
            "sk1",
        ),
    ],
)
@pytest.mark.asyncio
async def test_patch_keeps_replaces_and_clears_a_secret(
    test_db, client, no_module_start, config_type, config, key, new_value
):
    row = await FanoutConfigRepository.create(
        config_type=config_type, name="x", config=config, scope={"messages": "all"}, enabled=False
    )
    stored_value = config[key]

    async def patch_with(value):
        shown = (await client.get(f"/api/fanout/{row['id']}")).json()["config"]
        assert shown[key] in (REDACTED, "")
        body = {**shown, key: value}
        response = await client.patch(f"/api/fanout/{row['id']}", json={"config": body})
        assert response.status_code == 200, response.text
        assert stored_value not in response.text
        stored = await FanoutConfigRepository.get(row["id"])
        assert stored is not None
        return stored["config"][key]

    assert await patch_with(REDACTED) == stored_value
    if config_type == "apprise":
        # Clearing the URLs of an Apprise row is refused by its validation.
        return
    assert await patch_with(new_value) == new_value
    if config_type == "sqs":
        # SQS refuses a key id without its secret key: never cleared alone.
        return
    assert await patch_with("") == ""


# -- v1 JSON backup ------------------------------------------------------------


@pytest.mark.asyncio
async def test_json_backup_has_no_notification_secret_and_restore_keeps_them(test_db, client):
    stored = await AppSettingsRepository.get()
    destinations = stored.notification_destinations.model_copy(deep=True)
    destinations.email.password = "smtp-pass-1"
    destinations.webhook.hmac_secret = "hmac-secret-1"
    await AppSettingsRepository.update(notification_destinations=destinations)

    response = await client.get("/api/settings/backup/json")
    assert response.status_code == 200
    assert "smtp-pass-1" not in response.text
    assert "hmac-secret-1" not in response.text
    document = response.json()
    assert document["secret_omitted"] == {
        "settings.notification_destinations.email.password": True,
        "settings.notification_destinations.webhook.hmac_secret": True,
    }
    assert document["settings"]["notification_destinations"]["email"]["password"] == REDACTED

    restored = await client.post(
        "/api/settings/backup/restore", json={**document, "confirm": True}
    )
    assert restored.status_code == 200, restored.text
    after = (await AppSettingsRepository.get()).notification_destinations
    assert after.email.password == "smtp-pass-1"
    assert after.webhook.hmac_secret == "hmac-secret-1"


@pytest.mark.asyncio
async def test_full_backup_keeps_the_secrets(test_db):
    from app.services.backup import export_json

    stored = await AppSettingsRepository.get()
    destinations = stored.notification_destinations.model_copy(deep=True)
    destinations.email.password = "smtp-pass-2"
    await AppSettingsRepository.update(notification_destinations=destinations)

    document = await export_json(include_secrets=True)
    assert document.settings.notification_destinations.email.password == "smtp-pass-2"
    assert document.secret_omitted == {}


# -- restore: secrets and LAN opt-ins -------------------------------------------


@pytest.mark.asyncio
async def test_restored_redacted_secret_keeps_the_stored_one(backup_db, no_module_start):
    await FanoutConfigRepository.create(
        config_type="webhook",
        name="Hook",
        config={"url": "https://example.org/h", "hmac_secret": "keep-me"},
        scope={"messages": "all"},
        enabled=False,
        config_id="hook-1",
    )
    entry = BackupFanoutConfig(
        id="hook-1",
        type="webhook",
        name="Hook",
        enabled=False,
        config={"url": "https://example.org/h", "hmac_secret": REDACTED},
        scope={"messages": "all"},
    )
    await restore_meshloom_extra(MeshloomExtra(fanout_configs=[entry]))
    stored = await FanoutConfigRepository.get("hook-1")
    assert stored is not None
    assert stored["config"]["hmac_secret"] == "keep-me"


@pytest.mark.parametrize(
    "config",
    [
        {"url": "https://example.org/h", "allow_private_network": True},
        # A row saved before the guard: the restore grandfathers its opt-in.
        {"url": "https://example.org/h"},
    ],
)
@pytest.mark.asyncio
async def test_private_network_row_comes_back_disabled(backup_db, no_module_start, config):
    entry = BackupFanoutConfig(
        id="hook-2",
        type="webhook",
        name="LAN hook",
        enabled=True,
        config=config,
        scope={"messages": "all"},
    )
    warnings = await restore_meshloom_extra(MeshloomExtra(fanout_configs=[entry]))
    assert "private_network_restored_disabled:LAN hook" in warnings
    stored = await FanoutConfigRepository.get("hook-2")
    assert stored is not None
    assert stored["enabled"] is False
    assert stored["config"]["allow_private_network"] is True
