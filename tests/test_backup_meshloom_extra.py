"""meshloom_extra restore: through the route checks, idempotent, bots disabled,
push keys only on a host without subscriptions, proxy rules."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.push.vapid as vapid
from app.fanout.manager import SYSTEM_MESHLOOM_STATS_ID, fanout_manager
from app.models import (
    BackupFanoutConfig,
    BackupPushSubscription,
    BackupRadioProxy,
    BackupRestoreRequest,
    BackupVapid,
    ContactGroup,
    MeshloomExtra,
)
from app.repository import AppSettingsRepository, ContactGroupRepository
from app.repository.fanout import FanoutConfigRepository
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.radio_transport import RadioTransportRepository
from app.services.backup import restore_json, restore_meshloom_extra

BOT_CODE = "def bot(**kwargs):\n    return None\n"


def webhook(config_id: str = "hook-1", **changes) -> BackupFanoutConfig:
    fields = {
        "id": config_id,
        "type": "webhook",
        "name": "Hook",
        "enabled": False,
        "config": {"url": "https://example.org/hook"},
        "scope": {"messages": "all"},
    }
    fields.update(changes)
    return BackupFanoutConfig(**fields)


def bot(config_id: str = "bot-1") -> BackupFanoutConfig:
    return BackupFanoutConfig(
        id=config_id, type="bot", name="Greeter", enabled=True, config={"code": BOT_CODE}
    )


@pytest.fixture(autouse=True)
def _keep_vapid_cache(monkeypatch):
    for name in ("_cached_private_key", "_cached_public_key", "_cached_subject"):
        monkeypatch.setattr(vapid, name, getattr(vapid, name))


@pytest.fixture
def reload_spy(monkeypatch):
    spy = AsyncMock()
    monkeypatch.setattr(fanout_manager, "reload_config", spy)
    return spy


@pytest.fixture
def proxy_spy(monkeypatch):
    from app.radio_proxy.manager import radio_proxy_manager

    spy = AsyncMock(return_value={"enabled": False})
    monkeypatch.setattr(radio_proxy_manager, "apply_settings", spy)
    return spy


@pytest.mark.asyncio
async def test_restoring_twice_changes_no_count(backup_db, reload_spy, proxy_spy):
    extra = MeshloomExtra(
        fanout_configs=[webhook()],
        vapid=BackupVapid(private_key="priv", public_key="pub"),  # type: ignore[arg-type]
        push_subscriptions=[
            BackupPushSubscription(endpoint="https://push.example/1", p256dh="k", auth="a")
        ],
    )
    group = ContactGroup(id=1, name="Home", created_at=0, public_keys=[])
    for _ in range(2):
        assert await restore_meshloom_extra(extra) == []
        await restore_json(BackupRestoreRequest(confirm=True, groups=[group]))
    assert len(await FanoutConfigRepository.get_all()) == 1
    assert len(await PushSubscriptionRepository.get_all()) == 1
    assert len(await ContactGroupRepository.list_all()) == 1


@pytest.mark.asyncio
async def test_reserved_and_invalid_entries_are_refused(backup_db, reload_spy, proxy_spy):
    warnings = await restore_meshloom_extra(
        MeshloomExtra(
            fanout_configs=[
                webhook(SYSTEM_MESHLOOM_STATS_ID),
                webhook("bad", config={"url": "ftp://nope", "secret_token": "s3cr3t"}),
                webhook("good"),
            ]
        )
    )
    assert f"fanout_reserved:{SYSTEM_MESHLOOM_STATS_ID}" in warnings
    assert "fanout_invalid:bad:webhook" in warnings
    assert not any("s3cr3t" in warning for warning in warnings)
    assert [cfg["id"] for cfg in await FanoutConfigRepository.get_all()] == ["good"]
    reload_spy.assert_awaited_once_with("good")


@pytest.mark.asyncio
async def test_apprise_row_with_a_refused_scheme_comes_back_disabled(
    backup_db, reload_spy, proxy_spy
):
    """A 4.18 backup may hold an Apprise URL the allowlist now refuses: the row
    is kept, disabled, with a coded notice, never dropped or enabled."""
    old_row = webhook(
        "ap-1",
        type="apprise",
        name="Desktop",
        enabled=True,
        config={"urls": "dbus://\ntgram://123:ABC/456"},
    )
    warnings = await restore_meshloom_extra(MeshloomExtra(fanout_configs=[old_row]))
    assert "apprise_restored_disabled:Desktop" in warnings
    assert not any(w.startswith("fanout_invalid") for w in warnings)
    [stored] = await FanoutConfigRepository.get_all()
    assert stored["id"] == "ap-1"
    assert stored["enabled"] is False
    assert stored["config"]["urls"] == "dbus://\ntgram://123:ABC/456"
    assert stored["config"]["allow_private_network"] is True
    assert stored["config"]["private_network_grandfathered"] is True


@pytest.mark.asyncio
async def test_bots_skipped_while_disabled(backup_db, reload_spy, proxy_spy, monkeypatch):
    monkeypatch.setattr(fanout_manager, "_bots_disabled_until_restart", True)
    warnings = await restore_meshloom_extra(MeshloomExtra(fanout_configs=[bot()]))
    assert warnings == ["bots_disabled:Greeter"]
    assert await FanoutConfigRepository.get_all() == []


@pytest.mark.asyncio
async def test_bots_come_back_disabled(backup_db, proxy_spy, monkeypatch):
    monkeypatch.setattr(fanout_manager, "_bots_disabled_until_restart", False)
    started = AsyncMock()
    monkeypatch.setattr(fanout_manager, "_start_module", started)
    warnings = await restore_meshloom_extra(
        MeshloomExtra(fanout_configs=[bot(), webhook(enabled=True)])
    )
    assert "bots_restored_disabled:Greeter" in warnings
    stored = {cfg["id"]: cfg for cfg in await FanoutConfigRepository.get_all()}
    assert stored["bot-1"]["enabled"] is False
    assert stored["hook-1"]["enabled"] is True
    # reload_config ran for both; only the enabled webhook was started.
    assert [call.args[0]["id"] for call in started.await_args_list] == ["hook-1"]


@pytest.mark.asyncio
async def test_vapid_restored_on_a_host_without_subscriptions(backup_db, reload_spy, proxy_spy):
    await AppSettingsRepository.set_vapid_keys("host-priv", "host-pub")
    await vapid.ensure_vapid_keys()
    warnings = await restore_meshloom_extra(
        MeshloomExtra(
            vapid=BackupVapid(private_key="backup-priv", public_key="backup-pub"),  # type: ignore[arg-type]
            push_subscriptions=[
                BackupPushSubscription(endpoint="https://push.example/1", p256dh="k", auth="a")
            ],
        )
    )
    assert warnings == []
    assert vapid.get_vapid_private_key() == "backup-priv"
    assert vapid.get_vapid_public_key() == "backup-pub"
    assert len(await PushSubscriptionRepository.get_all()) == 1


@pytest.mark.asyncio
async def test_existing_subscriptions_keep_the_host_keys(backup_db, reload_spy, proxy_spy):
    await AppSettingsRepository.set_vapid_keys("host-priv", "host-pub")
    await vapid.ensure_vapid_keys()
    await PushSubscriptionRepository.create("https://push.example/host", "k", "a")
    warnings = await restore_meshloom_extra(
        MeshloomExtra(
            vapid=BackupVapid(private_key="backup-priv", public_key="backup-pub"),  # type: ignore[arg-type]
            push_subscriptions=[
                BackupPushSubscription(endpoint="https://push.example/1", p256dh="k", auth="a")
            ],
        )
    )
    assert warnings == ["push_kept_existing"]
    assert vapid.get_vapid_private_key() == "host-priv"
    assert [s["endpoint"] for s in await PushSubscriptionRepository.get_all()] == [
        "https://push.example/host"
    ]


@pytest.mark.asyncio
async def test_restored_vapid_subject_is_cached(backup_db):
    await restore_json(BackupRestoreRequest(confirm=True, vapid_subject="mailto:ops@example.org"))
    assert vapid.get_vapid_claims() == {"sub": "mailto:ops@example.org"}


@pytest.mark.asyncio
async def test_proxy_settings_go_through_the_route_checks(backup_db, proxy_spy, monkeypatch):
    from app.config import settings as server_settings
    from app.radio_proxy.manager import radio_proxy_manager

    await restore_meshloom_extra(
        MeshloomExtra(
            radio_proxy=BackupRadioProxy(enabled=True, bind="0.0.0.0", port=5002, max_clients=3)
        )
    )
    proxy_spy.assert_awaited_once()
    applied = proxy_spy.await_args.args[0]
    assert (applied.enabled, applied.port, applied.max_clients) == (True, 5002, 3)

    # A port the host manages is dropped, the rest applies.
    monkeypatch.setattr(server_settings, "managed_ports", True)
    monkeypatch.setattr(radio_proxy_manager, "_settings", applied)
    proxy_spy.reset_mock()
    warnings = await restore_meshloom_extra(
        MeshloomExtra(
            radio_proxy=BackupRadioProxy(enabled=True, bind="0.0.0.0", port=6000, max_clients=4)
        )
    )
    assert warnings == ["proxy_port_managed"]
    applied = proxy_spy.await_args.args[0]
    assert (applied.port, applied.max_clients) == (5002, 4)


@pytest.mark.asyncio
async def test_proxy_targeting_the_transport_stays_disabled(backup_db, proxy_spy):
    snapshot = await RadioTransportRepository.get()
    snapshot.transport = "tcp"
    snapshot.tcp_host = "127.0.0.1"
    snapshot.tcp_port = 5003
    await RadioTransportRepository.save(snapshot)
    warnings = await restore_meshloom_extra(
        MeshloomExtra(
            radio_proxy=BackupRadioProxy(enabled=True, bind="0.0.0.0", port=5003, max_clients=2)
        )
    )
    assert warnings == ["proxy_targets_transport"]
    assert proxy_spy.await_args.args[0].enabled is False


@pytest.mark.asyncio
async def test_integrations_keep_the_file_order(backup_db, reload_spy, proxy_spy):
    await FanoutConfigRepository.create(
        config_type="webhook",
        name="Existing",
        config={"url": "https://example.org/e"},
        scope={"messages": "all"},
        enabled=False,
        config_id="hook-b",
    )
    await restore_meshloom_extra(
        MeshloomExtra(
            fanout_configs=[
                webhook("hook-a", sort_order=2),
                webhook("hook-b", sort_order=0),
                webhook("hook-c", sort_order=1),
            ]
        )
    )
    ordered = sorted(await FanoutConfigRepository.get_all(), key=lambda cfg: cfg["sort_order"])
    assert [cfg["id"] for cfg in ordered] == ["hook-b", "hook-c", "hook-a"]


@pytest.mark.asyncio
async def test_inspect_lists_integrations_proxy_and_skipped_bots(backup_db, monkeypatch):
    from app.models import BackupExportV2
    from app.services.backup_restore import inspect_backup

    doc = BackupExportV2(
        meshloom_extra=MeshloomExtra(
            fanout_configs=[
                webhook(enabled=True),
                webhook(SYSTEM_MESHLOOM_STATS_ID),
                bot(),
            ],
            radio_proxy=BackupRadioProxy(enabled=True, port=5000, max_clients=2),
        )
    )
    monkeypatch.setattr(fanout_manager, "_bots_disabled_until_restart", True)
    result = await inspect_backup(doc, None)
    assert [(i.name, i.type, i.enabled) for i in result.integrations] == [("Hook", "webhook", True)]
    assert result.radio_proxy_enabled is True
    assert result.bot_names == ["Greeter"]
    assert result.bots_will_be_skipped is True

    monkeypatch.setattr(fanout_manager, "_bots_disabled_until_restart", False)
    assert (await inspect_backup(doc, None)).bots_will_be_skipped is False


@pytest.mark.asyncio
async def test_inspect_shows_where_restored_push_notifications_go(backup_db):
    """The file chooses the push endpoints: inspect names their hosts, but only
    when the restore would write them (no subscriptions of this host's own)."""
    from app.models import BackupExportV2
    from app.services.backup_restore import inspect_backup

    doc = BackupExportV2(
        meshloom_extra=MeshloomExtra(
            vapid=BackupVapid(private_key="backup-priv", public_key="backup-pub"),  # type: ignore[arg-type]
            push_subscriptions=[
                BackupPushSubscription(
                    endpoint="https://push.example.net/send/abc",
                    p256dh="k",
                    auth="a",
                    label="Phone",
                ),
                BackupPushSubscription(endpoint="not a url", p256dh="k", auth="a"),
            ],
        )
    )
    result = await inspect_backup(doc, None)
    assert [(t.label, t.host) for t in result.push_targets] == [
        ("Phone", "push.example.net"),
        ("", "not a url"),
    ]

    own = await PushSubscriptionRepository.create("https://push.example/host", "k", "a")
    await AppSettingsRepository.set_vapid_keys("host-priv", "host-pub")
    assert (await inspect_backup(doc, None)).push_targets == []  # kept: push_kept_existing

    no_vapid = BackupExportV2(
        meshloom_extra=MeshloomExtra(
            push_subscriptions=[
                BackupPushSubscription(endpoint="https://push.example/1", p256dh="k", auth="a")
            ]
        )
    )
    await PushSubscriptionRepository.delete(own["id"])
    assert (await inspect_backup(no_vapid, None)).push_targets == []
