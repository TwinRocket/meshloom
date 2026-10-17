"""Apprise URL validation (``app/net/apprise_urls.py``) and the fanout egress checks."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.net.apprise_urls import (
    ALLOWED_SCHEMES,
    REFUSED_SCHEMES,
    AppriseUrlRefused,
    parse_apprise_urls,
    redact_apprise_url,
    validate_apprise_urls,
)

# A public address that is not a documentation range: no DNS lookup needed.
PUBLIC_IP = "93.184.215.14"


def _installed_schemes() -> set[str]:
    from apprise.plugins import N_MGR

    N_MGR.load_modules()
    return {s.lower() for s in N_MGR.schemas(include_disabled=True)}


class TestClassification:
    def test_every_installed_plugin_is_classified(self):
        unclassified = _installed_schemes() - set(ALLOWED_SCHEMES) - REFUSED_SCHEMES
        assert not unclassified, (
            "Apprise exposes plugins Meshloom has not classified; add them to "
            f"ALLOWED_SCHEMES or REFUSED_SCHEMES in app/net/apprise_urls.py: {sorted(unclassified)}"
        )

    def test_the_lists_do_not_overlap(self):
        assert not set(ALLOWED_SCHEMES) & REFUSED_SCHEMES

    def test_a_new_plugin_makes_the_classification_test_fail(self):
        from apprise.plugins import N_MGR
        from apprise.plugins.base import NotifyBase

        class NotifyMeshloomFake(NotifyBase):
            service_name = "Fake"
            protocol = "meshloomfake"

            def send(self, body, title="", notify_type=None, **kwargs):
                return True

        N_MGR.load_modules()
        N_MGR.add(NotifyMeshloomFake, schemas="meshloomfake")
        try:
            with pytest.raises(AssertionError, match="meshloomfake"):
                TestClassification().test_every_installed_plugin_is_classified()
        finally:
            N_MGR.remove("meshloomfake")
        assert "meshloomfake" not in _installed_schemes()


class TestSchemes:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "url",
        [
            "dbus://",
            "windows://",
            "macosx://",
            "gnome://",
            "syslog://",
            "rsyslog://localhost",
            "file:///etc/passwd",
            "sns://AKIA/secret/us-east-1/+15555555555",
            "ses://user@example.com/AKIA/secret/us-east-1",
            "nope",
            "https://192.168.1.5/not-a-native-url",
        ],
    )
    @pytest.mark.parametrize("allow_private_network", [False, True])
    async def test_refused_schemes(self, url, allow_private_network):
        with pytest.raises(AppriseUrlRefused) as caught:
            await validate_apprise_urls([url], allow_private_network=allow_private_network)
        assert caught.value.code in {"egress_scheme", "apprise_url_invalid"}
        if not url.startswith("https://"):
            assert caught.value.code == "egress_scheme"

    def test_refused_scheme_is_not_instantiated(self):
        with patch("apprise.Apprise.instantiate") as instantiate:
            with pytest.raises(AppriseUrlRefused):
                parse_apprise_urls(["dbus://"])
        instantiate.assert_not_called()

    def test_detail_names_the_line_and_scheme_only(self):
        with pytest.raises(AppriseUrlRefused) as caught:
            parse_apprise_urls(["discord://1/abc", "dbus://secret-token"])
        detail = caught.value.detail()
        assert detail["code"] == "egress_scheme"
        assert detail["params"] == {"line": 2, "context": "apprise", "scheme": "dbus"}
        assert "secret-token" not in str(detail)


class TestHosts:
    @pytest.mark.asyncio
    async def test_private_json_refused_without_the_flag(self):
        with pytest.raises(AppriseUrlRefused) as caught:
            await validate_apprise_urls(["json://192.168.1.5/"], allow_private_network=False)
        assert caught.value.code == "egress_private_address"

    @pytest.mark.asyncio
    async def test_private_json_and_loopback_accepted_with_the_flag(self):
        result = await validate_apprise_urls(
            ["json://192.168.1.5/", "gotify://127.0.0.1/token"], allow_private_network=True
        )
        assert [r.url_class for r in result] == ["user_host", "user_host"]
        assert result[0].destination is not None

    @pytest.mark.asyncio
    async def test_metadata_refused_whatever_the_flag(self):
        with pytest.raises(AppriseUrlRefused) as caught:
            await validate_apprise_urls(["json://169.254.169.254/"], allow_private_network=True)
        assert caught.value.code == "egress_private_address"

    @pytest.mark.asyncio
    async def test_smtp_server_is_the_checked_host(self):
        with pytest.raises(AppriseUrlRefused) as caught:
            await validate_apprise_urls(
                [f"mailtos://user:pass@{PUBLIC_IP}?smtp=10.0.0.1&from=a@b.c"],
                allow_private_network=False,
            )
        assert caught.value.code == "egress_private_address"

    @pytest.mark.asyncio
    async def test_public_user_host_passes(self):
        result = await validate_apprise_urls(
            [f"json://{PUBLIC_IP}:8080/path"], allow_private_network=False
        )
        assert result[0].destination is not None
        assert result[0].destination.port == 8080

    @pytest.mark.asyncio
    async def test_ntfy_cloud_mode_needs_no_lookup(self):
        with patch("app.net.apprise_urls.validate_destination") as guard:
            result = await validate_apprise_urls(["ntfy://my-topic"], allow_private_network=False)
        guard.assert_not_called()
        assert result[0].destination is None

    @pytest.mark.asyncio
    async def test_telegram_with_chat_id_passes_without_any_host_check(self):
        with patch("app.net.apprise_urls.validate_destination") as guard:
            result = await validate_apprise_urls(
                ["tgram://123:ABC/456"], allow_private_network=False
            )
        guard.assert_not_called()
        assert result[0].url_class == "fixed_host"

    @pytest.mark.asyncio
    async def test_telegram_without_chat_id_refused(self):
        with pytest.raises(AppriseUrlRefused) as caught:
            await validate_apprise_urls(["tgram://123:ABC"], allow_private_network=True)
        assert caught.value.code == "apprise_chat_id_required"

    @pytest.mark.asyncio
    async def test_native_discord_url_is_a_fixed_host(self):
        result = await validate_apprise_urls(
            ["https://discord.com/api/webhooks/1234/abcd"], allow_private_network=False
        )
        assert result[0].url_class == "fixed_host"


class TestRedirects:
    @pytest.mark.parametrize(
        "url", ["json://127.0.0.1/?redirect=yes", "ntfy://topic", "discord://1/abc"]
    )
    def test_redirects_forced_off(self, url):
        [item] = parse_apprise_urls([url])
        assert item.server.redirects is False

    def test_json_post_does_not_follow_redirects(self):
        from unittest.mock import MagicMock

        [item] = parse_apprise_urls(["json://127.0.0.1/?redirect=yes"])
        response = MagicMock(status_code=200, content=b"", text="")
        with patch("requests.request", return_value=response) as post:
            assert item.server.notify(body="hi") is True
        assert post.call_args.kwargs["allow_redirects"] is False


class TestSecondaryHosts:
    """Lookups that would reach a host other than the checked one are off."""

    @pytest.mark.parametrize(
        "url",
        [
            f"matrixs://user:pass@{PUBLIC_IP}/#room",
            f"matrixs://user:pass@{PUBLIC_IP}/#room?discovery=yes",
        ],
    )
    def test_matrix_server_discovery_is_off(self, url):
        [item] = parse_apprise_urls([url])
        assert item.server.discovery is False
        with patch("requests.get") as get:
            # The checked host, never a base_url read from .well-known.
            assert item.server.base_url == f"https://{PUBLIC_IP}"
        get.assert_not_called()

    def test_matrix_send_reaches_only_the_checked_host(self):
        from unittest.mock import MagicMock

        [item] = parse_apprise_urls([f"matrixs://user:pass@{PUBLIC_IP}/#room"])
        response = MagicMock(status_code=500, content=b"{}", text="{}")
        response.json.return_value = {}
        with (
            patch("requests.get", return_value=response) as get,
            patch("requests.post", return_value=response) as post,
            patch("requests.put", return_value=response) as put,
        ):
            item.server.notify(body="hi")
        urls = [c.args[0] for m in (get, post, put) for c in m.call_args_list]
        assert urls, "the plugin sent nothing"
        assert all(u.startswith(f"https://{PUBLIC_IP}/") for u in urls), urls
        assert not any(".well-known" in u for u in urls)

    def test_email_wkd_lookup_is_off(self):
        [item] = parse_apprise_urls(
            [f"mailto://user:pass@{PUBLIC_IP}?to=someone@example.org&wkd=yes"]
        )
        assert item.server.use_wkd is False
        assert item.server.pgp.wkd is None


class TestRedaction:
    @pytest.mark.parametrize(
        ("url", "expected"),
        [
            ("tgram://123:ABC/456", "tgram://****"),
            ("discord://1234/secret", "discord://****"),
            ("json://user:pw@example.com:8080/path?x=1", "json://****@example.com:8080/****"),
            ("ntfys://ntfy.sh/topic", "ntfys://ntfy.sh/****"),
            ("gotify://10.0.0.4", "gotify://10.0.0.4"),
            ("dbus://", "dbus://****"),
            ("not a url", "****"),
        ],
    )
    def test_redact(self, url, expected):
        assert redact_apprise_url(url) == expected


# ---------------------------------------------------------------------------
# Fanout routes (create / update) through the guard
# ---------------------------------------------------------------------------


async def _create(config_type: str, config: dict, *, enabled: bool = True) -> dict:
    from app.routers.fanout import FanoutConfigCreate, create_fanout_config

    with patch("app.routers.fanout.fanout_manager.reload_config", new=AsyncMock()):
        return await create_fanout_config(
            FanoutConfigCreate(type=config_type, name="t", config=config, enabled=enabled)
        )


class TestFanoutRoutes:
    @pytest.mark.asyncio
    async def test_webhook_to_private_address_needs_the_flag(self, test_db):
        with pytest.raises(HTTPException) as caught:
            await _create("webhook", {"url": "http://192.168.1.5/hook"})
        assert caught.value.status_code == 400
        assert caught.value.detail["code"] == "egress_private_address"

        cfg = await _create(
            "webhook", {"url": "http://192.168.1.5/hook", "allow_private_network": True}
        )
        assert cfg["config"]["allow_private_network"] is True

    @pytest.mark.asyncio
    async def test_webhook_to_loopback_accepted_with_the_flag(self, test_db):
        cfg = await _create(
            "webhook", {"url": "http://127.0.0.1:1880/hook", "allow_private_network": True}
        )
        assert cfg["config"]["url"] == "http://127.0.0.1:1880/hook"

    @pytest.mark.asyncio
    async def test_new_rows_default_to_false_and_are_not_grandfathered(self, test_db):
        cfg = await _create(
            "webhook",
            {"url": f"https://{PUBLIC_IP}/hook", "private_network_grandfathered": True},
        )
        assert cfg["config"]["allow_private_network"] is False
        assert "private_network_grandfathered" not in cfg["config"]

    @pytest.mark.asyncio
    async def test_apprise_refused_scheme_whatever_the_flag(self, test_db):
        for flag in (False, True):
            with pytest.raises(HTTPException) as caught:
                await _create(
                    "apprise", {"urls": "discord://1/abc\ndbus://", "allow_private_network": flag}
                )
            assert caught.value.status_code == 400
            assert caught.value.detail["code"] == "egress_scheme"
            assert caught.value.detail["params"]["line"] == 2

    @pytest.mark.asyncio
    async def test_apprise_private_json_and_telegram(self, test_db):
        with pytest.raises(HTTPException) as caught:
            await _create("apprise", {"urls": "json://192.168.1.5/"})
        assert caught.value.detail["code"] == "egress_private_address"

        with pytest.raises(HTTPException) as caught:
            await _create("apprise", {"urls": "tgram://123:ABC"})
        assert caught.value.detail["code"] == "apprise_chat_id_required"

        cfg = await _create("apprise", {"urls": "tgram://123:ABC/456"})
        assert cfg["config"]["urls"] == "tgram://123:ABC/456"

    @pytest.mark.asyncio
    async def test_sqs_endpoint_and_map_upload_api_are_checked(self, test_db):
        with pytest.raises(HTTPException) as caught:
            await _create(
                "sqs",
                {
                    "queue_url": "https://sqs.us-east-1.amazonaws.com/1/q",
                    "endpoint_url": "http://10.0.0.9:4566",
                },
            )
        assert caught.value.detail["code"] == "egress_private_address"

        with pytest.raises(HTTPException) as caught:
            await _create("map_upload", {"api_url": "http://127.0.0.1:9000/upload"})
        assert caught.value.detail["code"] == "egress_private_address"

        cfg = await _create(
            "map_upload", {"api_url": "http://127.0.0.1:9000/upload", "allow_private_network": True}
        )
        assert cfg["config"]["allow_private_network"] is True

    @pytest.mark.asyncio
    async def test_disabled_row_skips_the_lookup_until_enabled(self, test_db):
        from app.routers.fanout import FanoutConfigUpdate, update_fanout_config

        cfg = await _create("webhook", {"url": "http://192.168.1.5/hook"}, enabled=False)
        with patch("app.routers.fanout.fanout_manager.reload_config", new=AsyncMock()):
            with pytest.raises(HTTPException) as caught:
                await update_fanout_config(cfg["id"], FanoutConfigUpdate(enabled=True))
        assert caught.value.detail["code"] == "egress_private_address"

    @pytest.mark.asyncio
    async def test_grandfathered_flag_kept_until_the_destination_changes(self, test_db):
        from app.repository.fanout import FanoutConfigRepository
        from app.routers.fanout import FanoutConfigUpdate, update_fanout_config

        row = await FanoutConfigRepository.create(
            config_type="webhook",
            name="old",
            config={
                "url": "http://192.168.1.5/hook",
                "allow_private_network": True,
                "private_network_grandfathered": True,
            },
            scope={},
            enabled=True,
        )
        with patch("app.routers.fanout.fanout_manager.reload_config", new=AsyncMock()):
            kept = await update_fanout_config(row["id"], FanoutConfigUpdate(name="renamed"))
            assert kept["config"]["private_network_grandfathered"] is True
            dismissed = await update_fanout_config(
                row["id"],
                FanoutConfigUpdate(
                    config={**kept["config"], "private_network_grandfathered": False}
                ),
            )
        assert "private_network_grandfathered" not in dismissed["config"]
        assert dismissed["config"]["allow_private_network"] is True

    def test_restore_of_an_older_backup_is_grandfathered(self):
        from app.routers.fanout import prepare_fanout_config

        config, _ = prepare_fanout_config(
            "webhook", {"url": "http://192.168.1.5/hook"}, {}, restored=True
        )
        assert config["allow_private_network"] is True
        assert config["private_network_grandfathered"] is True

        config, _ = prepare_fanout_config(
            "webhook",
            {"url": "http://192.168.1.5/hook", "allow_private_network": False},
            {},
            restored=True,
        )
        assert config["allow_private_network"] is False
        assert "private_network_grandfathered" not in config


# ---------------------------------------------------------------------------
# Modules check again before each send
# ---------------------------------------------------------------------------


class TestModulesRecheck:
    @pytest.mark.asyncio
    async def test_apprise_module_refuses_before_sending(self):
        from app.fanout.apprise_mod import AppriseModule

        mod = AppriseModule("t", {"urls": "json://192.168.1.5/"})
        with patch("app.fanout.apprise_mod._send_sync") as send:
            await mod.on_message({"type": "PRIV", "text": "hi", "sender_name": "A"})
        send.assert_not_called()
        assert mod.last_error == "egress_private_address (URL 1)"

    @pytest.mark.asyncio
    async def test_webhook_module_refuses_before_connecting(self):
        from app.fanout.webhook import WebhookModule

        mod = WebhookModule("t", {"url": "http://192.168.1.5/hook"})
        await mod.start()
        with patch("app.fanout.webhook.pinned_async_client") as client:
            await mod.on_message({"type": "PRIV", "text": "hi"})
        client.assert_not_called()
        assert mod.last_error == "egress_private_address"

    @pytest.mark.asyncio
    async def test_map_upload_post_refuses_before_connecting(self):
        from app.fanout.map_upload import MapUploadModule
        from app.net.egress_guard import EgressBlocked

        mod = MapUploadModule("t", {"api_url": "http://10.1.2.3/upload", "dry_run": False})
        with patch("app.fanout.map_upload.pinned_async_client") as client:
            with pytest.raises(EgressBlocked):
                await mod._post("http://10.1.2.3/upload", content="{}", headers={})
        client.assert_not_called()

    @pytest.mark.asyncio
    async def test_sqs_module_does_not_start_on_a_private_endpoint(self):
        from app.fanout.sqs import SqsModule

        mod = SqsModule(
            "t",
            {
                "queue_url": "https://sqs.us-east-1.amazonaws.com/1/q",
                "endpoint_url": "http://10.0.0.9",
            },
        )
        with patch("app.fanout.sqs.boto3.client") as boto_client:
            await mod.start()
        boto_client.assert_not_called()
        assert mod.last_error == "egress_private_address"
