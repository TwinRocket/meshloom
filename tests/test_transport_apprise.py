"""The generic Apprise transport: one object per URL, final per-URL failures."""

from __future__ import annotations

import pytest

from app.notifications import apprise_medium
from app.notifications.apprise_medium import _Response
from app.notifications.channels import Channel
from app.notifications.events import event_from_payload
from app.notifications.transports.apprise import AppriseTransport
from app.notifications.transports.base import Delivery

TG_A = "tgram://111111:AAAA-token-a/1001"
TG_B = "tgram://222222:BBBB-token-b/2002"
DM = {"type": "PRIV", "conversation_key": "aa" * 32, "sender_name": "Léa", "text": "secret plans"}


def _channel(**kwargs) -> Channel:
    config = kwargs.pop("config", {})
    return Channel(id="apprise-1", type="apprise", name="Apprise", config=config, **kwargs)


def _delivery(payload: dict) -> Delivery:
    return Delivery(
        name=str(payload.get("event")), payload=payload, event=event_from_payload(payload)
    )


class _Calls(list):
    answers: dict[str, _Response]


@pytest.fixture
def sends(monkeypatch):
    calls = _Calls()
    answers: dict[str, _Response] = {}

    def fake(server, title, body, markdown):
        chat = str(server.targets[0][0])
        calls.append(
            {
                "chat": chat,
                "title": title,
                "body": body,
                "markdown": markdown,
                "verify": server.verify_certificate,
                "redirects": server.redirects,
                "cto": server.socket_connect_timeout,
                "rto": server.socket_read_timeout,
            }
        )
        answer = answers.get(chat, _Response(status=200))
        return answer.status == 200, answer

    async def no_sleep(seconds):
        return None

    monkeypatch.setattr(apprise_medium, "_send_sync", fake)
    monkeypatch.setattr(apprise_medium, "_sleep", no_sleep)
    monkeypatch.setattr("app.email_template.resolve_instance_language", _en)
    calls.answers = answers
    return calls


async def _en():
    return "en"


@pytest.mark.asyncio
async def test_one_failing_url_does_not_stop_nor_repeat_the_other(sends):
    sends.answers["1001"] = _Response(status=400)  # URL 1 rejected for good
    result = await AppriseTransport().send(
        _channel(), None, _delivery(DM), {"urls": f"{TG_A}\n{TG_B}"}
    )
    # URL 2 got it exactly once and the delivery is final: no dispatcher retry.
    assert [c["chat"] for c in sends] == ["1001", "2002"]
    assert result.status == "failed"
    assert result.error_code is not None and result.error_code.startswith("line 1 (")
    assert "service_rejected" in result.error_code
    assert "token-a" not in result.error_code


@pytest.mark.asyncio
async def test_a_retryable_url_retries_itself_then_stays_final(sends):
    sends.answers["2002"] = _Response(status=503)
    result = await AppriseTransport().send(
        _channel(), None, _delivery(DM), {"urls": f"{TG_A}\n{TG_B}"}
    )
    assert [c["chat"] for c in sends].count("1001") == 1
    assert [c["chat"] for c in sends].count("2002") == 3  # RETRY_WAITS, per URL
    assert (result.status, result.error_code) == (
        "failed",
        result.error_code,
    ) and "service_error" in str(result.error_code)


@pytest.mark.asyncio
async def test_all_urls_ok_sends_each_once_with_the_guards_on(sends):
    result = await AppriseTransport().send(
        _channel(), None, _delivery(DM), {"urls": f"{TG_A}\n{TG_B}"}
    )
    assert result.ok
    assert [c["chat"] for c in sends] == ["1001", "2002"]
    assert all(c["redirects"] is False and c["verify"] is True for c in sends)
    assert all(c["cto"] == apprise_medium.CONNECT_TIMEOUT_S for c in sends)
    assert all(c["rto"] == apprise_medium.READ_TIMEOUT_S for c in sends)


@pytest.mark.asyncio
async def test_text_policy_language_and_markdown_come_from_the_renderer(sends):
    transport = AppriseTransport()
    await transport.send(
        _channel(include_message_text="never", language="fr", config={"format": "markdown"}),
        None,
        _delivery(DM),
        {"urls": TG_A},
    )
    call = sends[-1]
    assert call["markdown"] is True
    assert "secret" not in call["body"] and "plans" not in call["body"]
    assert "Message de" in call["body"]
    await transport.send(_channel(), None, _delivery(DM), {"urls": TG_A})
    assert sends[-1]["markdown"] is False
    assert "secret plans" in sends[-1]["body"]


@pytest.mark.asyncio
async def test_coded_errors_for_a_refused_scheme_and_a_private_host(sends):
    send = AppriseTransport().send
    refused = await send(_channel(), None, _delivery(DM), {"urls": "dbus://"})
    assert refused.status == "failed"
    assert refused.error_code == "line 1 (dbus://****): egress_scheme"
    private = await send(_channel(), None, _delivery(DM), {"urls": "json://192.168.1.10/hook"})
    assert str(private.error_code).endswith("): egress_private_address")
    allowed = await send(
        _channel(allow_private_network=True), None, _delivery(DM), {"urls": "tgram://1:a-b/1"}
    )
    assert allowed.ok
    assert not any(c["chat"] == "192.168" for c in sends)


@pytest.mark.asyncio
async def test_not_configured_and_tls_policy(sends, monkeypatch):
    send = AppriseTransport().send
    assert (await send(_channel(), None, _delivery(DM), {})).error_code == "not_configured"
    # A fixed-host service and a public user-chosen host keep their certificate check.
    for urls in (TG_A, "json://93.184.215.14/hook"):
        bad = await send(
            _channel(verify_tls=False, allow_private_network=True),
            None,
            _delivery(DM),
            {"urls": urls},
        )
        assert str(bad.error_code).endswith(": verify_tls_requires_private_destination")
    assert not sends
    seen = []

    def fake(server, title, body, markdown):
        seen.append(server.verify_certificate)
        return True, _Response(status=200)

    monkeypatch.setattr(apprise_medium, "_send_sync", fake)
    ok = await send(
        _channel(verify_tls=False, allow_private_network=True),
        None,
        _delivery(DM),
        {"urls": "json://192.168.1.10/hook"},
    )
    assert ok.ok and seen == [False]


@pytest.mark.asyncio
async def test_discord_urls_never_override_the_avatar(monkeypatch):
    seen = []

    def fake(server, title, body, markdown):
        seen.append(server.avatar)
        return True, _Response(status=200)

    monkeypatch.setattr(apprise_medium, "_send_sync", fake)
    monkeypatch.setattr("app.email_template.resolve_instance_language", _en)
    result = await AppriseTransport().send(
        _channel(), None, _delivery(DM), {"urls": "discord://123456/abcdefgh-token"}
    )
    assert result.ok
    assert seen == [False]


@pytest.mark.asyncio
async def test_the_urls_never_reach_the_logs(sends, caplog):
    caplog.set_level("DEBUG")
    sends.answers["1001"] = _Response(status=400)
    await AppriseTransport().send(_channel(), None, _delivery(DM), {"urls": TG_A})
    assert "AAAA-token-a" not in caplog.text
