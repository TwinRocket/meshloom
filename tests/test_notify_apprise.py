"""Apprise as the fourth notification medium (notifications phase 0, MSHL-104).

The transport runs with ``_send_sync`` mocked (one call per URL object), except
for one test that goes through a local HTTP server to check that the real
``requests`` answer reaches the retry policy.
"""

from __future__ import annotations

import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import AsyncMock

import pytest

import app.notify as notify
from app.data.meshcore_channels import hashtag_key_from_name
from app.models import NotificationAppriseDest, NotificationDestinations
from app.notifications import apprise_medium
from app.notifications.apprise_medium import AppriseQueue, AppriseSendFailed, _Response
from app.push.manager import event_notification_text
from app.repository.settings import AppSettingsRepository

TG_A = "tgram://111111:AAAA-token-a/1001"
TG_B = "tgram://222222:BBBB-token-b/2002"
DM_KEY = "aa" * 32


@pytest.fixture(autouse=True)
def _reset_apprise_state(monkeypatch):
    monkeypatch.setattr(notify, "_apprise_queue", None)
    monkeypatch.setattr(notify, "_apprise_last_error", None)
    monkeypatch.setattr(
        "app.email_template.resolve_instance_language", AsyncMock(return_value="en")
    )


@pytest.fixture
def sleeps(monkeypatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        recorded.append(seconds)

    monkeypatch.setattr(apprise_medium, "_sleep", fake_sleep)
    return recorded


def _fake_sends(monkeypatch, answers: dict[str, list[_Response | None]]) -> list[tuple]:
    """``_send_sync`` answering per chat id: ``None`` is a success."""
    calls: list[tuple] = []

    def fake(server, title, body, markdown):
        chat = str(server.targets[0][0])
        calls.append((chat, title, body, markdown))
        queue = answers.get(chat) or [None]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        return (answer is None, answer or _Response(status=200))

    monkeypatch.setattr(apprise_medium, "_send_sync", fake)
    return calls


def _dest(urls: str, **kwargs) -> NotificationDestinations:
    return NotificationDestinations(apprise=NotificationAppriseDest(urls=urls, **kwargs))


def _dm(text: str = "secret plans", sender: str = "Léa") -> dict:
    return {
        "type": "PRIV",
        "conversation_key": DM_KEY,
        "sender_name": sender,
        "text": text,
        "outgoing": False,
    }


def _hashtag_message(text: str = "hello mesh") -> dict:
    return {
        "type": "CHAN",
        "conversation_key": hashtag_key_from_name("#test").hex().upper(),
        "channel_name": "#test",
        "sender_name": "Bob",
        "text": text,
        "outgoing": False,
    }


# -- transport -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_object_per_url_and_only_the_failed_url_is_retried(monkeypatch, sleeps):
    calls = _fake_sends(monkeypatch, {"2002": [_Response(status=503), _Response(status=503), None]})
    await notify.send_apprise_alert(_dest(f"{TG_A}\n{TG_B}"), _dm())
    assert [chat for chat, *_ in calls] == ["1001", "2002", "2002", "2002"]
    assert sleeps == [5.0, 30.0]
    assert notify.apprise_last_error() is None


@pytest.mark.asyncio
async def test_no_retry_on_401(monkeypatch, sleeps):
    calls = _fake_sends(monkeypatch, {"1001": [_Response(status=401)]})
    with pytest.raises(AppriseSendFailed) as exc:
        await notify.send_apprise_alert(_dest(TG_A), _dm())
    assert exc.value.code == "service_rejected"
    assert exc.value.status == 401
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_network_error_is_retried_then_reported(monkeypatch, sleeps):
    calls = _fake_sends(monkeypatch, {"1001": [_Response(network_error=True)]})
    with pytest.raises(AppriseSendFailed) as exc:
        await notify.send_apprise_alert(_dest(TG_A), _dm())
    assert exc.value.code == "network_error"
    assert len(calls) == 3
    assert sleeps == [5.0, 30.0]


@pytest.mark.asyncio
async def test_retry_after_is_respected(monkeypatch, sleeps):
    _fake_sends(monkeypatch, {"1001": [_Response(status=429, retry_after="7"), None]})
    await notify.send_apprise_alert(_dest(TG_A), _dm())
    assert sleeps == [7.0]


@pytest.mark.asyncio
async def test_last_error_is_scrubbed(monkeypatch, sleeps):
    _fake_sends(monkeypatch, {"1001": [_Response(status=500)]})
    with pytest.raises(AppriseSendFailed):
        await notify.send_apprise_alert(_dest(TG_A), _dm())
    last = notify.apprise_last_error()
    assert last is not None
    assert last["code"] == "service_error"
    assert last["line"] == 1
    assert last["url"] == "tgram://****"
    assert "AAAA" not in str(last) and "111111" not in str(last)
    assert notify.notification_statuses()["apprise"]["last_error"] == last


@pytest.mark.asyncio
async def test_refused_url_does_not_stop_the_others(monkeypatch, sleeps):
    calls = _fake_sends(monkeypatch, {})
    with pytest.raises(Exception) as exc:
        await notify.send_apprise_alert(_dest(f"dbus://\n{TG_B}"), _dm())
    assert getattr(exc.value, "code", "") == "egress_scheme"
    assert [chat for chat, *_ in calls] == ["2002"]


class _Handler(BaseHTTPRequestHandler):
    answers: list[tuple[int, dict[str, str]]] = []
    hits = 0

    def do_POST(self):  # noqa: N802
        type(self).hits += 1
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        status, headers = type(self).answers.pop(0) if type(self).answers else (200, {})
        self.send_response(status)
        for key, value in headers.items():
            self.send_header(key, value)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def http_server():
    _Handler.answers = []
    _Handler.hits = 0
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_real_http_answer_drives_the_retry_policy(http_server, sleeps):
    port = http_server.server_address[1]
    _Handler.answers = [(429, {"Retry-After": "3"}), (200, {})]
    dest = _dest(f"json://127.0.0.1:{port}/hook", allow_private_network=True)
    await notify.send_apprise_alert(dest, _dm())
    assert _Handler.hits == 2
    assert sleeps == [3.0]

    _Handler.answers = [(401, {})]
    _Handler.hits = 0
    with pytest.raises(AppriseSendFailed) as exc:
        await notify.send_apprise_alert(dest, _dm())
    assert exc.value.code == "service_rejected"
    assert _Handler.hits == 1


# -- limiter -------------------------------------------------------------------


async def _drain(queue: AppriseQueue, delivered: list, expected: int) -> None:
    for _ in range(500):
        if len(delivered) >= expected:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_limiter_collapses_a_burst_of_50_into_20_plus_one():
    delivered: list[dict] = []

    async def deliver(payload):
        delivered.append(payload)

    queue = AppriseQueue(deliver, min_interval_s=0.001)
    for i in range(50):
        await queue.submit({"event": "message", "n": i})
    await _drain(queue, delivered, 21)
    assert len(delivered) == 21
    assert [p["n"] for p in delivered[:20]] == list(range(20))
    assert delivered[20] == {"event": "notifications_grouped", "count": 30}


@pytest.mark.asyncio
async def test_first_seen_is_capped_then_grouped():
    delivered: list[dict] = []

    async def deliver(payload):
        delivered.append(payload)

    queue = AppriseQueue(
        deliver, min_interval_s=0.001, first_seen_per_hour=10, first_seen_period_s=0.5
    )
    for i in range(15):
        await queue.submit({"event": "first_seen", "n": i})
    await _drain(queue, delivered, 11)
    assert [p.get("n") for p in delivered[:10]] == list(range(10))
    assert delivered[10] == {"event": "first_seen_grouped", "count": 5}


@pytest.mark.asyncio
async def test_limiter_spaces_sends():
    stamps: list[float] = []

    async def deliver(_payload):
        stamps.append(asyncio.get_running_loop().time())

    queue = AppriseQueue(deliver, min_interval_s=0.1)
    for i in range(3):
        await queue.submit({"event": "message", "n": i})
    for _ in range(100):
        if len(stamps) == 3:
            break
        await asyncio.sleep(0.02)
    assert len(stamps) == 3
    assert stamps[2] - stamps[0] >= 0.18


# -- message text policy -------------------------------------------------------


def test_public_only_hides_dm_text_and_keeps_hashtag_text():
    title, body = event_notification_text(_dm(), "en", include_message_text="public_only")
    assert "secret plans" not in title + body
    assert body == "Message from Léa"
    title, body = event_notification_text(
        _hashtag_message(), "en", include_message_text="public_only"
    )
    assert "hello mesh" in body


def test_public_only_hides_a_private_hash_channel():
    message = {**_hashtag_message(), "conversation_key": "DD" * 16}
    _title, body = event_notification_text(message, "en", include_message_text="public_only")
    assert "hello mesh" not in body


def test_never_strips_both():
    _title, body = event_notification_text(_dm(), "fr", include_message_text="never")
    assert body == "Message de Léa"
    _title, body = event_notification_text(_hashtag_message(), "en", include_message_text="never")
    assert "hello mesh" not in body


def test_markdown_escapes_and_neutralises_mentions():
    message = _hashtag_message("@everyone [click](https://evil.example) <@123> @here")
    _title, body = event_notification_text(
        message, "en", include_message_text="always", markdown=True
    )
    assert "@everyone" not in body and "@here" not in body and "<@1" not in body
    assert "](" not in body
    assert "\\[click\\]" in body


def test_mentions_are_neutralised_in_text_format_too():
    _title, body = event_notification_text(_hashtag_message("@everyone hi"), "en")
    assert "@everyone" not in body


@pytest.mark.asyncio
async def test_dm_to_a_mocked_url_carries_no_message_text(monkeypatch, sleeps):
    calls = _fake_sends(monkeypatch, {})
    await notify.send_apprise_alert(_dest(TG_A), _dm())
    await notify.send_apprise_alert(_dest(TG_A), _hashtag_message())
    (_, dm_title, dm_body, _), (_, chan_title, chan_body, _) = calls
    assert "secret plans" not in dm_title + dm_body
    assert "hello mesh" in chan_body

    calls.clear()
    await notify.send_apprise_alert(_dest(TG_A, include_message_text="never"), _hashtag_message())
    assert "hello mesh" not in calls[0][1] + calls[0][2]


# -- dispatch ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dispatch_enqueues_only_with_urls_and_flag(test_db, monkeypatch):
    submitted: list[dict] = []

    class _Queue:
        async def submit(self, payload):
            submitted.append(dict(payload))

    monkeypatch.setattr(notify, "apprise_queue", lambda: _Queue())
    await notify.dispatch_system_event({"event": "app_started"}, {"apprise": True})
    assert submitted == []  # no URL configured

    await AppSettingsRepository.update(notification_destinations=_dest(TG_A))
    await notify.dispatch_system_event({"event": "app_started"}, {"apprise": False})
    assert submitted == []
    await notify.dispatch_system_event({"event": "app_started"}, {"apprise": True})
    assert submitted == [{"event": "app_started"}]


@pytest.mark.asyncio
async def test_dm_matrix_apprise_flag_reaches_the_queue(test_db, monkeypatch):
    from app.push.manager import push_manager

    submitted: list[dict] = []

    class _Queue:
        async def submit(self, payload):
            submitted.append(dict(payload))

    monkeypatch.setattr(notify, "apprise_queue", lambda: _Queue())
    await AppSettingsRepository.update(notification_destinations=_dest(TG_A))
    await AppSettingsRepository.set_push_defaults({"new_dm": {"push": False, "apprise": True}})
    await push_manager.dispatch_message(_dm())
    assert len(submitted) == 1
    assert submitted[0]["text"] == "secret plans"  # the policy applies at send time


# -- settings API, secrets, proof of modification ------------------------------


@pytest.fixture
def destination_notices(monkeypatch) -> list[dict]:
    from app.push.manager import push_manager

    notices: list[dict] = []

    async def fake_dispatch_event(data):
        notices.append(dict(data))

    monkeypatch.setattr(push_manager, "dispatch_event", fake_dispatch_event)
    return notices


async def _patch_apprise(client, **apprise):
    return await client.patch(
        "/api/settings", json={"notification_destinations": {"apprise": apprise}}
    )


@pytest.mark.asyncio
async def test_settings_never_return_an_apprise_url(test_db, client, destination_notices):
    response = await _patch_apprise(client, urls=f"{TG_A}\n{TG_B}")
    assert response.status_code == 200
    for body in (response.text, (await client.get("/api/settings")).text):
        assert "AAAA" not in body and "111111" not in body and "tgram" not in body
    assert response.json()["notification_destinations"]["apprise"]["urls"] == "********"


@pytest.mark.asyncio
async def test_redacted_urls_keep_and_empty_clears(test_db, client, destination_notices):
    await _patch_apprise(client, urls=TG_A)
    response = await _patch_apprise(client, urls="********", include_message_text="never")
    assert response.status_code == 200
    stored = (await AppSettingsRepository.get()).notification_destinations.apprise
    assert stored.urls == TG_A
    assert stored.include_message_text == "never"

    await _patch_apprise(client, urls="")
    assert (await AppSettingsRepository.get()).notification_destinations.apprise.urls == ""


@pytest.mark.asyncio
async def test_refused_url_is_a_coded_400_at_save(test_db, client, destination_notices):
    response = await _patch_apprise(client, urls=f"{TG_A}\ndbus://")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["code"] == "egress_scheme"
    assert detail["params"]["line"] == 2
    assert (await AppSettingsRepository.get()).notification_destinations.apprise.urls == ""
    assert destination_notices == []


@pytest.mark.asyncio
async def test_destination_change_sends_the_push_notice(test_db, client, destination_notices):
    await _patch_apprise(client, urls=TG_A)
    await asyncio.sleep(0.05)
    assert destination_notices == [{"event": "notify.destination_changed", "kinds": ["apprise"]}]

    destination_notices.clear()
    await _patch_apprise(client, urls="********")  # nothing changed
    await client.patch(
        "/api/settings",
        json={"notification_destinations": {"webhook": {"url": "https://93.184.215.14/h"}}},
    )
    await asyncio.sleep(0.05)
    assert destination_notices == [{"event": "notify.destination_changed", "kinds": ["webhook"]}]


def test_destination_notice_text():
    from app.push.manager import _build_payload

    payload = _build_payload(
        {"event": "notify.destination_changed", "kinds": ["apprise", "email"]}, "en"
    )
    assert "Notification destination changed" in payload
    assert "Apprise, e-mail" in payload


@pytest.mark.asyncio
async def test_json_backup_omits_apprise_urls(test_db, destination_notices):
    from app.services.backup import export_json

    await AppSettingsRepository.update(notification_destinations=_dest(TG_A))
    document = (await export_json()).model_dump_json()
    assert "AAAA" not in document
    assert "settings.notification_destinations.apprise.urls" in document


@pytest.mark.asyncio
async def test_test_route_covers_apprise_with_coded_errors(
    test_db, client, monkeypatch, sleeps, destination_notices
):
    route = "/api/settings/notification-destinations/test"
    response = await client.post(route, json={"channel": "apprise"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "notification_destination_invalid"

    await AppSettingsRepository.update(notification_destinations=_dest(TG_A))
    _fake_sends(monkeypatch, {"1001": [_Response(status=401)]})
    response = await client.post(route, json={"channel": "apprise"})
    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "service_rejected"
    assert detail["params"] == {
        "line": 1,
        "context": "apprise",
        "url": "tgram://****",
        "status": 401,
    }
    assert "AAAA" not in response.text

    _fake_sends(monkeypatch, {})
    response = await client.post(route, json={"channel": "apprise"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "channel": "apprise"}


@pytest.mark.asyncio
async def test_health_reports_notification_statuses(test_db, client, monkeypatch, sleeps):
    _fake_sends(monkeypatch, {"1001": [_Response(status=500)]})
    with pytest.raises(AppriseSendFailed):
        await notify.send_apprise_alert(_dest(TG_A), _dm())
    body = (await client.get("/api/health")).json()
    last = body["notification_statuses"]["apprise"]["last_error"]
    assert last["code"] == "service_error"
    assert last["url"] == "tgram://****"
    assert body["notification_statuses"]["webhook"]["last_error"] is None


# -- review round 1 (mentions, DEBUG logs, multi-target retries) ---------------


@pytest.mark.parametrize(
    "mention",
    [
        "@everyone",
        "@here",
        "@channel",
        "@all",
        "@room",
        "@Channel",
        "<@123>",
        "<@!123>",
        "<!channel>",
        "<!here>",
        "<!everyone>",
    ],
)
@pytest.mark.parametrize("markdown", [False, True])
def test_every_room_wide_mention_is_neutralised(mention, markdown):
    _title, body = event_notification_text(
        _hashtag_message(f"hi {mention} there"), "en", markdown=markdown
    )
    plain = body.replace("\\", "")
    assert mention not in plain
    assert "​" in plain


def test_ordinary_at_words_are_left_alone():
    _title, body = event_notification_text(_hashtag_message("mail me @alice or @allison"), "en")
    assert "@alice" in body and "@allison" in body


@pytest.mark.asyncio
async def test_a_multi_target_url_is_not_retried(monkeypatch, sleeps):
    calls = _fake_sends(monkeypatch, {"1001": [_Response(status=503)]})
    with pytest.raises(AppriseSendFailed) as exc:
        await notify.send_apprise_alert(_dest("tgram://111111:AAAA-token-a/1001/1002"), _dm())
    assert exc.value.code == "service_error"
    assert len(calls) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_apprise_debug_logs_never_carry_url_tokens(http_server, sleeps):
    import logging

    from app.log_redaction import RedactingFormatter

    secret = "PathTokenS3cret"
    port = http_server.server_address[1]
    _Handler.answers = [(500, {}), (500, {}), (500, {})]
    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = _Capture(level=logging.DEBUG)
    handler.setFormatter(RedactingFormatter(logging.Formatter("%(name)s %(message)s")))
    apprise_logger = logging.getLogger("apprise")
    previous = apprise_logger.level
    apprise_logger.setLevel(logging.DEBUG)
    apprise_logger.addHandler(handler)
    try:
        dest = _dest(f"json://user:{secret}@127.0.0.1:{port}/{secret}x", allow_private_network=True)
        with pytest.raises(AppriseSendFailed):
            await notify.send_apprise_alert(dest, _dm())
    finally:
        apprise_logger.removeHandler(handler)
        apprise_logger.setLevel(previous)
    debug_lines = "\n".join(records)
    assert "127.0.0.1" in debug_lines  # Apprise did log the request at DEBUG
    assert secret not in debug_lines
