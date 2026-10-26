"""The notifications REST API (``app/routers/notifications.py``): types, channels, rules,
the journal, the test sends and the traces of a channel change."""

import ipaddress
import json
import socket
import time
from pathlib import Path

import pytest

from app.auth.ws_events import WsEvent
from app.notifications import api_support
from app.notifications.channels import types_schema
from app.notifications.transports.base import SendResult
from app.notifications.transports.webhook import WebhookTransport
from app.repository.auth import AuthAuditRepository

SNAPSHOT = Path(__file__).parent / "fixtures" / "notification_types_schema.json"

WEBHOOK_URL = "https://hooks.example.org/services/T0/B0/secret-token-1"
SECRETS = ("secret-token-1", "hmac-secret-1", "smtp-pass-1", "AAAA-token-a")


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """Every host name resolves to one public address; IP literals resolve as themselves."""
    real = socket.getaddrinfo

    def fake(host, port, *args, **kwargs):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            host = "93.184.216.34"
        return real(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


REAL_WEBHOOK_SEND = WebhookTransport.send


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """No transport reaches the network: a change announces itself to the other channels."""

    async def send(self, channel, rendered, event, secrets):
        return SendResult("ok")

    for module, name in (
        ("webhook", "WebhookTransport"),
        ("email", "EmailTransport"),
        ("apprise", "AppriseTransport"),
        ("ntfy", "NtfyTransport"),
        ("web_push", "WebPushTransport"),
    ):
        monkeypatch.setattr(f"app.notifications.transports.{module}.{name}.send", send)


@pytest.fixture
def traces(monkeypatch):
    """The WebSocket events and the notifications a channel change produces."""
    emitted: list[dict] = []

    def capture_ws(event_type, data, **kwargs):
        emitted.append({"ws": event_type, "data": data, "kwargs": kwargs})

    async def capture_emit(event, *, payload=None, targets=None):
        emitted.append({"emit": payload, "targets": list(targets or [])})

    monkeypatch.setattr("app.websocket.broadcast_event", capture_ws)
    monkeypatch.setattr("app.notifications.dispatcher.emit", capture_emit)
    return emitted


def webhook(**extra):
    config = {"url": WEBHOOK_URL, "hmac_secret": "hmac-secret-1"}
    return {"type": "webhook", "name": "Hook", "config": config, **extra}


def email(**config):
    base = {"host": "smtp.example.org", "to": "me@example.org", "password": "smtp-pass-1"}
    return {"type": "email", "name": "Mail", "config": {**base, **config}}


def apprise(**extra):
    return {
        "type": "apprise",
        "name": "Apprise",
        "config": {"urls": "tgram://111111:AAAA-token-a/1001"},
        **extra,
    }


def code_of(response):
    return response.json()["detail"]["code"]


def assert_no_secret(response):
    for value in SECRETS:
        assert value not in response.text


async def create(client, payload):
    response = await client.post("/api/notifications/channels", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ── Types ───────────────────────────────────────────────────────────────


def _kind(prop: dict) -> str:
    """The field kind of a property in the shared form subset, or the reason it is not."""
    options = [o for o in prop.get("anyOf", [prop]) if o.get("type") != "null"]
    assert len(options) == 1, prop
    option = options[0]
    if "$ref" in option or "properties" in option or option.get("type") in ("object", "array"):
        return "unsupported"
    if option.get("writeOnly"):
        return "secret"
    if "enum" in option:
        return "enum"
    return option["type"]


async def test_types_every_secret_field_is_write_only_and_kinds_stay_in_the_subset(test_db, client):
    response = await client.get("/api/notifications/types")
    assert response.status_code == 200
    all_types = {entry["type"]: entry for entry in response.json()}
    # The presets (tests/test_apprise_presets.py) ride on ``apprise``.
    types = {k: v for k, v in all_types.items() if not v["preset"]}
    assert set(types) == {"web_push", "email", "webhook", "apprise", "ntfy", "discord"}
    assert {k for k, v in all_types.items() if v["preset"]} == {
        "telegram",
        "gotify",
        "home_assistant",
        "pushover",
        "matrix",
        "signal",
        "slack",
        "mattermost",
        "rocketchat",
        "sms_free",
        "sms_sfr",
    }
    secret_names = {
        "email": {"password"},
        "webhook": {"url", "hmac_secret"},
        "apprise": {"urls"},
        "ntfy": {"topic", "token", "password"},
        "discord": {"webhook_url"},
    }
    for name, entry in types.items():
        assert entry["preset"] is False
        assert set(entry["default_limits"]) == {"per_min", "per_hour", "per_day"}
        assert isinstance(entry["cloud"], bool)
        assert isinstance(entry["quiet_hours_exempt_default"], bool)
        kinds = {key: _kind(prop) for key, prop in entry["schema"]["properties"].items()}
        assert "unsupported" not in kinds.values(), (name, kinds)
        assert {k for k, v in kinds.items() if v == "secret"} == secret_names.get(name, set())
        for key in secret_names.get(name, set()):
            assert entry["schema"]["properties"][key]["writeOnly"] is True


def test_types_schema_snapshot():
    """An unintended change of a schema fails here; refresh the fixture on purpose."""
    current = json.loads(json.dumps(types_schema(), sort_keys=True))
    # Regenerate on purpose with: uv run python -c "import json; from app.notifications.channels
    # import types_schema; print(json.dumps(types_schema(), indent=2, sort_keys=True))"
    assert current == json.loads(SNAPSHOT.read_text())


async def test_events_catalogue(test_db, client):
    body = (await client.get("/api/notifications/events")).json()
    ids = {e["id"]: e for e in body["events"]}
    assert body["contract_version"] == 1
    assert ids["radio.offline"]["urgent"] is True
    assert ids["notify.channel_changed"]["locked_channels"] == ["builtin:push"]
    assert ids["notify.test"]["configurable"] is False


# ── Channels: CRUD, redaction, keep and clear ───────────────────────────


async def test_the_default_database_has_the_builtin_push_channel(test_db, client):
    channels = (await client.get("/api/notifications/channels")).json()
    assert [c["id"] for c in channels] == ["builtin:push"]
    assert channels[0]["deletable"] is False
    assert channels[0]["status"]["status"] == "connected"


@pytest.mark.parametrize(
    ("payload", "secret_names"),
    [
        (webhook(), ["hmac_secret", "url"]),
        (email(), ["password"]),
        (apprise(), ["urls"]),
    ],
)
async def test_create_get_list_redact_secrets(test_db, client, payload, secret_names):
    created = await create(client, payload)
    assert_no_secret(await client.get("/api/notifications/channels"))
    assert created["secret_fields"] == secret_names
    for name in secret_names:
        assert created["config"][name] == "********"
    fetched = await client.get(f"/api/notifications/channels/{created['id']}")
    assert fetched.json() == created
    assert_no_secret(fetched)
    assert created["id"].startswith(payload["type"] + "-")
    assert created["limits"]["per_min"] is not None


async def test_create_returns_ids_and_defaults(test_db, client):
    hook = await create(client, webhook())
    assert hook["quiet_hours_exempt"] is True  # the registry default of a webhook
    assert hook["include_message_text"] == "always"
    mail = await create(client, email())
    assert mail["include_message_text"] == "public_only"  # a public SMTP relay is a third party
    assert mail["config"]["from"] == ""


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"type": "nope", "name": "x", "config": {}}, "channel_type_unknown"),
        (email(port=70000), "invalid_config"),
        (email(host=""), "config_required"),
        (email(unknown_field="x"), "invalid_config"),
        (webhook(config={"url": "ftp://example.org/x"}), "egress_scheme"),
        (webhook(config={"url": "http://127.0.0.1:9/x"}), "egress_private_address"),
        (webhook(config={"url": "http://192.168.1.20/x"}), "egress_private_address"),
        (webhook(config={}), "config_required"),
        (apprise(config={"urls": "file:///etc/passwd"}), None),
    ],
)
async def test_create_refuses_with_a_code(test_db, client, payload, code):
    response = await client.post("/api/notifications/channels", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == (code or detail["code"])
    assert set(detail) == {"code", "params"}
    assert "127.0.0.1" not in response.text and "192.168" not in response.text


async def test_a_validation_error_does_not_echo_a_secret(test_db, client):
    response = await client.post(
        "/api/notifications/channels", json=email(port="not-a-number", password="smtp-pass-1")
    )
    assert response.status_code == 400
    assert_no_secret(response)


async def test_patch_keeps_secrets_unless_cleared(test_db, client):
    created = await create(client, email())
    cid = created["id"]
    # Omitted, null and ******** keep the stored password.
    for config in ({"to": "a@example.org"}, {"password": None}, {"password": "********"}):
        response = await client.patch(f"/api/notifications/channels/{cid}", json={"config": config})
        assert response.status_code == 200, response.text
        assert response.json()["secret_fields"] == ["password"]
    # An empty string clears it.
    cleared = await client.patch(
        f"/api/notifications/channels/{cid}", json={"config": {"password": ""}}
    )
    assert cleared.json()["secret_fields"] == []
    assert cleared.json()["config"]["password"] == ""
    # A new value replaces it.
    replaced = await client.patch(
        f"/api/notifications/channels/{cid}", json={"config": {"password": "smtp-pass-2"}}
    )
    assert replaced.json()["secret_fields"] == ["password"]
    assert "smtp-pass-2" not in replaced.text


async def test_patch_refuses_a_kept_secret_that_would_follow_a_destination_change(test_db, client):
    created = await create(client, email())
    response = await client.patch(
        f"/api/notifications/channels/{created['id']}",
        json={"config": {"host": "other.example.org"}},
    )
    assert response.status_code == 400
    assert code_of(response) == "secret_required"


NTFY_TOPIC = "ntfy-topic-secret-1"


def ntfy(**extra):
    config = {"server": "https://ntfy.example.org", "topic": NTFY_TOPIC, "token": "tk_secret_1"}
    return {"type": "ntfy", "name": "Phone", "config": config, **extra}


async def test_ntfy_channel_never_shows_its_secrets(test_db, client):
    created = await create(client, ntfy())
    assert created["secret_fields"] == ["token", "topic"]
    assert created["config"]["topic"] == "********"
    assert created["config"]["token"] == "********"
    assert created["config"]["priority_default"] == 3 or "priority_default" not in created["config"]
    for response in (
        await client.get("/api/notifications/channels"),
        await client.get(f"/api/notifications/channels/{created['id']}"),
    ):
        assert NTFY_TOPIC not in response.text
        assert "tk_secret_1" not in response.text
    # A public self-hosted server: no daily cap, text always sent.
    assert created["limits"]["per_day"] is None
    assert created["include_message_text"] == "always"


async def test_ntfy_sh_defaults_to_a_daily_limit_and_public_only(test_db, client):
    created = await create(
        client, {"type": "ntfy", "name": "Hosted", "config": {"topic": NTFY_TOPIC}}
    )
    assert created["limits"]["per_day"] == 200
    assert created["include_message_text"] == "public_only"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            ntfy(config={"server": "http://192.168.1.20", "topic": NTFY_TOPIC}),
            "egress_private_address",
        ),
        (ntfy(config={"server": "https://ntfy.example.org"}), "config_required"),
        (ntfy(verify_tls=False), "verify_tls_requires_private_destination"),
    ],
)
async def test_ntfy_create_refusals(test_db, client, payload, code):
    response = await client.post("/api/notifications/channels", json=payload)
    assert response.status_code == 400
    assert code_of(response) == code
    assert NTFY_TOPIC not in response.text


async def test_ntfy_lan_server_with_the_flag_may_turn_tls_checks_off(test_db, client):
    created = await create(
        client,
        ntfy(
            config={"server": "https://192.168.1.20", "topic": NTFY_TOPIC},
            allow_private_network=True,
            verify_tls=False,
        ),
    )
    assert created["verify_tls"] is False


async def test_ntfy_invalid_topic_error_does_not_echo_it(test_db, client):
    response = await client.post(
        "/api/notifications/channels",
        json=ntfy(config={"server": "https://ntfy.example.org", "topic": "bad topic/with secret"}),
    )
    assert response.status_code in (400, 422)
    assert "with secret" not in response.text


async def test_ntfy_credentials_do_not_follow_a_new_server(test_db, client):
    created = await create(client, ntfy())
    response = await client.patch(
        f"/api/notifications/channels/{created['id']}",
        json={"config": {"server": "https://other.example.org"}},
    )
    assert response.status_code == 400
    assert code_of(response) == "secret_required"


async def test_patch_a_webhook_url_is_a_secret_destination(test_db, client):
    created = await create(client, webhook())
    kept = await client.patch(
        f"/api/notifications/channels/{created['id']}", json={"name": "Renamed"}
    )
    assert kept.json()["name"] == "Renamed"
    assert kept.json()["config"]["url"] == "********"
    private = await client.patch(
        f"/api/notifications/channels/{created['id']}",
        json={"config": {"url": "http://10.0.0.5/x", "hmac_secret": "n"}},
    )
    assert private.status_code == 400 and code_of(private) == "egress_private_address"


async def test_type_is_immutable_and_unknown_ids_are_missing(test_db, client):
    created = await create(client, webhook())
    response = await client.patch(
        f"/api/notifications/channels/{created['id']}", json={"type": "email"}
    )
    assert response.status_code == 400 and code_of(response) == "type_immutable"
    same = await client.patch(
        f"/api/notifications/channels/{created['id']}", json={"type": "webhook"}
    )
    assert same.status_code == 200
    for method in ("get", "patch", "delete"):
        kwargs = {"json": {"name": "x"}} if method == "patch" else {}
        response = await getattr(client, method)("/api/notifications/channels/nope-1", **kwargs)
        assert response.status_code == 404 and code_of(response) == "channel_missing"
    assert (await client.post("/api/notifications/channels/nope-1/test")).status_code == 404


async def test_delete_cascades_and_the_builtin_cannot_be_deleted(test_db, client):
    created = await create(client, webhook())
    rules = await client.patch(
        "/api/notifications/rules",
        json={"events": {"message.dm": {"channels": ["builtin:push", created["id"]]}}},
    )
    assert created["id"] in rules.json()["events"]["message.dm"]["channels"]
    deleted = await client.delete(f"/api/notifications/channels/{created['id']}")
    assert deleted.json() == {"deleted": True}
    after = (await client.get("/api/notifications/rules")).json()
    assert created["id"] not in after["events"]["message.dm"]["channels"]
    builtin = await client.delete("/api/notifications/channels/builtin:push")
    assert builtin.status_code == 403 and code_of(builtin) == "builtin_channel"


async def test_a_second_web_push_channel_is_refused(test_db, client):
    response = await client.post(
        "/api/notifications/channels", json={"type": "web_push", "name": "Push", "config": {}}
    )
    assert response.status_code == 409 and code_of(response) == "channel_exists"


# ── Test sends ──────────────────────────────────────────────────────────


@pytest.fixture
def webhook_result(monkeypatch):
    """Make the webhook transport answer ``SendResult(...)`` without any connection."""
    calls: list[tuple] = []
    answer = {"result": SendResult("ok")}

    async def send(self, channel, rendered, event, secrets):
        calls.append((channel.id, event.name, dict(secrets)))
        return answer["result"]

    monkeypatch.setattr("app.notifications.transports.webhook.WebhookTransport.send", send)
    return answer, calls


async def test_unsaved_test_persists_nothing_and_sends_through_the_transport(
    test_db, client, webhook_result
):
    answer, calls = webhook_result
    response = await client.post("/api/notifications/channels/test", json=webhook())
    assert response.status_code == 200 and response.json() == {"status": "sent"}
    assert calls and calls[0][1] == "notify.test"
    assert calls[0][2]["url"] == WEBHOOK_URL  # the transport got the secret, the answer does not
    assert [c["id"] for c in (await client.get("/api/notifications/channels")).json()] == [
        "builtin:push"
    ]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (webhook(config={"url": "http://127.0.0.1:9/x"}), "egress_private_address"),
        (webhook(config={}), "config_required"),
        (email(port=0), "invalid_config"),
    ],
)
async def test_unsaved_test_returns_the_codes_of_a_save(test_db, client, payload, code):
    tested = await client.post("/api/notifications/channels/test", json=payload)
    saved = await client.post("/api/notifications/channels", json=payload)
    assert tested.status_code == saved.status_code == 400
    assert code_of(tested) == code_of(saved) == code


async def test_saved_test_is_limited_to_one_per_five_seconds_per_channel(
    test_db, client, webhook_result
):
    first = await create(client, webhook())
    second = await create(client, webhook(name="Other"))
    ok = await client.post(f"/api/notifications/channels/{first['id']}/test")
    assert ok.status_code == 200
    limited = await client.post(f"/api/notifications/channels/{first['id']}/test")
    assert limited.status_code == 429 and code_of(limited) == "rate_limited"
    assert limited.headers["Retry-After"] == "5"
    # Another channel has its own budget; the budget comes back with time.
    assert (
        await client.post(f"/api/notifications/channels/{second['id']}/test")
    ).status_code == 200
    limiter = api_support._limiters[first["id"]]
    limiter._level = 0
    assert (await client.post(f"/api/notifications/channels/{first['id']}/test")).status_code == 200


@pytest.mark.parametrize(
    ("result", "status", "code"),
    [
        (SendResult("failed", error_code="http_401"), 502, "auth_failed"),
        (SendResult("failed", error_code="http_403"), 502, "auth_failed"),
        (SendResult("retry", error_code="service_error"), 502, "service_error"),
        (SendResult("failed", error_code="http_404"), 502, "service_error"),
        (SendResult("retry", error_code="timeout"), 504, "timeout"),
        (SendResult("failed", error_code="tls_error"), 502, "tls_error"),
        (SendResult("retry", error_code="rate_limited"), 429, "rate_limited"),
        (SendResult("failed", error_code="egress_private_address"), 400, "egress_private_address"),
        (SendResult("failed", error_code="not_configured"), 400, "invalid_config"),
        (SendResult("failed", error_code="connect_error"), 502, "service_error"),
        (SendResult("failed", error_code="line 1 (tgram://****): auth_failed"), 502, "auth_failed"),
        (
            SendResult("failed", error_code="weird text 10.0.0.1 hooks.example.org"),
            502,
            "service_error",
        ),
    ],
)
async def test_a_failed_test_is_coded_and_leaks_nothing(
    test_db, client, webhook_result, result, status, code
):
    answer, _calls = webhook_result
    created = await create(client, webhook())
    answer["result"] = result
    response = await client.post(f"/api/notifications/channels/{created['id']}/test")
    assert response.status_code == status
    assert response.json()["detail"] == {"code": code, "params": {}}
    for leaked in ("hooks.example.org", "10.0.0.1", "secret-token-1"):
        assert leaked not in response.text


async def test_a_channel_that_now_points_at_a_private_address_is_refused_at_test(
    test_db, client, monkeypatch
):
    created = await create(client, webhook())
    monkeypatch.setattr(WebhookTransport, "send", REAL_WEBHOOK_SEND)
    real = socket.getaddrinfo

    def private(host, port, *args, **kwargs):
        return real("10.1.2.3", port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", private)
    response = await client.post(f"/api/notifications/channels/{created['id']}/test")
    assert response.status_code == 400 and code_of(response) == "egress_private_address"
    assert "10.1.2.3" not in response.text and "hooks.example.org" not in response.text
    # The failure shows in the channel's status.
    listed = {c["id"]: c for c in (await client.get("/api/notifications/channels")).json()}
    assert listed[created["id"]]["status"]["status"] == "error"


# ── channel_changed ─────────────────────────────────────────────────────


async def test_create_emits_the_ws_event_the_audit_row_and_the_notification(
    test_db, client, traces
):
    created = await create(client, webhook())
    ws = next(t for t in traces if "ws" in t)
    assert ws["ws"] is WsEvent.NOTIFICATION_CHANNEL_CHANGED
    assert ws["data"] == {
        "id": created["id"],
        "action": "created",
        "type": "webhook",
        "name": "Hook",
    }
    assert ws["kwargs"] == {"realtime": False}
    emitted = next(t for t in traces if "emit" in t)
    assert emitted["emit"]["event"] == "notify.channel_changed"
    assert emitted["emit"]["action"] == "created"
    # builtin:push (it cannot be unchecked) and every other enabled channel by default; never
    # the channel that changed.
    assert emitted["targets"] == ["builtin:push"]
    assert "secret-token-1" not in json.dumps(emitted)
    audit = [
        e for e in await AuthAuditRepository.list_recent() if e.action == "notify.channel_changed"
    ]
    assert len(audit) == 1
    assert audit[0].target == f"notification_channel:{created['id']}"
    assert audit[0].details["action"] == "created"
    assert "secret-token-1" not in json.dumps(audit[0].details)


async def test_channel_changed_follows_destination_changes_and_enable_toggles_only(
    test_db, client, traces
):
    created = await create(client, webhook())
    cid = created["id"]
    traces.clear()
    # A rename, a limit and a credential change are not a new destination.
    for body in ({"name": "Renamed"}, {"rate_per_min": 5}, {"config": {"hmac_secret": "new"}}):
        assert (
            await client.patch(f"/api/notifications/channels/{cid}", json=body)
        ).status_code == 200
    assert traces == []
    # A new URL is.
    other = "https://hooks.example.org/services/other"
    await client.patch(
        f"/api/notifications/channels/{cid}", json={"config": {"url": other, "hmac_secret": "n"}}
    )
    assert [t["emit"]["action"] for t in traces if "emit" in t] == ["changed"]
    traces.clear()
    await client.patch(f"/api/notifications/channels/{cid}", json={"enabled": False})
    await client.patch(f"/api/notifications/channels/{cid}", json={"enabled": True})
    assert [t["emit"]["action"] for t in traces if "emit" in t] == ["disabled", "enabled"]


async def test_channel_changed_honours_the_rule_and_never_drops_the_push(test_db, client, traces):
    one = await create(client, webhook())
    two = await create(client, webhook(name="Two"))
    three = await create(client, webhook(name="Three"))
    traces.clear()
    await client.patch(f"/api/notifications/channels/{three['id']}", json={"enabled": False})
    assert next(t for t in traces if "emit" in t)["targets"] == [
        "builtin:push",
        one["id"],
        two["id"],
    ]
    patched = await client.patch(
        "/api/notifications/rules",
        json={"events": {"notify.channel_changed": {"channels": ["builtin:push", one["id"]]}}},
    )
    assert patched.status_code == 200
    traces.clear()
    await client.patch(f"/api/notifications/channels/{three['id']}", json={"enabled": True})
    assert next(t for t in traces if "emit" in t)["targets"] == ["builtin:push", one["id"]]
    refused = await client.patch(
        "/api/notifications/rules",
        json={"events": {"notify.channel_changed": {"channels": [one["id"]]}}},
    )
    assert refused.status_code == 403 and code_of(refused) == "builtin_channel"


async def test_delete_announces_without_the_deleted_channel(test_db, client, traces):
    created = await create(client, webhook())
    traces.clear()
    await client.delete(f"/api/notifications/channels/{created['id']}")
    emitted = next(t for t in traces if "emit" in t)
    assert emitted["emit"]["action"] == "deleted"
    assert created["id"] not in emitted["targets"]


def test_channel_changed_renders_in_both_languages():
    from app.notifications.render import build_push_payload

    payload = {
        "event": "notify.channel_changed",
        "action": "created",
        "channel_type": "webhook",
        "name": "Hook",
    }
    en = json.loads(build_push_payload(payload, "en"))
    fr = json.loads(build_push_payload(payload, "fr"))
    assert en["title"] == "Notification channel changed" and '"Hook" created' in en["body"]
    assert fr["title"] == "Canal de notification modifié" and "« Hook » créé" in fr["body"]


# ── Rules and the global pause ──────────────────────────────────────────


async def _rules(client) -> dict:
    return (await client.get("/api/notifications/rules")).json()


async def test_rules_read_and_validation(test_db, client):
    rules = (await client.get("/api/notifications/rules")).json()
    assert rules["paused_until"] is None
    assert rules["radio_offline_grace_s"] == 60
    assert rules["mention_whole_word"] is False
    assert rules["push_allow_private_endpoints"] is False
    assert rules["host_timezone"]
    assert "message.dm" in rules["events"]

    for body, status, code in (
        ({"events": {"message.dm": {"channels": ["ghost-1"]}}}, 400, "channel_missing"),
        ({"events": {"not.an.event": {"always_deliver": True}}}, 400, "invalid_config"),
        ({"events": {"notify.test": {"always_deliver": True}}}, 400, "invalid_config"),
        ({"events": {"message.dm": {"contact_types": {"companion": []}}}}, 400, "invalid_config"),
        ({"paused_until": int(time.time()) + 400 * 86400}, 400, "invalid_config"),
    ):
        response = await client.patch("/api/notifications/rules", json=body)
        assert (response.status_code, code_of(response)) == (status, code), body
    assert (
        await client.patch("/api/notifications/rules", json={"radio_offline_grace_s": 99999})
    ).status_code == 422


async def test_rules_patch_events_and_settings(test_db, client):
    hook = await create(client, webhook())
    repeater = (await _rules(client))["events"]["contact.first_seen"]["contact_types"]["repeater"]
    patched = await client.patch(
        "/api/notifications/rules",
        json={
            "events": {
                "message.dm": {"channels": ["builtin:push", hook["id"], hook["id"]]},
                "radio.offline": {"always_deliver": True},
                "contact.first_seen": {"contact_types": {"repeater": [*repeater, hook["id"]]}},
            },
            "radio_offline_grace_s": 120,
            "mention_whole_word": True,
            "push_allow_private_endpoints": True,
        },
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["events"]["message.dm"]["channels"] == ["builtin:push", hook["id"]]
    assert body["events"]["radio.offline"]["always_deliver"] is True
    assert body["events"]["contact.first_seen"]["contact_types"]["repeater"] == [
        *repeater,
        hook["id"],
    ]
    assert (body["radio_offline_grace_s"], body["mention_whole_word"]) == (120, True)
    assert body["push_allow_private_endpoints"] is True


async def test_conversation_exception_put_and_clear(test_db, client):
    hook = await create(client, webhook())
    key = "contact-" + "ab" * 32
    put = await client.put(
        f"/api/notifications/rules/conversations/{key}",
        json={"channels": {hook["id"]: True}},
    )
    assert put.status_code == 200
    assert put.json()["conversations"][key] == {hook["id"]: True}
    cleared = await client.put(
        f"/api/notifications/rules/conversations/{key}", json={"channels": None}
    )
    assert key not in cleared.json()["conversations"]
    ghost = await client.put(
        f"/api/notifications/rules/conversations/{key}", json={"channels": {"ghost-1": True}}
    )
    assert ghost.status_code == 400 and code_of(ghost) == "channel_missing"
    bad_key = await client.put(
        "/api/notifications/rules/conversations/not a key", json={"channels": None}
    )
    assert bad_key.status_code == 400 and code_of(bad_key) == "invalid_config"


async def test_pause_suppresses_a_dm_and_lets_radio_offline_through(test_db, client, monkeypatch):
    from app.notifications.dispatcher import Dispatcher
    from app.notifications.events import event_from_payload

    sent: list[str] = []

    async def send(self, channel, rendered, event, secrets):
        sent.append(event.name)
        return SendResult("ok")

    monkeypatch.setattr("app.notifications.transports.web_push.WebPushTransport.send", send)
    ok = await client.patch(
        "/api/notifications/rules",
        json={
            "events": {
                "message.dm": {"channels": ["builtin:push"]},
                "radio.offline": {"channels": ["builtin:push"]},
            },
            "paused_until": int(time.time()) + 3600,
        },
    )
    assert ok.status_code == 200 and ok.json()["paused_until"] is not None
    dispatcher = Dispatcher()
    dm = event_from_payload(
        {"type": "PRIV", "conversation_key": "ab" * 32, "text": "hi", "sender_name": "Al"}
    )
    offline = event_from_payload({"event": "radio_offline", "urgent": True})
    assert dm is not None and offline is not None
    await dispatcher.emit(dm)
    await dispatcher.emit(offline)
    await dispatcher.wait_idle()
    assert sent == ["radio.offline"]
    journal = (await client.get("/api/notifications/deliveries?channel=builtin:push")).json()
    assert [(r["event"], r["status"]) for r in journal["deliveries"]] == [("message.dm", "paused")]
    # Resuming lets a DM through again.
    resumed = await client.patch("/api/notifications/rules", json={"paused_until": None})
    assert resumed.json()["paused_until"] is None
    sent.clear()
    await dispatcher.emit(dm)
    await dispatcher.wait_idle()
    assert sent == ["message.dm"]


# ── Journal ─────────────────────────────────────────────────────────────


async def test_deliveries_filter_limit_and_counters(test_db, client):
    from app.repository.notification_channels import NotificationJournalRepository as Journal

    hook = await create(client, webhook())
    for i in range(3):
        await Journal.add_delivery(
            hook["id"], "message.dm", status="failed", dedup_key=f"k{i}", error="x"
        )
    await Journal.add_delivery(
        "builtin:push", "radio.offline", status="failed", dedup_key="z", error="x"
    )
    await Journal.add_sent(hook["id"], int(time.time()) // 3600, 4)
    body = (await client.get(f"/api/notifications/deliveries?channel={hook['id']}&limit=2")).json()
    assert len(body["deliveries"]) == 2
    assert {r["channel_id"] for r in body["deliveries"]} == {hook["id"]}
    assert body["counters"] == [
        {"channel_id": hook["id"], "hour": int(time.time()) // 3600, "sent": 4}
    ]
    everything = (await client.get("/api/notifications/deliveries")).json()
    assert len(everything["deliveries"]) == 4
    assert (await client.get("/api/notifications/deliveries?channel=ghost")).status_code == 404
    assert (await client.get("/api/notifications/deliveries?limit=0")).status_code == 422


async def test_deliveries_totals_are_exact_beyond_the_ring(test_db, client):
    """Held and grouped counts come from per-hour counters, not from the 200 ring rows."""
    from app.repository.notification_channels import NotificationJournalRepository as Journal

    hook = await create(client, webhook())
    other = await create(client, webhook())
    rows = [{"channel_id": hook["id"], "event": "message.dm", "status": "suppressed_quiet"}] * 250
    rows += [{"channel_id": hook["id"], "event": "message.dm", "status": "aggregated"}] * 3
    rows += [{"channel_id": hook["id"], "event": "message.dm", "status": "failed"}]
    rows += [{"channel_id": other["id"], "event": "message.dm", "status": "aggregated"}]
    for start in range(0, len(rows), 50):
        await Journal.add_deliveries(rows[start : start + 50])
    await Journal.add_sent(hook["id"], int(time.time()) // 3600, 4)
    body = (await client.get(f"/api/notifications/deliveries?channel={hook['id']}")).json()
    assert len(body["deliveries"]) == 50
    assert body["totals"] == {"sent": 4, "suppressed_quiet": 250, "aggregated": 3}
    everything = (await client.get("/api/notifications/deliveries")).json()
    assert everything["totals"] == {"sent": 4, "suppressed_quiet": 250, "aggregated": 4}
    # An older hour leaves the 24 h window; deleting the channel drops its counters.
    old = await Journal.window_totals(hook["id"], now=int(time.time()) + 25 * 3600)
    assert old == {"sent": 0, "suppressed_quiet": 0, "aggregated": 0}
    assert (await client.delete(f"/api/notifications/channels/{hook['id']}")).status_code in (
        200,
        204,
    )
    everything = (await client.get("/api/notifications/deliveries")).json()
    assert everything["totals"]["suppressed_quiet"] == 0
    assert everything["totals"]["aggregated"] == 1


# ── Access ──────────────────────────────────────────────────────────────


async def test_every_notifications_route_is_admin_only(test_db, client, sign_in_as):
    routes = [
        ("get", "/api/notifications/types", None),
        ("get", "/api/notifications/events", None),
        ("get", "/api/notifications/channels", None),
        ("post", "/api/notifications/channels", webhook()),
        ("post", "/api/notifications/channels/test", webhook()),
        ("get", "/api/notifications/channels/builtin:push", None),
        ("patch", "/api/notifications/channels/builtin:push", {"name": "x"}),
        ("delete", "/api/notifications/channels/builtin:push", None),
        ("post", "/api/notifications/channels/builtin:push/test", None),
        ("get", "/api/notifications/rules", None),
        ("patch", "/api/notifications/rules", {}),
        ("put", "/api/notifications/rules/conversations/contact-ab", {"channels": None}),
        ("get", "/api/notifications/deliveries", None),
    ]
    for role in ("member", "viewer", "kiosk"):
        sign_in_as(role)
        for method, path, body in routes:
            kwargs = {"json": body} if body is not None else {}
            response = await getattr(client, method)(path, **kwargs)
            assert response.status_code == 403, (role, method, path)
    sign_in_as("admin")
    assert (await client.get("/api/notifications/channels")).status_code == 200


# ── The rules decide; the deprecated preferences follow ─────────────────


async def _preferences(client) -> dict:
    response = await client.get("/api/push/preferences")
    assert response.status_code == 200
    return response.json()


async def test_the_rules_decide_and_the_legacy_preferences_follow(test_db, client):
    response = await client.patch(
        "/api/notifications/rules", json={"events": {"message.dm": {"channels": []}}}
    )
    assert response.status_code == 200
    assert (await _preferences(client))["defaults"]["new_dm"]["push"] is False

    before = await _rules(client)
    current = before["events"]["contact.first_seen"]["contact_types"]["repeater"]
    flipped = [] if "builtin:push" in current else ["builtin:push"]
    ok = await client.patch(
        "/api/notifications/rules",
        json={"events": {"contact.first_seen": {"contact_types": {"repeater": flipped}}}},
    )
    assert ok.status_code == 200
    assert (await _preferences(client))["defaults"]["advert_repeater"]["push"] == bool(flipped)

    key = "contact-" + "ef" * 32
    conversation = await client.put(
        f"/api/notifications/rules/conversations/{key}", json={"channels": {"builtin:push": False}}
    )
    assert conversation.status_code == 200
    assert (await _preferences(client))["overrides"][key] == {"push": False}
    cleared = await client.put(
        f"/api/notifications/rules/conversations/{key}", json={"channels": None}
    )
    assert cleared.status_code == 200
    assert key not in (await _preferences(client))["overrides"]


async def test_media_without_a_channel_keep_their_legacy_flags(test_db, client):
    from app.repository.settings import AppSettingsRepository

    """The reviewer's case: ``new_dm.email`` is on but no SMTP is set yet, so the rules cannot
    hold ``legacy:email``. A change through the new API must not wipe the 4.18.x flag."""
    key = "contact-" + "ab" * 32
    await AppSettingsRepository.set_push_defaults({"new_dm": {"push": True, "email": True}})
    await AppSettingsRepository.set_push_conversation_override(key, {"email": True})
    assert (await _preferences(client))["defaults"]["new_dm"]["email"] is True
    ok = await client.patch(
        "/api/notifications/rules", json={"events": {"message.dm": {"always_deliver": True}}}
    )
    assert ok.status_code == 200
    other = await client.put(
        "/api/notifications/rules/conversations/contact-" + "cd" * 32,
        json={"channels": {"builtin:push": False}},
    )
    assert other.status_code == 200
    prefs = await _preferences(client)
    assert prefs["defaults"]["new_dm"]["email"] is True
    assert prefs["overrides"][key] == {"email": True}
    # The destination arrives: the kept flag becomes a rule again.
    patched = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"email": {"host": "smtp.example.org", "to": "a@b.c"}}},
    )
    assert patched.status_code == 200
    rules = await _rules(client)
    assert "legacy:email" in rules["events"]["message.dm"]["channels"]
    assert rules["conversations"][key] == {"legacy:email": True}


async def test_a_rule_on_a_new_channel_stays_out_of_the_legacy_preferences(test_db, client):
    hook = await create(client, webhook())
    ok = await client.patch(
        "/api/notifications/rules",
        json={"events": {"message.dm": {"channels": ["builtin:push", hook["id"]]}}},
    )
    assert ok.status_code == 200
    assert (await _preferences(client))["defaults"]["new_dm"] == {
        "push": True,
        "email": False,
        "webhook": False,
        "apprise": False,
    }
    # The legacy write that follows keeps the new channel in the rules.
    await client.patch("/api/push/preferences", json={"defaults": {"new_dm": {"push": True}}})
    assert (await _rules(client))["events"]["message.dm"]["channels"] == [
        "builtin:push",
        hook["id"],
    ]


async def test_legacy_channels_destinations_are_set_through_the_settings(test_db, client):
    patched = await client.patch(
        "/api/settings",
        json={"notification_destinations": {"email": {"host": "smtp.example.org", "to": "a@b.c"}}},
    )
    assert patched.status_code == 200
    url = "/api/notifications/channels/legacy:email"
    for body in ({"config": {"host": "other.example.org"}}, {"allow_private_network": True}):
        response = await client.patch(url, json=body)
        assert (response.status_code, code_of(response)) == (409, "legacy_managed")
    assert (await client.delete(url)).status_code == 409
    assert (await client.patch(url, json={"name": "Mail"})).status_code == 200


# ── Audit of the rules ──────────────────────────────────────────────────


async def test_rules_changes_and_the_global_pause_are_audited(test_db, client):
    until = int(time.time()) + 3600
    await client.patch("/api/notifications/rules", json={"paused_until": until})
    await client.patch("/api/notifications/rules", json={"mention_whole_word": True})
    hook = await create(client, webhook())
    await client.put(
        "/api/notifications/rules/conversations/contact-ab", json={"channels": {hook["id"]: True}}
    )
    rows = [
        e for e in await AuthAuditRepository.list_recent() if e.action == "notify.rules_changed"
    ][::-1]
    assert [r.details["fields"] for r in rows] == [
        ["paused_until"],
        ["mention_whole_word"],
        ["conversations"],
    ]
    assert rows[0].details["paused_until"] == until
    assert rows[0].target == "notification_rules"
    # A refused change writes nothing.
    await client.patch(
        "/api/notifications/rules", json={"events": {"message.dm": {"channels": ["nope"]}}}
    )
    assert (
        len(
            [
                e
                for e in await AuthAuditRepository.list_recent()
                if e.action == "notify.rules_changed"
            ]
        )
        == 3
    )


# ── Test limits ─────────────────────────────────────────────────────────


async def test_the_draft_limit_comes_before_any_dns_resolution(test_db, client, monkeypatch):
    lookups: list[str] = []
    real = socket.getaddrinfo

    def counting(host, *args, **kwargs):
        lookups.append(host)
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", counting)
    assert (
        await client.post("/api/notifications/channels/test", json=webhook())
    ).status_code == 200
    seen = len(lookups)
    assert seen
    limited = await client.post("/api/notifications/channels/test", json=webhook())
    assert limited.status_code == 429 and code_of(limited) == "rate_limited"
    assert len(lookups) == seen  # the second call resolved nothing


async def test_all_test_sends_share_a_small_budget(test_db, client):
    channels = [
        await create(client, webhook(name=f"H{i}")) for i in range(api_support.GLOBAL_TESTS + 1)
    ]
    statuses = [
        (await client.post(f"/api/notifications/channels/{c['id']}/test")).status_code
        for c in channels
    ]
    assert statuses == [200] * api_support.GLOBAL_TESTS + [429]


# ── Apprise errors never carry the host of the secret URLs ──────────────


async def test_an_apprise_failure_stores_the_line_and_the_code_only(test_db, client, monkeypatch):
    async def send(self, channel, rendered, event, secrets):
        return SendResult("failed", error_code="line 1: service_error")

    monkeypatch.setattr("app.notifications.transports.apprise.AppriseTransport.send", send)
    created = await create(
        client, apprise(config={"urls": "json://93.184.216.34:8080/hook-secret-9"})
    )
    response = await client.post(f"/api/notifications/channels/{created['id']}/test")
    assert response.status_code == 502
    listed = (await client.get(f"/api/notifications/channels/{created['id']}")).json()
    assert listed["status"]["last_error"] == "line 1: service_error"
    assert "93.184.216.34" not in json.dumps(listed)


async def test_webhook_body_type_is_v1_or_v2(test_db, client):
    """MSHL-137: an unknown body format is refused on create and on update, like any other
    invalid config value (400 ``invalid_config`` naming the field, not FastAPI's 422: the
    config is a free dict at the route and validated by the type's model)."""
    payload = webhook()
    payload["config"]["body_type"] = "v3"
    response = await client.post("/api/notifications/channels", json=payload)
    assert (response.status_code, code_of(response)) == (400, "invalid_config")
    assert "body_type" in response.text
    created = await create(client, webhook())
    assert created["config"]["body_type"] == "v2"
    response = await client.patch(
        f"/api/notifications/channels/{created['id']}", json={"config": {"body_type": "v3"}}
    )
    assert (response.status_code, code_of(response)) == (400, "invalid_config")
    assert "body_type" in response.text
    response = await client.patch(
        f"/api/notifications/channels/{created['id']}", json={"config": {"body_type": "v1"}}
    )
    assert response.status_code == 200, response.text
    assert response.json()["config"]["body_type"] == "v1"


# ── Secrets at rest (MSHL-133) ──────────────────────────────────────────


@pytest.fixture
def other_key(tmp_path):
    """Swap the secrets key, as a database copied without its secrets.key would see it."""
    from app import secret_store
    from app.repository import notification_channels

    def swap():
        secret_store.reset()
        assert secret_store.ensure_key(tmp_path / "other" / secret_store.KEY_FILENAME)
        notification_channels.invalidate_cache()

    yield swap
    secret_store.reset()
    notification_channels.invalidate_cache()


async def _raw_secrets(test_db, channel_id):
    async with test_db.tx() as conn:
        async with conn.execute(
            "SELECT secrets FROM notification_channels WHERE id = ?", (channel_id,)
        ) as cursor:
            return (await cursor.fetchone())[0]


async def test_channel_secrets_are_stored_encrypted(test_db, client):
    from app import secret_store
    from app.repository.notification_channels import NotificationChannelRepository

    created = await create(client, webhook())
    raw = await _raw_secrets(test_db, created["id"])
    assert WEBHOOK_URL not in raw and "hmac-secret-1" not in raw
    assert all(secret_store.is_encrypted(v) for v in json.loads(raw).values())
    _channel, secrets = await NotificationChannelRepository.get_for_send(created["id"])
    assert secrets == {"url": WEBHOOK_URL, "hmac_secret": "hmac-secret-1"}


async def test_a_legacy_plain_secret_is_read_and_encrypted_on_the_next_write(test_db, client):
    from app import secret_store
    from app.repository import notification_channels

    created = await create(client, email())
    cid = created["id"]
    async with test_db.tx() as conn:
        await conn.execute(
            "UPDATE notification_channels SET secrets = ? WHERE id = ?",
            (json.dumps({"password": "smtp-pass-1"}), cid),
        )
    notification_channels.invalidate_cache()
    fetched = (await client.get(f"/api/notifications/channels/{cid}")).json()
    assert fetched["secret_fields"] == ["password"] and fetched["enabled"] is True
    found = await notification_channels.NotificationChannelRepository.get_for_send(cid)
    assert found[1] == {"password": "smtp-pass-1"}

    patched = await client.patch(
        f"/api/notifications/channels/{cid}", json={"config": {"to": "b@example.org"}}
    )
    assert patched.status_code == 200, patched.text
    stored = json.loads(await _raw_secrets(test_db, cid))["password"]
    assert secret_store.is_encrypted(stored)
    aad = secret_store.aad("notification_channels", cid, "password")
    assert secret_store.decrypt(stored, aad) == "smtp-pass-1"


async def test_an_unreadable_secret_turns_the_channel_off_until_it_is_typed_again(
    test_db, client, other_key, caplog
):
    created = await create(client, webhook())
    cid = created["id"]
    other_key()

    listed = {c["id"]: c for c in (await client.get("/api/notifications/channels")).json()}
    channel = listed[cid]
    assert channel["enabled"] is False
    assert channel["status"]["last_error"] == "secret_unreadable"
    assert channel["secret_fields"] == []

    # A PATCH that does not touch the secrets is saved, but the channel stays off.
    enable = await client.patch(f"/api/notifications/channels/{cid}", json={"enabled": True})
    assert enable.status_code == 200, enable.text
    assert enable.json()["enabled"] is False
    assert enable.json()["status"]["last_error"] == "secret_unreadable"
    tested = await client.post(f"/api/notifications/channels/{cid}/test")
    assert tested.status_code == 409 and code_of(tested) == "secret_unreadable"
    renamed = await client.patch(f"/api/notifications/channels/{cid}", json={"name": "Renamed"})
    assert renamed.status_code == 200 and renamed.json()["enabled"] is False

    saved = await client.patch(
        f"/api/notifications/channels/{cid}",
        json={"config": {"url": WEBHOOK_URL, "hmac_secret": "hmac-secret-1"}},
    )
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["enabled"] is True and body["secret_fields"] == ["hmac_secret", "url"]
    assert body["status"]["last_error"] != "secret_unreadable"
    from app.repository.notification_channels import NotificationChannelRepository

    _channel, secrets = await NotificationChannelRepository.get_for_send(cid)
    assert secrets["url"] == WEBHOOK_URL  # encrypted again with the new key
    for response in (enable, tested, renamed, saved):
        assert_no_secret(response)
    assert all(value not in caplog.text for value in SECRETS)


async def test_the_matching_key_brings_an_unreadable_channel_back(test_db, client, other_key):
    from app import secret_store
    from app.repository import notification_channels

    created = await create(client, webhook())
    other_key()
    assert (await client.get(f"/api/notifications/channels/{created['id']}")).json()[
        "enabled"
    ] is False
    secret_store.reset()  # the database's own secrets.key again
    notification_channels.invalidate_cache()
    back = (await client.get(f"/api/notifications/channels/{created['id']}")).json()
    assert back["enabled"] is True and back["secret_fields"] == ["hmac_secret", "url"]


def _lose_the_key(tmp_path, monkeypatch):
    """No usable secrets key: its folder is a file, so the key can be neither read nor created."""
    from app import secret_store

    blocker = tmp_path / "not-a-folder"
    blocker.write_text("")
    monkeypatch.setattr(secret_store, "key_path", lambda: blocker / secret_store.KEY_FILENAME)
    secret_store.reset()
    assert secret_store.ensure_key() is False


async def test_without_a_key_a_secret_is_never_stored_in_plain_text(
    test_db, client, tmp_path, monkeypatch, caplog
):
    from app import secret_store
    from app.repository import notification_channels

    existing = await create(client, email())
    before = await _raw_secrets(test_db, existing["id"])
    key_path = secret_store.key_path
    _lose_the_key(tmp_path, monkeypatch)
    try:
        notification_channels.invalidate_cache()
        created = await client.post("/api/notifications/channels", json=webhook())
        assert created.status_code == 503 and code_of(created) == "secret_store_unavailable"
        assert_no_secret(created)

        patched = await client.patch(
            f"/api/notifications/channels/{existing['id']}",
            json={"config": {"password": "new-smtp-pass-1"}},
        )
        assert patched.status_code == 503 and code_of(patched) == "secret_store_unavailable"
        assert "new-smtp-pass-1" not in patched.text
    finally:
        monkeypatch.setattr(secret_store, "key_path", key_path)
        secret_store.reset()

    async with test_db.tx() as conn:
        async with conn.execute("SELECT id, secrets FROM notification_channels") as cursor:
            rows = dict(await cursor.fetchall())
    assert set(rows) == {"builtin:push", existing["id"]}  # the webhook was not created
    assert rows[existing["id"]] == before  # nor the typed password stored
    dump = json.dumps(rows)
    assert WEBHOOK_URL not in dump and "new-smtp-pass-1" not in dump
    assert "new-smtp-pass-1" not in caplog.text and WEBHOOK_URL not in caplog.text
