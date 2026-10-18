"""Notification channels: registry, repository, deletion cascade, cache, journal."""

import ipaddress
import json
import logging
import socket

import pytest

from app.notifications import channels as channels_module
from app.notifications.channels import (
    REGISTRY,
    ChannelCreate,
    ChannelError,
    ChannelUpdate,
    types_schema,
)
from app.repository.notification_channels import (
    DELIVERIES_PER_CHANNEL,
    strip_channel_from_rules,
)
from app.repository.notification_channels import (
    NotificationChannelRepository as Channels,
)
from app.repository.notification_channels import (
    NotificationJournalRepository as Journal,
)


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


SECRET_VALUES = ("smtp-pass-1", "hmac-secret-1", "https://hooks.example.org/secret-token")


def _email(**config):
    base = {"host": "smtp.example.org", "to": "me@example.org", "password": "smtp-pass-1"}
    return ChannelCreate(type="email", name="Mail", config={**base, **config})


async def _set_rules(db, rules: dict) -> None:
    async with db.tx() as conn:
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1", (json.dumps(rules),)
        )


async def _get_rules(db) -> dict:
    async with db.readonly() as conn:
        async with conn.execute("SELECT notification_rules FROM app_settings WHERE id = 1") as c:
            return json.loads((await c.fetchone())[0])


class TestRegistry:
    def test_every_native_type_is_registered(self):
        assert set(REGISTRY) == {"web_push", "email", "webhook", "apprise"}

    def test_secret_fields_are_write_only_in_the_schema(self):
        by_type = {entry["type"]: entry["schema"] for entry in types_schema()}
        assert by_type["email"]["properties"]["password"]["writeOnly"] is True
        assert by_type["webhook"]["properties"]["url"]["writeOnly"] is True
        assert by_type["webhook"]["properties"]["hmac_secret"]["writeOnly"] is True
        assert by_type["apprise"]["properties"]["urls"]["writeOnly"] is True
        assert "writeOnly" not in by_type["email"]["properties"]["host"]
        assert by_type["email"]["properties"]["host"]["label_key"].startswith("notifications.")
        assert "from" in by_type["email"]["properties"]

    def test_cloud_types_default_to_public_only_text(self):
        assert REGISTRY["webhook"].include_message_text_default == "always"
        cloud = channels_module.ChannelType(
            "x", channels_module.WebPushConfig, channels_module.Limits(1, 1, 1), cloud=True
        )
        assert cloud.include_message_text_default == "public_only"

    def test_defaults(self):
        assert REGISTRY["webhook"].quiet_hours_exempt_default is True
        assert REGISTRY["email"].default_limits.per_day == 200


@pytest.mark.asyncio
class TestRepository:
    async def test_fresh_database_has_the_builtin_push(self, test_db):
        found = await Channels.list_all()
        assert [c.id for c in found] == ["builtin:push"]
        assert found[0].deletable is False and found[0].type == "web_push"

    async def test_create_splits_secrets_and_no_read_path_returns_them(self, test_db):
        created = await Channels.create(_email())
        assert created.secret_fields_set == ["password"]
        assert "password" not in created.config
        assert created.config["from"] == ""
        dumps = [
            created.model_dump_json(),
            repr(created),
            *(c.model_dump_json() for c in await Channels.list_all()),
            (await Channels.get(created.id)).model_dump_json(),  # type: ignore[union-attr]
            json.dumps(types_schema()),
        ]
        for blob in dumps:
            assert not any(secret in blob for secret in SECRET_VALUES)
        # The dispatcher is the one reader of the values.
        channel, secrets = await Channels.get_for_send(created.id)  # type: ignore[misc]
        assert secrets == {"password": "smtp-pass-1"} and channel.id == created.id

    async def test_required_fields_and_invalid_config(self, test_db):
        with pytest.raises(ChannelError) as missing:
            await Channels.create(ChannelCreate(type="email", name="x", config={"host": "h"}))
        assert missing.value.code == "config_required" and missing.value.params["fields"] == ["to"]
        with pytest.raises(ChannelError) as invalid:
            await Channels.create(_email(port=70000))
        assert invalid.value.code == "config_invalid"
        assert "smtp-pass-1" not in repr(invalid.value.params)
        with pytest.raises(ChannelError) as unknown:
            await Channels.create(ChannelCreate(type="telex", name="x"))
        assert unknown.value.code == "channel_type_unknown"

    async def test_validation_error_never_echoes_a_secret(self, test_db):
        with pytest.raises(ChannelError) as exc:
            await Channels.create(
                ChannelCreate(
                    type="webhook",
                    name="w",
                    config={"url": "https://hooks.example.org/secret-token", "bogus": 1},
                )
            )
        assert exc.value.code == "config_invalid"
        assert "secret-token" not in json.dumps(exc.value.detail())

    async def test_egress_guard_refuses_a_private_host_unless_allowed(self, test_db):
        body = {"type": "webhook", "name": "lan"}
        with pytest.raises(ChannelError) as refused:
            await Channels.create(ChannelCreate(**body, config={"url": "http://127.0.0.1:9/h"}))
        assert refused.value.code == "egress_private_address"
        with pytest.raises(ChannelError) as scheme:
            await Channels.create(ChannelCreate(**body, config={"url": "ftp://example.org/h"}))
        assert scheme.value.code == "egress_scheme"
        with pytest.raises(ChannelError) as apprise:
            await Channels.create(
                ChannelCreate(type="apprise", name="a", config={"urls": "file:///etc/passwd"})
            )
        assert apprise.value.code
        allowed = await Channels.create(
            ChannelCreate(
                **body, config={"url": "http://127.0.0.1:9/h"}, allow_private_network=True
            )
        )
        assert allowed.allow_private_network is True and allowed.quiet_hours_exempt is True

    async def test_builtin_push_cannot_be_deleted_or_retyped(self, test_db):
        with pytest.raises(ChannelError) as deletion:
            await Channels.delete("builtin:push")
        assert deletion.value.code == "channel_builtin" and deletion.value.status == 400
        for new_type in ("email", "web_push"):
            if new_type == "web_push":
                await Channels.update("builtin:push", ChannelUpdate(type="web_push", enabled=False))
                continue
            with pytest.raises(ChannelError) as retype:
                await Channels.update("builtin:push", ChannelUpdate(type=new_type))
            assert retype.value.code == "type_immutable" and retype.value.status == 400
        assert (await Channels.get("builtin:push")).type == "web_push"  # type: ignore[union-attr]
        with pytest.raises(ChannelError) as second:
            await Channels.create(ChannelCreate(type="web_push", name="again"))
        assert second.value.code == "channel_exists"

    async def test_type_is_immutable_for_any_channel(self, test_db):
        created = await Channels.create(_email())
        with pytest.raises(ChannelError) as exc:
            await Channels.update(created.id, ChannelUpdate(type="webhook"))
        assert exc.value.code == "type_immutable" and exc.value.status == 400

    async def test_patch_is_visible_to_the_next_evaluation(self, test_db):
        created = await Channels.create(_email())
        assert (await Channels.get(created.id)).enabled is True  # type: ignore[union-attr]
        await Channels.update(created.id, ChannelUpdate(enabled=False, name="Renamed"))
        after = await Channels.get(created.id)
        assert after is not None and after.enabled is False and after.name == "Renamed"
        assert [c.enabled for c in await Channels.resolve([created.id])] == [False]
        await Channels.record_result(created.id, ok=False, error="boom")
        failed = await Channels.get(created.id)
        assert failed is not None and failed.failure_count == 1 and failed.last_error == "boom"
        await Channels.record_result(created.id, ok=True)
        ok = await Channels.get(created.id)
        assert ok is not None and ok.failure_count == 0 and ok.last_ok_at and ok.last_error is None

    async def test_secret_patch_rules(self, test_db):
        created = await Channels.create(_email())
        # Omitted, null and the mask keep the secret; an empty string clears it.
        for kept in ({"user": "bob"}, {"password": None}, {"password": "********"}):
            await Channels.update(created.id, ChannelUpdate(config=kept))
            assert (await Channels.get_for_send(created.id))[1] == {"password": "smtp-pass-1"}  # type: ignore[index]
        await Channels.update(created.id, ChannelUpdate(config={"password": "new-pass"}))
        assert (await Channels.get_for_send(created.id))[1] == {"password": "new-pass"}  # type: ignore[index]
        await Channels.update(created.id, ChannelUpdate(config={"password": ""}))
        found = await Channels.get(created.id)
        assert found is not None and found.secret_fields_set == []

    async def test_a_kept_secret_never_follows_a_destination_change(self, test_db):
        created = await Channels.create(_email())
        with pytest.raises(ChannelError) as exc:
            await Channels.update(created.id, ChannelUpdate(config={"host": "other.example.org"}))
        assert exc.value.code == "secret_required"
        assert exc.value.params["fields"] == ["password"]
        moved = await Channels.update(
            created.id, ChannelUpdate(config={"host": "other.example.org", "password": "p2"})
        )
        assert moved.config["host"] == "other.example.org"

    async def test_ids_are_not_reused_after_a_delete(self, test_db):
        first = await Channels.create(_email())
        await Channels.delete(first.id)
        second = await Channels.create(_email())
        assert first.id != second.id


@pytest.mark.asyncio
class TestDeletionCascade:
    async def test_delete_removes_every_reference_in_one_transaction(self, test_db):
        gone = (await Channels.create(_email())).id
        kept = (await Channels.create(_email(host="b.example.org"))).id
        await _set_rules(
            test_db,
            {
                "version": 1,
                "events": {
                    "message.dm": {"channels": ["builtin:push", gone, kept]},
                    "contact.first_seen": {
                        "channels": [gone],
                        "contact_types": {
                            "companion": [gone, kept],
                            "repeater": [],
                            "sensor": [gone],
                        },
                    },
                },
                "conversations": {
                    "contact-a": {gone: True, "builtin:push": False},
                    "contact-b": {gone: False},
                },
                "keywords": [{"id": "k1", "channels": [gone, kept]}],
                "watched_nodes": [{"public_key": "aa", "channels": [gone]}],
            },
        )
        await Journal.add_delivery(gone, "message.dm", status="failed")
        await Journal.add_sent(gone, 1)
        await Channels.delete(gone)

        rules = await _get_rules(test_db)
        assert gone not in json.dumps(rules)
        assert rules["events"]["message.dm"]["channels"] == ["builtin:push", kept]
        assert rules["events"]["contact.first_seen"]["contact_types"]["companion"] == [kept]
        assert rules["conversations"] == {"contact-a": {"builtin:push": False}}
        assert rules["keywords"][0]["channels"] == [kept]
        assert rules["watched_nodes"][0]["channels"] == []
        assert await Journal.list_deliveries(gone) == []
        assert await Journal.sent_since(gone, 0) == 0
        assert await Channels.get(gone) is None

    async def test_a_disabled_channel_keeps_its_rules(self, test_db):
        channel = (await Channels.create(_email())).id
        rules = {"version": 1, "events": {"message.dm": {"channels": [channel]}}}
        await _set_rules(test_db, rules)
        await Channels.update(channel, ChannelUpdate(enabled=False))
        assert await _get_rules(test_db) == rules

    async def test_deleting_an_unknown_channel_is_a_404(self, test_db):
        with pytest.raises(ChannelError) as exc:
            await Channels.delete("nope-1")
        assert exc.value.status == 404

    async def test_an_orphan_id_is_tolerated_and_logged_once(self, test_db, caplog):
        with caplog.at_level(logging.WARNING):
            first = await Channels.resolve(["builtin:push", "ghost-1"])
            second = await Channels.resolve(["ghost-1"])
        assert [c.id for c in first] == ["builtin:push"] and second == []
        assert caplog.text.count("ghost-1") == 1

    async def test_strip_tolerates_malformed_rules(self):
        rules = {
            "events": {"a": "x", "b": {"channels": "nope"}},
            "conversations": [],
            "keywords": [1],
        }
        assert strip_channel_from_rules(rules, "c") is False


@pytest.mark.asyncio
class TestJournal:
    async def test_deliveries_ring_keeps_200_per_channel(self, test_db):
        for i in range(DELIVERIES_PER_CHANNEL + 25):
            await Journal.add_delivery("a-1", "message.dm", status="failed", error=f"e{i}")
        await Journal.add_delivery("b-1", "message.dm", status="failed")
        rows = await Journal.list_deliveries("a-1", limit=1000)
        assert len(rows) == DELIVERIES_PER_CHANNEL
        assert rows[0]["error"] == f"e{DELIVERIES_PER_CHANNEL + 24}"
        assert len(await Journal.list_deliveries("b-1")) == 1

    async def test_daily_prune_caps_each_channel_and_drops_deleted_ones(self, test_db):
        async with test_db.tx() as conn:
            await conn.executemany(
                "INSERT INTO notification_deliveries (channel_id, event, status, created_at)"
                " VALUES (?, 'message.dm', 'failed', 0)",
                [("builtin:push",)] * (DELIVERIES_PER_CHANNEL + 50) + [("deleted-1",)] * 3,
            )
        assert await Journal.prune_deliveries() == 53
        assert (
            len(await Journal.list_deliveries("builtin:push", limit=1000)) == DELIVERIES_PER_CHANNEL
        )
        assert await Journal.list_deliveries("deleted-1") == []

    async def test_counters_and_state(self, test_db):
        await Journal.add_sent("a-1", 100)
        await Journal.add_sent("a-1", 100, 2)
        await Journal.add_sent("a-1", 101)
        assert await Journal.sent_since("a-1", 100) == 4
        await Journal.prune_counters(101)
        assert await Journal.sent_since("a-1", 0) == 1
        await Journal.state_set("first_seen:aa", "1", expires_at=50)
        await Journal.state_set("outage", "x")
        assert await Journal.state_get("first_seen:aa", now=10) == "1"
        assert await Journal.state_get("first_seen:aa", now=60) is None
        await Journal.state_prune(now=60)
        assert await Journal.state_get("outage") == "x"
