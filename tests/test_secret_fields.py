"""Write-only secrets (MSHL-106): fanout configs, notification destinations, v1 backups."""

from __future__ import annotations

import logging
import re
from unittest.mock import AsyncMock, patch

import pytest

from app.fanout.base import FanoutModule
from app.fanout.manager import fanout_manager
from app.models import BackupFanoutConfig, MeshloomExtra
from app.repository import AppSettingsRepository
from app.repository.fanout import FanoutConfigRepository
from app.secret_fields import (
    REDACTED,
    SecretRequiredError,
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
        "access_key_id": REDACTED,
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
    stored = {"broker_host": "h", "password": "stored"}
    merged = merge_secrets("mqtt_private", stored, {"broker_host": "h", **incoming})
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


def test_scrub_error_keeps_a_user_host_url_host_readable():
    url = "json://alice:pw-123@hooks.example.org:8443/path-tok/x?key=qval-9#frag-7"
    secrets = config_secret_values("apprise", {"urls": url})
    scrubbed = scrub_error(f"could not reach {url}", secrets)
    assert scrubbed is not None
    assert re.search(r"hooks\.example\.org:8443/", scrubbed)
    for part in ("alice", "pw-123", "path-tok", "qval-9", "frag-7"):
        assert part not in scrubbed


# -- a kept secret never follows a destination change ---------------------------

# (type, stored config, secret key(s), destination key, new destination value)
DESTINATION_MOVES = [
    (
        "webhook",
        {"url": "https://example.org/h", "hmac_secret": "s0"},
        "hmac_secret",
        "url",
        "https://other.example.org/h",
    ),
    (
        "mqtt_private",
        {"broker_host": "mqtt.example.org", "password": "pw0"},
        "password",
        "broker_host",
        "evil.example.org",
    ),
    (
        "mqtt_private",
        {"broker_host": "mqtt.example.org", "use_tls": True, "password": "pw0"},
        "password",
        "use_tls",
        False,
    ),
    (
        "mqtt_ha",
        {"broker_host": "ha.example.org", "broker_port": 1883, "password": "pw0"},
        "password",
        "broker_port",
        1884,
    ),
    (
        "mqtt_community",
        {"auth_mode": "password", "username": "u", "password": "pw0", "iata": "CDG"},
        "password",
        "broker_host",
        "evil.example.org",
    ),
    (
        "sqs",
        {
            "queue_url": "https://sqs.eu-west-1.amazonaws.com/1/q",
            "region_name": "eu-west-1",
            "access_key_id": "AKIA",
            "secret_access_key": "sk0",
        },
        ("access_key_id", "secret_access_key"),
        "endpoint_url",
        "https://evil.example.org",
    ),
]
DESTINATION_IDS = [f"{case[0]}-{case[3]}" for case in DESTINATION_MOVES]


def _secret_keys(key: str | tuple[str, ...]) -> tuple[str, ...]:
    return key if isinstance(key, tuple) else (key,)


@pytest.mark.parametrize(
    ("kind", "config", "key", "field", "value"), DESTINATION_MOVES, ids=DESTINATION_IDS
)
def test_merge_secrets_refuses_a_kept_secret_on_a_new_destination(kind, config, key, field, value):
    keys = _secret_keys(key)
    without_secret = {k: v for k, v in config.items() if k not in keys}
    for kept in (dict.fromkeys(keys, REDACTED), {}):
        with pytest.raises(SecretRequiredError) as exc:
            merge_secrets(kind, config, {**without_secret, **kept, field: value})
        assert exc.value.keys == list(keys)
        assert exc.value.detail()["code"] == "secret_required"
    fresh = merge_secrets(kind, config, {**config, **dict.fromkeys(keys, "fresh"), field: value})
    assert all(fresh[k] == "fresh" for k in keys)
    cleared = merge_secrets(kind, config, {**config, **dict.fromkeys(keys, ""), field: value})
    assert all(cleared[k] == "" for k in keys)
    # Same destination, spelled with the default or a string port: kept.
    same = merge_secrets(kind, config, {**config, **dict.fromkeys(keys, REDACTED)})
    assert all(same[k] == config[k] for k in keys)


def test_a_row_without_destination_keys_reads_as_the_defaults():
    stored = {"password": "pw0"}
    incoming = {"broker_host": "", "broker_port": "1883", "password": REDACTED}
    assert merge_secrets("mqtt_private", stored, incoming)["password"] == "pw0"


@pytest.mark.parametrize(
    ("kind", "config", "key", "field", "value"), DESTINATION_MOVES, ids=DESTINATION_IDS
)
@pytest.mark.asyncio
async def test_patch_to_a_new_destination_requires_the_secret(
    test_db, client, no_module_start, kind, config, key, field, value
):
    keys = _secret_keys(key)
    row = await FanoutConfigRepository.create(
        config_type=kind, name="x", config=config, scope={"messages": "all"}, enabled=False
    )
    shown = (await client.get(f"/api/fanout/{row['id']}")).json()["config"]
    assert all(shown[k] == REDACTED for k in keys)

    refused = await client.patch(
        f"/api/fanout/{row['id']}", json={"config": {**shown, field: value}}
    )
    assert refused.status_code == 400, refused.text
    detail = refused.json()["detail"]
    assert detail["code"] == "secret_required"
    assert detail["params"] == {"fields": ", ".join(keys)}
    stored = await FanoutConfigRepository.get(row["id"])
    assert stored is not None
    assert stored["config"] == config

    accepted = await client.patch(
        f"/api/fanout/{row['id']}",
        json={"config": {**shown, field: value, **dict.fromkeys(keys, "fresh")}},
    )
    assert accepted.status_code == 200, accepted.text
    stored = await FanoutConfigRepository.get(row["id"])
    assert stored is not None
    assert all(stored["config"][k] == "fresh" for k in keys)
    assert stored["config"][field] == value


@pytest.mark.parametrize(
    ("kind", "config", "key", "field", "value"), DESTINATION_MOVES, ids=DESTINATION_IDS
)
@pytest.mark.asyncio
async def test_crafted_restore_to_a_new_destination_drops_the_secret(
    backup_db, no_module_start, kind, config, key, field, value
):
    await FanoutConfigRepository.create(
        config_type=kind,
        name="Row",
        config=config,
        scope={"messages": "all"},
        enabled=False,
        config_id="row-1",
    )
    entry = BackupFanoutConfig(
        id="row-1",
        type=kind,
        name="Row",
        enabled=False,
        config={**config, **dict.fromkeys(_secret_keys(key), REDACTED), field: value},
        scope={"messages": "all"},
    )
    warnings = await restore_meshloom_extra(MeshloomExtra(fanout_configs=[entry]))
    assert "secret_dropped_destination_changed:Row" in warnings
    stored = await FanoutConfigRepository.get("row-1")
    assert stored is not None
    # Either the row moved without its secret, or (a type that needs the
    # secret to validate) it stayed where it was: never both.
    moved = stored["config"].get(field) == value
    assert not any(moved and stored["config"].get(k) == config[k] for k in _secret_keys(key))


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
    monkeypatch.setattr(apprise_mod, "is_filtered", AsyncMock(return_value=False))
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
        (
            "webhook",
            {"url": "https://example.org/h", "hmac_secret": "s3cret"},
            "hmac_secret",
            "n3w",
        ),
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

    restored = await client.post("/api/settings/backup/restore", json={**document, "confirm": True})
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


# -- notification destinations: same rule -----------------------------------------


async def _store_destinations(password: str = "smtp-pass", hmac: str = "hmac-secret"):
    stored = await AppSettingsRepository.get()
    dest = stored.notification_destinations.model_copy(deep=True)
    dest.email.host = "smtp.example.org"
    dest.email.password = password
    dest.webhook.url = "https://example.org/alerts"
    dest.webhook.hmac_secret = hmac
    await AppSettingsRepository.update(notification_destinations=dest)
    return dest


@pytest.mark.parametrize(
    ("patch_body", "field"),
    [
        ({"email": {"host": "smtp.evil.example"}}, "email.password"),
        ({"email": {"port": 25, "password": REDACTED}}, "email.password"),
        ({"email": {"mode": "none"}}, "email.password"),
        ({"webhook": {"url": "https://evil.example/alerts"}}, "webhook.hmac_secret"),
    ],
)
@pytest.mark.asyncio
async def test_notification_patch_to_a_new_destination_requires_the_secret(
    test_db, client, patch_body, field
):
    await _store_destinations()
    response = await client.patch("/api/settings", json={"notification_destinations": patch_body})
    assert response.status_code == 400, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "secret_required"
    assert detail["params"] == {"fields": field}
    after = (await AppSettingsRepository.get()).notification_destinations
    assert after.email.host == "smtp.example.org"
    assert after.email.password == "smtp-pass"
    assert after.webhook.url == "https://example.org/alerts"


@pytest.mark.asyncio
async def test_notification_patch_with_the_secret_or_same_destination_is_accepted(test_db, client):
    await _store_destinations()
    same = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"email": {"to": "ops@example.org"}}},
    )
    assert same.status_code == 200, same.text
    moved = await client.patch(
        "/api/settings",
        json={
            "notification_destinations": {
                "email": {"host": "smtp2.example.org", "password": "new-pass"}
            }
        },
    )
    assert moved.status_code == 200, moved.text
    after = (await AppSettingsRepository.get()).notification_destinations
    assert after.email.host == "smtp2.example.org"
    assert after.email.password == "new-pass"
    assert after.email.to == "ops@example.org"


@pytest.mark.asyncio
async def test_v1_restore_to_a_new_destination_drops_the_secret(test_db, client):
    await _store_destinations()
    document = (await client.get("/api/settings/backup/json")).json()
    dest = document["settings"]["notification_destinations"]
    dest["email"]["host"] = "smtp.evil.example"
    dest["webhook"]["url"] = "https://evil.example/alerts"

    restored = await client.post("/api/settings/backup/restore", json={**document, "confirm": True})
    assert restored.status_code == 200, restored.text
    assert set(restored.json()["warnings"]) == {
        "secret_dropped_destination_changed:email.password",
        "secret_dropped_destination_changed:webhook.hmac_secret",
    }
    after = (await AppSettingsRepository.get()).notification_destinations
    assert after.email.host == "smtp.evil.example"
    assert after.email.password == ""
    assert after.webhook.url == "https://evil.example/alerts"
    assert after.webhook.hmac_secret == ""


# -- MSHL-275: webhook URL and headers, SQS access key id ---------------------------

HOOK_URL = "https://hooks.example.org/api/webhooks/123/hook-tok-9?key=qval-9"
HOOK_TOKENS = ("hook-tok-9", "qval-9", "Bearer hdr-tok-7")
HOOK_CONFIG = {
    "url": HOOK_URL,
    "headers": {"Authorization": "Bearer hdr-tok-7", "X-Empty": ""},
    "hmac_secret": "s0",
}


def _assert_no_hook_token(text: str) -> None:
    for part in HOOK_TOKENS:
        assert part not in text


def test_webhook_url_and_header_values_are_redacted():
    redacted = redact_config("webhook", HOOK_CONFIG)
    assert redacted["url"] == REDACTED
    assert redacted["url_redacted"] == "https://hooks.example.org/****"
    assert redacted["headers"] == {"Authorization": REDACTED, "X-Empty": ""}
    _assert_no_hook_token(repr(redacted))
    assert redact_config("webhook", {"url": ""})["url_redacted"] == ""


def test_webhook_redacted_config_sent_back_keeps_everything():
    redacted = redact_config("webhook", HOOK_CONFIG)
    assert merge_secrets("webhook", HOOK_CONFIG, redacted) == HOOK_CONFIG


def test_webhook_headers_merge_by_name():
    incoming = {
        "url": REDACTED,
        "headers": {"Authorization": REDACTED, "X-New": "v", "X-Ghost": REDACTED},
    }
    merged = merge_secrets("webhook", HOOK_CONFIG, incoming)
    # Kept by name, added, and a removed name (X-Empty) or a sentinel with no
    # stored value (X-Ghost) is gone.
    assert merged["headers"] == {"Authorization": "Bearer hdr-tok-7", "X-New": "v"}
    assert merged["url"] == HOOK_URL
    assert merged["hmac_secret"] == "s0"


def test_a_new_webhook_url_requires_the_headers_and_secret_again():
    moved = {"url": "https://evil.example.org/x", "headers": {"Authorization": REDACTED}}
    with pytest.raises(SecretRequiredError) as exc:
        merge_secrets("webhook", HOOK_CONFIG, moved)
    assert exc.value.keys == ["headers", "hmac_secret"]
    retyped = merge_secrets(
        "webhook",
        HOOK_CONFIG,
        {**moved, "headers": {"Authorization": "Bearer other"}, "hmac_secret": ""},
    )
    assert retyped["headers"] == {"Authorization": "Bearer other"}
    # A header with no value to keep is no secret to follow the new URL.
    plain = merge_secrets(
        "webhook", HOOK_CONFIG, {**moved, "headers": {"X-Plain": "1"}, "hmac_secret": ""}
    )
    assert plain["headers"] == {"X-Plain": "1"}


def test_a_cleared_headers_secret_empties_to_an_object():
    assert merge_secrets("webhook", HOOK_CONFIG, {"url": REDACTED, "headers": ""})["headers"] == {}
    assert (
        merge_secrets("webhook", None, {"url": "https://a.example", "headers": None})["headers"]
        == {}
    )


def test_webhook_url_and_header_values_are_scrubbed_from_errors():
    secrets = config_secret_values("webhook", HOOK_CONFIG)
    scrubbed = scrub_error(f"error POST {HOOK_URL} with Bearer hdr-tok-7", secrets)
    assert scrubbed is not None
    assert "hooks.example.org" in scrubbed
    _assert_no_hook_token(scrubbed)
    assert "hdr-tok-7" not in scrubbed


def test_sqs_access_key_id_is_scrubbed_from_errors():
    secrets = config_secret_values("sqs", {"access_key_id": "AKIAEXAMPLE1"})
    assert scrub_error("bad key AKIAEXAMPLE1", secrets) == "bad key ****"


@pytest.mark.asyncio
async def test_webhook_api_never_returns_the_url_or_header_values(test_db, client, no_module_start):
    row = await FanoutConfigRepository.create(
        config_type="webhook",
        name="x",
        config=HOOK_CONFIG,
        scope={"messages": "all"},
        enabled=False,
    )
    listed = await client.get("/api/fanout")
    one = await client.get(f"/api/fanout/{row['id']}")
    for response in (listed, one):
        assert response.status_code == 200
        _assert_no_hook_token(response.text)
    shown = one.json()["config"]
    assert shown["url_redacted"] == "https://hooks.example.org/****"

    saved = await client.patch(
        f"/api/fanout/{row['id']}", json={"config": {**shown, "method": "PUT"}}
    )
    assert saved.status_code == 200, saved.text
    _assert_no_hook_token(saved.text)
    stored = await FanoutConfigRepository.get(row["id"])
    assert stored is not None
    assert stored["config"]["url"] == HOOK_URL
    assert stored["config"]["headers"] == HOOK_CONFIG["headers"]
    assert stored["config"]["method"] == "PUT"
    assert "url_redacted" not in stored["config"]


@pytest.mark.asyncio
async def test_webhook_url_change_without_headers_is_400(test_db, client, no_module_start):
    row = await FanoutConfigRepository.create(
        config_type="webhook",
        name="x",
        config={"url": HOOK_URL, "headers": {"Authorization": "Bearer hdr-tok-7"}},
        scope={"messages": "all"},
        enabled=False,
    )
    shown = (await client.get(f"/api/fanout/{row['id']}")).json()["config"]
    refused = await client.patch(
        f"/api/fanout/{row['id']}",
        json={"config": {**shown, "url": "https://evil.example.org/x"}},
    )
    assert refused.status_code == 400, refused.text
    assert refused.json()["detail"]["params"] == {"fields": "headers"}


@pytest.mark.asyncio
async def test_legacy_restore_with_clear_values_keeps_working(backup_db, no_module_start):
    """A 4.18.x backup holds the URL, headers and access key id in clear."""
    sqs_config = {
        "queue_url": "https://sqs.eu-west-1.amazonaws.com/1/q",
        "region_name": "eu-west-1",
        "access_key_id": "AKIAEXAMPLE1",
        "secret_access_key": "sk0",
    }
    entries = [
        BackupFanoutConfig(
            id="hook-1",
            type="webhook",
            name="Hook",
            enabled=False,
            config=HOOK_CONFIG,
            scope={"messages": "all"},
        ),
        BackupFanoutConfig(
            id="sqs-1",
            type="sqs",
            name="Queue",
            enabled=False,
            config=sqs_config,
            scope={"messages": "all"},
        ),
    ]
    warnings = await restore_meshloom_extra(MeshloomExtra(fanout_configs=entries))
    assert not [w for w in warnings if w.startswith(("fanout_invalid", "secret_dropped"))]
    hook = await FanoutConfigRepository.get("hook-1")
    queue = await FanoutConfigRepository.get("sqs-1")
    assert hook is not None and queue is not None
    assert hook["config"]["url"] == HOOK_URL
    assert hook["config"]["headers"] == HOOK_CONFIG["headers"]
    assert queue["config"]["access_key_id"] == "AKIAEXAMPLE1"


@pytest.mark.asyncio
async def test_restore_to_a_new_url_drops_the_kept_headers(backup_db, no_module_start):
    await FanoutConfigRepository.create(
        config_type="webhook",
        name="Hook",
        config=HOOK_CONFIG,
        scope={"messages": "all"},
        enabled=False,
        config_id="hook-1",
    )
    entry = BackupFanoutConfig(
        id="hook-1",
        type="webhook",
        name="Hook",
        enabled=False,
        config={
            "url": "https://evil.example.org/x",
            "headers": {"Authorization": REDACTED},
            "hmac_secret": REDACTED,
        },
        scope={"messages": "all"},
    )
    warnings = await restore_meshloom_extra(MeshloomExtra(fanout_configs=[entry]))
    assert "secret_dropped_destination_changed:Hook" in warnings
    stored = await FanoutConfigRepository.get("hook-1")
    assert stored is not None
    assert stored["config"]["url"] == "https://evil.example.org/x"
    assert stored["config"]["headers"] == {}
    assert stored["config"]["hmac_secret"] == ""


@pytest.mark.asyncio
async def test_notification_webhook_url_is_write_only(test_db, client):
    await _store_destinations()
    settings = await client.get("/api/settings")
    webhook = settings.json()["notification_destinations"]["webhook"]
    assert webhook["url"] == REDACTED
    assert webhook["url_redacted"] == "https://example.org/****"
    assert "example.org/alerts" not in settings.text

    # The form sent back: URL and HMAC secret kept, no secret_required.
    saved = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"webhook": {**webhook, "hmac_secret": REDACTED}}},
    )
    assert saved.status_code == 200, saved.text
    assert "example.org/alerts" not in saved.text
    after = (await AppSettingsRepository.get()).notification_destinations
    assert after.webhook.url == "https://example.org/alerts"
    assert after.webhook.hmac_secret == "hmac-secret"
    assert after.webhook.url_redacted is None

    cleared = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"webhook": {"url": "", "hmac_secret": ""}}},
    )
    assert cleared.status_code == 200, cleared.text
    after = (await AppSettingsRepository.get()).notification_destinations
    assert after.webhook.url == ""


@pytest.mark.asyncio
async def test_json_backup_leaves_out_the_notification_webhook_url(test_db, client):
    await _store_destinations()
    response = await client.get("/api/settings/backup/json")
    assert "example.org/alerts" not in response.text
    document = response.json()
    assert document["secret_omitted"]["settings.notification_destinations.webhook.url"] is True
    assert "url_redacted" not in document["settings"]["notification_destinations"]["webhook"]

    restored = await client.post("/api/settings/backup/restore", json={**document, "confirm": True})
    assert restored.status_code == 200, restored.text
    assert restored.json()["warnings"] == []
    after = (await AppSettingsRepository.get()).notification_destinations
    assert after.webhook.url == "https://example.org/alerts"
    assert after.webhook.hmac_secret == "hmac-secret"


@pytest.mark.asyncio
async def test_legacy_v1_restore_with_a_clear_webhook_url(test_db, client):
    """A 4.18.x JSON backup holds the notification webhook URL in clear."""
    document = (await client.get("/api/settings/backup/json")).json()
    webhook = document["settings"]["notification_destinations"]["webhook"]
    webhook["url"] = "https://example.org/legacy"
    webhook["hmac_secret"] = "legacy-secret"
    restored = await client.post("/api/settings/backup/restore", json={**document, "confirm": True})
    assert restored.status_code == 200, restored.text
    after = (await AppSettingsRepository.get()).notification_destinations
    assert after.webhook.url == "https://example.org/legacy"
    assert after.webhook.hmac_secret == "legacy-secret"
