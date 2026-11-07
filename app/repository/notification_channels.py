"""Repository for the notification channels and their journal, counters and state.

Reads never return a secret: ``Channel`` carries ``secret_fields_set`` (names only).
``get_for_send`` is the one reader of the secret values, for the dispatcher. The values
are stored encrypted (``app.secret_store``, MSHL-133): ``encode_secrets`` on every write,
``decode_secrets`` on every read; a value the key cannot open makes the channel read as
off with ``last_error = "secret_unreadable"`` until the secret is typed again. The public
channels are cached in memory; every write invalidates the cache, so the next
evaluation sees a PATCH.
"""

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import aiosqlite
from pydantic import ValidationError

from app import secret_store
from app.database import db
from app.net.apprise_urls import AppriseUrlRefused, split_apprise_urls, validate_apprise_urls
from app.net.egress_guard import (
    EgressBlocked,
    HostAllowlist,
    tls_check_may_be_off,
    validate_destination,
)
from app.notifications.channels import (
    BUILTIN_PUSH_ID,
    DISCORD_HOSTS,
    HEARTBEAT_URL_KEY,
    NTFY_DEFAULT_SERVER,
    REGISTRY,
    Channel,
    ChannelCreate,
    ChannelError,
    ChannelType,
    ChannelUpdate,
    get_type,
    heartbeat_supported,
    is_builtin,
)
from app.notifications.failures import autopause_key
from app.notifications.heartbeat import check_ping_url
from app.notifications.presets import PresetError, compile_url
from app.notifications.templates import check_templates
from app.secret_fields import REDACTED, destination_changed

logger = logging.getLogger(__name__)

DELIVERIES_PER_CHANNEL = 200
# Journal statuses counted per channel and hour in ``notification_state`` (keys
# ``stat:<status>:<hour>:<channel_id>``, expiring after the 24 h window): the cards show
# exact "held" and "grouped" figures although the ring keeps only 200 rows.
STAT_PREFIX = "stat:"
STAT_STATUSES = ("suppressed_quiet", "aggregated")
STAT_WINDOW_H = 24
LAST_ERROR_MAX = 200

_cache: dict[str, Channel] | None = None
_orphans_logged: set[str] = set()


def invalidate_cache() -> None:
    global _cache
    _cache = None
    # The per-account overrides are notification state too (MSHL-321): one reset for both.
    from app.repository import user_notification_rules

    user_notification_rules.invalidate_cache()


def _loads(raw: object) -> dict[str, Any]:
    try:
        loaded = json.loads(raw) if isinstance(raw, str) and raw else {}
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


SECRETS_TABLE = "notification_channels"


def decode_secrets(raw: object, channel_id: str) -> tuple[dict[str, Any], list[str]]:
    """The stored secrets of a channel, decrypted, and the names the key cannot open.

    Tolerant read: a value that is not a ``v1:`` token (a row an older build wrote) is
    returned as it is. An unreadable value is left out of the dict, never raised."""
    readable: dict[str, Any] = {}
    unreadable: list[str] = []
    for name, value in _loads(raw).items():
        try:
            readable[name] = secret_store.decrypt(
                value, secret_store.aad(SECRETS_TABLE, channel_id, name)
            )
        except secret_store.SecretUnreadable:
            unreadable.append(name)
    return readable, sorted(unreadable)


def encode_secrets(secrets: dict[str, Any], channel_id: str) -> str:
    """The ``secrets`` column for ``secrets``: every non-empty string value encrypted,
    bound to the channel id and the field name."""
    stored: dict[str, Any] = {}
    for name, value in secrets.items():
        if isinstance(value, str) and value:
            # Fails closed: ``SecretStoreUnavailable`` propagates without a usable key
            # (503 ``secret_store_unavailable``); a secret is never stored plain.
            value = secret_store.encrypt(value, secret_store.aad(SECRETS_TABLE, channel_id, name))
        stored[name] = value
    return json.dumps(stored, sort_keys=True)


def _read_config(row: aiosqlite.Row) -> dict[str, Any]:
    """The stored config with the type's read-time defaults for keys an older version
    did not write (``ChannelType.stored_defaults``, e.g. a 4.18.x webhook reads as ``v1``)."""
    config = _loads(row["config"])
    entry = REGISTRY.get(row["type"])
    return entry.read_config(config) if entry is not None else config


def _row_to_channel(row: aiosqlite.Row) -> Channel:
    secrets, unreadable = decode_secrets(row["secrets"], row["id"])
    return Channel(
        id=row["id"],
        type=row["type"],
        preset=row["preset"],
        name=row["name"],
        # An unreadable secret reads as off, without touching the row: the matching
        # secrets.key brings the channel back as it was.
        enabled=bool(row["enabled"]) and not unreadable,
        language=row["language"],
        config=_read_config(row),
        secret_fields_set=sorted(k for k, v in secrets.items() if v),
        secret_unreadable=unreadable,
        allow_private_network=bool(row["allow_private_network"]),
        include_message_text=row["include_message_text"],
        verify_tls=bool(row["verify_tls"]),
        template_dm=row["template_dm"] or "",
        template_channel=row["template_channel"] or "",
        heartbeat_interval_s=row["heartbeat_interval_s"] or 0,
        heartbeat_method="POST" if row["heartbeat_method"] == "POST" else "GET",
        digest_interval_s=row["digest_interval_s"] or 0,
        quiet_hours_exempt=bool(row["quiet_hours_exempt"]),
        rate_per_min=row["rate_per_min"],
        rate_per_hour=row["rate_per_hour"],
        rate_per_day=row["rate_per_day"],
        sort_order=row["sort_order"] or 0,
        created_at=row["created_at"] or 0,
        last_ok_at=row["last_ok_at"],
        last_error=secret_store.SECRET_UNREADABLE if unreadable else row["last_error"],
        failure_count=row["failure_count"] or 0,
        paused_until=row["paused_until"],
        deletable=not is_builtin(row["id"]),
    )


async def _fetch(conn: aiosqlite.Connection, channel_id: str) -> aiosqlite.Row | None:
    async with conn.execute("SELECT * FROM notification_channels WHERE id = ?", (channel_id,)) as c:
        return await c.fetchone()


def _split_config(entry: ChannelType, config: dict[str, Any]) -> tuple[dict, dict]:
    """Validate ``config`` against the type's model, then split public from secret."""
    try:
        model = entry.config_model.model_validate(config)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in err["loc"]) for err in exc.errors()})
        raise ChannelError("invalid_config", fields=fields) from None
    dumped = model.model_dump(by_alias=True, mode="python")
    public: dict[str, Any] = {}
    secrets: dict[str, str] = {}
    for name, value in dumped.items():
        key = name
        if key in entry.secret_fields:
            raw = value.get_secret_value() if hasattr(value, "get_secret_value") else value
            if raw:
                secrets[key] = raw
        else:
            public[key] = value
    return public, secrets


# ``notification_state`` key of the secrets a restored channel still waits for (JSON list of
# names), written by the backup restore (``services/backup_notifications.py``).
PENDING_SECRETS = "secret_pending:"


async def _pending_secrets(channel_id: str) -> list[str]:
    async with db.readonly() as conn:
        async with conn.execute(
            "SELECT value FROM notification_state WHERE key = ?", (PENDING_SECRETS + channel_id,)
        ) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return []
    try:
        names = json.loads(row[0])
    except json.JSONDecodeError:
        return []
    return [n for n in names if isinstance(n, str)] if isinstance(names, list) else []


def _required_missing(entry: ChannelType, public: dict, secrets: dict) -> list[str]:
    return [f for f in entry.required if not (public.get(f) or secrets.get(f))]


async def _check_destination(
    entry: ChannelType,
    public: dict,
    secrets: dict,
    allow_private_network: bool,
    verify_tls: bool = True,
) -> None:
    """Refuse a host the egress guard would refuse at send (400, coded).

    Certificate checks come off only for a destination whose every resolved address is
    private (``verify_tls_requires_private_destination``), and a mail server outside the
    private network never gets a login over ``mode=none`` (``smtp_plaintext_auth_refused``).
    """
    try:
        if entry.preset is not None:
            # The URL a preset generates goes through the same checks as a typed one.
            try:
                url = compile_url(entry.preset, {**public, **secrets})
            except PresetError as exc:
                raise ChannelError(exc.code, fields=exc.fields) from None
            validated = await validate_apprise_urls(
                [url], allow_private_network=allow_private_network
            )
            if not verify_tls:
                for item in validated:
                    if item.destination is None or not tls_check_may_be_off(item.destination.ips):
                        raise ChannelError(
                            "verify_tls_requires_private_destination", scheme=item.scheme
                        )
        elif entry.id == "webhook" and secrets.get("url"):
            dest = await validate_destination(
                secrets["url"], allow_private_network=allow_private_network
            )
            if not verify_tls and not tls_check_may_be_off(dest.ips):
                raise ChannelError("verify_tls_requires_private_destination")
        elif entry.id == "ntfy":
            dest = await validate_destination(
                str(public.get("server") or NTFY_DEFAULT_SERVER),
                allow_private_network=allow_private_network,
            )
            if not verify_tls and not tls_check_may_be_off(dest.ips):
                raise ChannelError("verify_tls_requires_private_destination")
        elif entry.id == "discord" and secrets.get("webhook_url"):
            dest = await validate_destination(
                secrets["webhook_url"],
                allow_private_network=allow_private_network,
                schemes=frozenset({"https"}),
                allowed_hosts=HostAllowlist.parse(DISCORD_HOSTS),
            )
            if not verify_tls and not tls_check_may_be_off(dest.ips):
                raise ChannelError("verify_tls_requires_private_destination")
        elif entry.id == "apprise" and secrets.get("urls"):
            validated = await validate_apprise_urls(
                split_apprise_urls(secrets["urls"]), allow_private_network=allow_private_network
            )
            if not verify_tls:
                for line, item in enumerate(validated, start=1):
                    if item.destination is None or not tls_check_may_be_off(item.destination.ips):
                        raise ChannelError(
                            "verify_tls_requires_private_destination",
                            line=line,
                            scheme=item.scheme,
                        )
        elif entry.id == "email" and public.get("host"):
            host = str(public["host"]).strip()
            bracketed = f"[{host}]" if ":" in host else host
            dest = await validate_destination(
                f"http://{bracketed}:{public.get('port') or 587}/",
                allow_private_network=allow_private_network,
            )
            private = tls_check_may_be_off(dest.ips)
            if not verify_tls and not private:
                raise ChannelError("verify_tls_requires_private_destination")
            has_login = public.get("user") or secrets.get("password")
            if public.get("mode") == "none" and has_login and not private:
                raise ChannelError("smtp_plaintext_auth_refused")
    except EgressBlocked as exc:
        raise ChannelError(exc.code) from None
    except AppriseUrlRefused as exc:
        raise ChannelError(exc.code, line=exc.line, scheme=exc.scheme) from None


def _check_text_policy(entry: ChannelType, wanted: str | None) -> None:
    """A preset whose service cuts a long text (SMS) keeps its text policy (``never``)."""
    if entry.text_policy_locked and wanted not in (None, entry.include_message_text_default):
        raise ChannelError("text_policy_locked", policy=entry.include_message_text_default)


def _check_heartbeat(entry: ChannelType, interval: int, url: str) -> None:
    """A heartbeat only on the types that carry one (``heartbeat_supported``, MSHL-131)."""
    preset = entry.id if entry.preset is not None else None
    if (interval or url) and not heartbeat_supported(entry.row_type, preset):
        raise ChannelError("heartbeat_unsupported")


def _with_secret(raw: object, channel_id: str, name: str, value: str) -> str:
    """The stored ``secrets`` column with only ``name`` replaced (or removed when empty): the
    other values stay as stored, even one the current key cannot open."""
    stored = _loads(raw)
    if value:
        stored[name] = secret_store.encrypt(
            value, secret_store.aad(SECRETS_TABLE, channel_id, name)
        )
    else:
        stored.pop(name, None)
    return json.dumps(stored, sort_keys=True)


def _utf_trim(value: str | None) -> str | None:
    return value[:LAST_ERROR_MAX] if value else value


async def _delete_in_conn(conn: aiosqlite.Connection, channel_id: str) -> None:
    """Delete a channel's row and every reference to it on an open transaction."""
    await conn.execute("DELETE FROM notification_channels WHERE id = ?", (channel_id,))
    await conn.execute("DELETE FROM notification_deliveries WHERE channel_id = ?", (channel_id,))
    await conn.execute("DELETE FROM notification_counters WHERE channel_id = ?", (channel_id,))
    await conn.execute(
        "DELETE FROM notification_state WHERE key = ?", (PENDING_SECRETS + channel_id,)
    )
    # the auto-pause step of a channel that kept failing (MSHL-130)
    await conn.execute("DELETE FROM notification_state WHERE key = ?", (autopause_key(channel_id),))
    # quiet-hours and pause counters (``quiet_hours.py``), also per account
    async with conn.execute(
        "SELECT key FROM notification_state WHERE key LIKE ? ESCAPE '\\'",
        (_like_prefix(STAT_PREFIX),),
    ) as cursor:
        stat_keys = [r[0] for r in await cursor.fetchall()]
    for key in stat_keys:
        if key.split(":", 3)[3:] == [channel_id]:
            await conn.execute("DELETE FROM notification_state WHERE key = ?", (key,))
    for prefix in ("quiet:", "pause:"):
        for sep in (":", "@"):
            await conn.execute(
                "DELETE FROM notification_state WHERE key LIKE ? ESCAPE '\\'",
                (_like_prefix(f"{prefix}{channel_id}{sep}"),),
            )
    async with conn.execute("SELECT notification_rules FROM app_settings WHERE id = 1") as c:
        row = await c.fetchone()
    rules = _loads(row[0]) if row else {}
    if rules and strip_channel_from_rules(rules, channel_id):
        await conn.execute(
            "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
            (json.dumps(rules, sort_keys=True),),
        )


class NotificationChannelRepository:
    @staticmethod
    async def list_all() -> list[Channel]:
        global _cache
        if _cache is None:
            async with db.readonly() as conn:
                async with conn.execute(
                    "SELECT * FROM notification_channels ORDER BY sort_order, created_at, id"
                ) as cursor:
                    rows = await cursor.fetchall()
            _cache = {ch.id: ch for ch in map(_row_to_channel, rows)}
        return list(_cache.values())

    @staticmethod
    async def get(channel_id: str) -> Channel | None:
        await NotificationChannelRepository.list_all()
        return (_cache or {}).get(channel_id)

    @staticmethod
    async def resolve(channel_ids: list[str]) -> list[Channel]:
        """The channels behind rule ids, skipping an orphan (logged once per id)."""
        await NotificationChannelRepository.list_all()
        known = _cache or {}
        resolved: list[Channel] = []
        for channel_id in channel_ids:
            channel = known.get(channel_id)
            if channel is None:
                if channel_id not in _orphans_logged:
                    _orphans_logged.add(channel_id)
                    logger.warning("Notification rules name an unknown channel: %s", channel_id)
                continue
            resolved.append(channel)
        return resolved

    @staticmethod
    async def get_for_send(channel_id: str) -> tuple[Channel, dict[str, str]] | None:
        """The channel and its secret values, for the dispatcher only."""
        async with db.readonly() as conn:
            row = await _fetch(conn, channel_id)
        if row is None:
            return None
        decoded, _unreadable = decode_secrets(row["secrets"], channel_id)
        secrets = {k: v for k, v in decoded.items() if isinstance(v, str) and v}
        return _row_to_channel(row), secrets

    @staticmethod
    async def prepare(body: ChannelCreate) -> tuple[ChannelType, dict[str, Any], dict[str, str]]:
        """Every check of a creation but the write: the type, the config (public and
        secret parts), the required fields and the destination (egress guard). Also
        what ``POST /channels/test`` runs, so a test and a save refuse the same things."""
        entry = get_type(body.type, body.preset)
        _check_text_policy(entry, body.include_message_text)
        check_templates(entry.row_type, body.template_dm, body.template_channel)
        public, secrets = _split_config(entry, body.config)
        missing = _required_missing(entry, public, secrets)
        if missing:
            raise ChannelError("config_required", fields=missing)
        await _check_destination(
            entry, public, secrets, body.allow_private_network, body.verify_tls
        )
        heartbeat_url = "" if body.heartbeat_url == REDACTED else body.heartbeat_url.strip()
        _check_heartbeat(entry, body.heartbeat_interval_s, heartbeat_url)
        if heartbeat_url:
            # Through the egress guard now, and again on every ping (``heartbeat.ping``).
            await check_ping_url(
                heartbeat_url,
                allow_private_network=body.allow_private_network,
                verify_tls=body.verify_tls,
            )
            secrets[HEARTBEAT_URL_KEY] = heartbeat_url
        return entry, public, secrets

    @staticmethod
    async def create(body: ChannelCreate) -> Channel:
        entry = get_type(body.type, body.preset)
        if entry.singleton_id is None:
            candidate = body.id or f"{entry.id}-{uuid.uuid4().hex[:8]}"
            if is_builtin(candidate) or ":" in candidate:
                raise ChannelError("channel_id_reserved")
        entry, public, secrets = await NotificationChannelRepository.prepare(body)
        if entry.singleton_id is not None:
            channel_id = entry.singleton_id
        else:
            channel_id = candidate
        # NULL rate columns mean "the type's default limits" (registry).
        async with db.tx() as conn:
            if await _fetch(conn, channel_id) is not None:
                raise ChannelError("channel_exists", 409)
            async with conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM notification_channels"
            ) as cursor:
                sort_order = (await cursor.fetchone())[0]  # type: ignore[index]
            await conn.execute(
                """
                INSERT INTO notification_channels
                    (id, type, preset, name, enabled, language, config, secrets,
                     allow_private_network, include_message_text, verify_tls,
                     template_dm, template_channel,
                     quiet_hours_exempt, rate_per_min, rate_per_hour, rate_per_day,
                     sort_order, created_at, heartbeat_interval_s, heartbeat_method,
                     digest_interval_s)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    channel_id,
                    entry.row_type,
                    body.preset,
                    body.name.strip(),
                    1 if body.enabled else 0,
                    body.language,
                    json.dumps(public, sort_keys=True),
                    encode_secrets(secrets, channel_id),
                    1 if body.allow_private_network else 0,
                    body.include_message_text or entry.include_message_text_for(public),
                    1 if body.verify_tls else 0,
                    body.template_dm,
                    body.template_channel,
                    1
                    if (
                        entry.quiet_hours_exempt_default
                        if body.quiet_hours_exempt is None
                        else body.quiet_hours_exempt
                    )
                    else 0,
                    body.rate_per_min,
                    body.rate_per_hour,
                    body.rate_per_day,
                    sort_order,
                    int(time.time()),
                    body.heartbeat_interval_s,
                    body.heartbeat_method,
                    body.digest_interval_s,
                ),
            )
            row = await _fetch(conn, channel_id)
        invalidate_cache()
        assert row is not None
        return _row_to_channel(row)

    @staticmethod
    async def update(channel_id: str, patch: ChannelUpdate) -> Channel:
        found = await NotificationChannelRepository.get_for_send(channel_id)
        if found is None:
            raise ChannelError("channel_missing", 404)
        current, stored_secrets = found
        if patch.type is not None and patch.type != current.type:
            raise ChannelError("type_immutable")
        entry = get_type(current.type, current.preset)
        _check_text_policy(entry, patch.include_message_text)
        template_dm, template_channel = check_templates(
            entry.row_type,
            current.template_dm if patch.template_dm is None else patch.template_dm,
            current.template_channel if patch.template_channel is None else patch.template_channel,
        )

        public = dict(current.config)
        secrets = dict(stored_secrets)
        # The heartbeat URL (MSHL-131) shares the secrets column but not the config model.
        old_url = secrets.pop(HEARTBEAT_URL_KEY, "")
        config_changed = False
        if patch.config is not None:
            config_changed = True
            incoming = dict(patch.config)
            merged: dict[str, Any] = {**public}
            for name in entry.secret_fields:
                if name in secrets:
                    merged[name] = secrets[name]
            replaced: set[str] = set()
            for key, value in incoming.items():
                if key in entry.secret_fields:
                    if value is None or value == REDACTED:
                        continue
                    replaced.add(key)
                merged[key] = value
            if entry.destination_keys and destination_changed(
                entry.id,
                {**public, **secrets},
                merged,
                {entry.id: dict.fromkeys(entry.destination_keys, "")},
            ):
                kept = [f for f in entry.secret_follows if secrets.get(f) and f not in replaced]
                if kept:
                    raise ChannelError("secret_required", fields=kept)
            public, secrets = _split_config(entry, merged)
        if patch.heartbeat_url is None or patch.heartbeat_url == REDACTED:
            new_url = old_url
        else:
            new_url = patch.heartbeat_url.strip()
        url_changed = new_url != old_url
        if new_url:
            secrets[HEARTBEAT_URL_KEY] = new_url
        interval = (
            current.heartbeat_interval_s
            if patch.heartbeat_interval_s is None
            else patch.heartbeat_interval_s
        )
        _check_heartbeat(entry, interval, new_url)
        # A stored secret the key cannot open (MSHL-133) counts as present while the config
        # is not edited: a PATCH of the name, the switch, the limits... is saved and the
        # channel keeps reading as off (``secret_unreadable``) until the config is saved
        # again with its secrets, or the matching secrets.key comes back.
        missing = _required_missing(entry, public, secrets)
        if not config_changed:
            # An unreadable secret is still stored (the matching key opens it again).
            missing = [f for f in missing if f not in current.secret_unreadable]
        if missing:
            raise ChannelError("config_required", fields=missing)

        pending = await _pending_secrets(channel_id)
        still_pending: list[str] = []
        if pending:
            # A restored channel waits for the secrets the backup left out (MSHL-117): it stays
            # off until each is typed again or cleared on purpose (sent as "").
            cleared = {
                k for k, v in (patch.config or {}).items() if k in entry.secret_fields and v == ""
            }
            if patch.heartbeat_url == "":
                cleared.add(HEARTBEAT_URL_KEY)
            still_pending = [n for n in pending if not secrets.get(n) and n not in cleared]
            wanted_on = current.enabled if patch.enabled is None else patch.enabled
            if still_pending and wanted_on:
                raise ChannelError("secret_missing", fields=still_pending)

        allow_private = (
            current.allow_private_network
            if patch.allow_private_network is None
            else patch.allow_private_network
        )
        verify_tls = current.verify_tls if patch.verify_tls is None else patch.verify_tls
        if (
            config_changed
            or allow_private != current.allow_private_network
            or verify_tls != current.verify_tls
        ):
            await _check_destination(entry, public, secrets, allow_private, verify_tls)
        if new_url and (
            url_changed
            or allow_private != current.allow_private_network
            or verify_tls != current.verify_tls
        ):
            await check_ping_url(
                new_url, allow_private_network=allow_private, verify_tls=verify_tls
            )
        if config_changed:
            # The grandfathering banner was about the old destination.
            public.pop("private_network_grandfathered", None)

        sets: dict[str, Any] = {}
        if config_changed:
            sets["config"] = json.dumps(public, sort_keys=True)
            sets["secrets"] = encode_secrets(secrets, channel_id)
        for column in (
            "name",
            "enabled",
            "language",
            "allow_private_network",
            "include_message_text",
            "verify_tls",
            "quiet_hours_exempt",
            "rate_per_min",
            "rate_per_hour",
            "rate_per_day",
            "heartbeat_interval_s",
            "heartbeat_method",
            "digest_interval_s",
        ):
            if column in patch.model_fields_set and column != "type":
                value = getattr(patch, column)
                if value is None and column not in (
                    "rate_per_min",
                    "rate_per_hour",
                    "rate_per_day",
                ):
                    continue
                if isinstance(value, bool):
                    value = 1 if value else 0
                sets[column] = (
                    value.strip() if isinstance(value, str) and column == "name" else value
                )
        if template_dm != current.template_dm:
            sets["template_dm"] = template_dm
        if template_channel != current.template_channel:
            sets["template_channel"] = template_channel
        if pending and not still_pending:
            sets["last_error"] = None
        async with db.tx() as conn:
            if pending and not still_pending:
                await conn.execute(
                    "DELETE FROM notification_state WHERE key = ?", (PENDING_SECRETS + channel_id,)
                )
            if url_changed and not config_changed:
                stored = await _fetch(conn, channel_id)
                raw = stored["secrets"] if stored is not None else None
                sets["secrets"] = _with_secret(raw, channel_id, HEARTBEAT_URL_KEY, new_url)
            if sets:
                assignments = ", ".join(f"{col} = ?" for col in sets)
                await conn.execute(
                    f"UPDATE notification_channels SET {assignments} WHERE id = ?",  # noqa: S608
                    (*sets.values(), channel_id),
                )
            row = await _fetch(conn, channel_id)
        invalidate_cache()
        assert row is not None
        return _row_to_channel(row)

    @staticmethod
    async def delete(channel_id: str) -> None:
        """Delete a channel and every reference to it, in one transaction."""
        if is_builtin(channel_id):
            raise ChannelError("builtin_channel", 403)
        async with db.tx() as conn:
            if await _fetch(conn, channel_id) is None:
                raise ChannelError("channel_missing", 404)
            await _delete_in_conn(conn, channel_id)
        invalidate_cache()
        _orphans_logged.discard(channel_id)

    @staticmethod
    async def delete_if(
        channel_id: str, check: Callable[[aiosqlite.Connection], Awaitable[bool]]
    ) -> bool | None:
        """Delete a channel only if ``check`` (run on the same transaction, so no write can
        land between the check and the delete) returns True. ``check`` must use only the
        connection it gets (the DB lock is not reentrant). None: no such channel; False:
        kept; True: deleted. Any error rolls the whole transaction back."""
        if is_builtin(channel_id):
            raise ChannelError("builtin_channel", 403)
        async with db.tx() as conn:
            if await _fetch(conn, channel_id) is None:
                return None
            if not await check(conn):
                return False
            await _delete_in_conn(conn, channel_id)
        invalidate_cache()
        _orphans_logged.discard(channel_id)
        return True

    @staticmethod
    async def mirror_legacy(built: list[dict[str, Any]], legacy_ids: tuple[str, ...]) -> None:
        """Make the ``legacy:*`` channels match the 4.18.x destinations (``built``, in the
        shape of migration 113's ``build_channels``): upsert the configured ones, keeping
        what the user set on the channel itself, and delete the ones no longer configured."""
        by_id = {c["id"]: c for c in built}
        async with db.tx() as conn:
            for channel_id, channel in by_id.items():
                config, secrets = (
                    json.dumps(channel["config"], sort_keys=True),
                    encode_secrets(channel["secrets"], channel_id),
                )
                if await _fetch(conn, channel_id) is not None:
                    await conn.execute(
                        "UPDATE notification_channels SET config = ?, secrets = ?, "
                        "allow_private_network = ? WHERE id = ?",
                        (config, secrets, channel["allow_private_network"], channel_id),
                    )
                    continue
                await conn.execute(
                    """
                    INSERT INTO notification_channels
                        (id, type, name, enabled, language, config, secrets,
                         allow_private_network, include_message_text, verify_tls,
                         quiet_hours_exempt, sort_order, created_at)
                    VALUES (?, ?, ?, 1, 'auto', ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        channel_id,
                        channel["type"],
                        channel["name"],
                        config,
                        secrets,
                        channel["allow_private_network"],
                        channel["include_message_text"],
                        channel["quiet_hours_exempt"],
                        channel["sort_order"],
                        channel["created_at"],
                    ),
                )
        invalidate_cache()
        for channel_id in legacy_ids:
            if channel_id not in by_id and await NotificationChannelRepository.get(channel_id):
                await NotificationChannelRepository.delete(channel_id)

    @staticmethod
    async def record_result(channel_id: str, *, ok: bool, error: str | None = None) -> None:
        """Status update after a send. ``error`` must already be scrubbed by the caller."""
        now = int(time.time())
        async with db.tx() as conn:
            if ok:
                await conn.execute(
                    "UPDATE notification_channels SET last_ok_at = ?, last_error = NULL, "
                    "failure_count = 0 WHERE id = ?",
                    (now, channel_id),
                )
            else:
                await conn.execute(
                    "UPDATE notification_channels SET last_error = ?, "
                    "failure_count = failure_count + 1 WHERE id = ?",
                    (_utf_trim(error), channel_id),
                )
        invalidate_cache()

    @staticmethod
    async def get_rules() -> dict[str, Any]:
        """``app_settings.notification_rules`` (``{}`` when unset)."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT notification_rules FROM app_settings WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        return _loads(row[0]) if row else {}

    @staticmethod
    async def set_rules(rules: dict[str, Any]) -> None:
        """Replace ``app_settings.notification_rules`` (the caller merged what it keeps)."""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE app_settings SET notification_rules = ? WHERE id = 1",
                (json.dumps(rules, sort_keys=True),),
            )

    @staticmethod
    async def set_last_ok_at(channel_id: str, at: int) -> None:
        """The dispatcher's periodic flush: the last success, without touching errors."""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE notification_channels SET last_ok_at = MAX(COALESCE(last_ok_at, 0), ?) "
                "WHERE id = ?",
                (at, channel_id),
            )
        invalidate_cache()

    @staticmethod
    async def clear_failures(channel_id: str) -> None:
        """The Resume button: the failure count and the last error start over."""
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE notification_channels SET failure_count = 0, last_error = NULL "
                "WHERE id = ?",
                (channel_id,),
            )
        invalidate_cache()

    @staticmethod
    async def set_paused_until(channel_id: str, until: int | None) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE notification_channels SET paused_until = ? WHERE id = ?",
                (until, channel_id),
            )
        invalidate_cache()


def strip_channel_from_rules(rules: dict[str, Any], channel_id: str) -> bool:
    """Remove ``channel_id`` from every list and map of ``rules`` (in place).

    ``events[*].channels`` (and ``contact_types``), ``conversations[key]`` (a key left
    empty is dropped), ``keywords[*].channels`` and ``watched_nodes[*].channels``.
    Returns whether anything changed.
    """
    changed = False

    def drop(owner: dict[str, Any], key: str) -> None:
        nonlocal changed
        ids = owner.get(key)
        if isinstance(ids, list) and channel_id in ids:
            owner[key] = [i for i in ids if i != channel_id]
            changed = True

    for event in (rules.get("events") or {}).values():
        if not isinstance(event, dict):
            continue
        drop(event, "channels")
        types = event.get("contact_types")
        if isinstance(types, dict):
            for kind in types:
                drop(types, kind)
    conversations = rules.get("conversations")
    if isinstance(conversations, dict):
        for key in list(conversations):
            entry = conversations[key]
            if isinstance(entry, dict) and channel_id in entry:
                del entry[channel_id]
                changed = True
                if not entry:
                    del conversations[key]
    for section in ("keywords", "watched_nodes"):
        for item in rules.get(section) or []:
            if isinstance(item, dict):
                drop(item, "channels")
    return changed


class NotificationJournalRepository:
    """Deliveries ring (200 rows per channel), hourly counters and the dedup state."""

    @staticmethod
    async def add_delivery(
        channel_id: str,
        event: str,
        *,
        status: str,
        dedup_key: str = "",
        error: str | None = None,
        attempts: int = 1,
    ) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "INSERT INTO notification_deliveries "
                "(channel_id, event, dedup_key, status, error, attempts, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    channel_id,
                    event,
                    dedup_key,
                    status,
                    _utf_trim(error),
                    attempts,
                    int(time.time()),
                ),
            )
            await conn.execute(
                "DELETE FROM notification_deliveries WHERE channel_id = ? AND id NOT IN "
                "(SELECT id FROM notification_deliveries WHERE channel_id = ? "
                "ORDER BY id DESC LIMIT ?)",
                (channel_id, channel_id, DELIVERIES_PER_CHANNEL),
            )

    @staticmethod
    async def add_deliveries(rows: list[dict[str, Any]]) -> None:
        """Several rows in one transaction (the dispatcher batches them), then the trim."""
        now = int(time.time())
        async with db.tx() as conn:
            await conn.executemany(
                "INSERT INTO notification_deliveries "
                "(channel_id, event, dedup_key, status, error, attempts, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        row["channel_id"],
                        row["event"],
                        row.get("dedup_key") or "",
                        row["status"],
                        _utf_trim(row.get("error")),
                        row.get("attempts", 1),
                        now,
                    )
                    for row in rows
                ],
            )
            hour = now // 3600
            tallies: dict[tuple[str, str], int] = {}
            for row in rows:
                if row["status"] in STAT_STATUSES:
                    key = (row["status"], row["channel_id"])
                    tallies[key] = tallies.get(key, 0) + 1
            for (status, channel_id), amount in tallies.items():
                await conn.execute(
                    "INSERT INTO notification_state (key, value, expires_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "value = CAST(CAST(value AS INTEGER) + ? AS TEXT)",
                    (
                        f"{STAT_PREFIX}{status}:{hour}:{channel_id}",
                        str(amount),
                        (hour + STAT_WINDOW_H + 1) * 3600,
                        amount,
                    ),
                )
            for channel_id in {row["channel_id"] for row in rows}:
                await conn.execute(
                    "DELETE FROM notification_deliveries WHERE channel_id = ? AND id NOT IN "
                    "(SELECT id FROM notification_deliveries WHERE channel_id = ? "
                    "ORDER BY id DESC LIMIT ?)",
                    (channel_id, channel_id, DELIVERIES_PER_CHANNEL),
                )

    @staticmethod
    async def list_deliveries(
        channel_id: str | None, limit: int = DELIVERIES_PER_CHANNEL
    ) -> list[dict]:
        """The journal, newest first: one channel, or every channel when ``None``."""
        where = "WHERE channel_id = ? " if channel_id is not None else ""
        params = (channel_id, limit) if channel_id is not None else (limit,)
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT id, channel_id, event, dedup_key, status, error, attempts, created_at "
                f"FROM notification_deliveries {where}ORDER BY id DESC LIMIT ?",  # noqa: S608
                params,
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    @staticmethod
    async def prune_deliveries() -> int:
        """The daily prune: at most 200 rows per channel, none for a deleted channel."""
        async with db.tx() as conn:
            async with conn.execute(
                """
                DELETE FROM notification_deliveries WHERE id IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (
                            PARTITION BY channel_id ORDER BY id DESC) AS rn
                        FROM notification_deliveries
                    ) WHERE rn > ?
                ) OR channel_id NOT IN (SELECT id FROM notification_channels)
                """,
                (DELIVERIES_PER_CHANNEL,),
            ) as cursor:
                return cursor.rowcount

    @staticmethod
    async def add_sent(channel_id: str, hour: int, count: int = 1) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "INSERT INTO notification_counters (channel_id, hour, sent) VALUES (?, ?, ?) "
                "ON CONFLICT(channel_id, hour) DO UPDATE SET sent = sent + excluded.sent",
                (channel_id, hour, count),
            )

    @staticmethod
    async def sent_since(channel_id: str, since_hour: int) -> int:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT COALESCE(SUM(sent), 0) FROM notification_counters "
                "WHERE channel_id = ? AND hour >= ?",
                (channel_id, since_hour),
            ) as cursor:
                return int((await cursor.fetchone())[0])  # type: ignore[index]

    @staticmethod
    async def hourly_counts(channel_id: str | None, since_hour: int) -> list[dict[str, Any]]:
        """Hourly send counters from ``since_hour`` (one channel, or all), oldest first."""
        query = "SELECT channel_id, hour, sent FROM notification_counters WHERE hour >= ?"
        params: list[Any] = [since_hour]
        if channel_id is not None:
            query += " AND channel_id = ?"
            params.append(channel_id)
        async with db.readonly() as conn:
            async with conn.execute(query + " ORDER BY hour", params) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    @staticmethod
    async def window_totals(channel_id: str | None, now: int | None = None) -> dict[str, int]:
        """Exact counts of the last 24 hours: ``sent`` (hourly counters), ``suppressed_quiet``
        (held by the quiet hours) and ``aggregated`` (folded into a summary), for one channel
        or all of them."""
        since_hour = (int(time.time()) if now is None else now) // 3600 - (STAT_WINDOW_H - 1)
        totals = {"sent": 0, **dict.fromkeys(STAT_STATUSES, 0)}
        for row in await NotificationJournalRepository.hourly_counts(channel_id, since_hour):
            totals["sent"] += row["sent"]
        async with db.readonly() as conn:
            for status in STAT_STATUSES:
                async with conn.execute(
                    "SELECT key, value FROM notification_state WHERE key LIKE ? ESCAPE '\\'",
                    (_like_prefix(f"{STAT_PREFIX}{status}:"),),
                ) as cursor:
                    rows = await cursor.fetchall()
                for key, value in rows:
                    hour_text, _, owner = key[len(STAT_PREFIX) + len(status) + 1 :].partition(":")
                    if (channel_id is not None and owner != channel_id) or not hour_text.isdigit():
                        continue
                    if int(hour_text) >= since_hour:
                        totals[status] += int(value)
        return totals

    @staticmethod
    async def prune_counters(before_hour: int) -> None:
        async with db.tx() as conn:
            await conn.execute("DELETE FROM notification_counters WHERE hour < ?", (before_hour,))

    @staticmethod
    async def state_get(key: str, now: int | None = None) -> str | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT value FROM notification_state WHERE key = ? "
                "AND (expires_at IS NULL OR expires_at > ?)",
                (key, int(time.time()) if now is None else now),
            ) as cursor:
                row = await cursor.fetchone()
        return row[0] if row else None

    @staticmethod
    async def state_set(key: str, value: str, expires_at: int | None = None) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "INSERT INTO notification_state (key, value, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "expires_at = excluded.expires_at",
                (key, value, expires_at),
            )

    @staticmethod
    async def state_load_prefix(prefix: str, now: int) -> dict[str, int]:
        """``{key: expires_at}`` of the live rows whose key starts with ``prefix``."""
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT key, expires_at FROM notification_state "
                "WHERE key LIKE ? ESCAPE '\\' AND expires_at IS NOT NULL AND expires_at > ?",
                (like, now),
            ) as cursor:
                return {row[0]: row[1] for row in await cursor.fetchall()}

    @staticmethod
    async def state_set_many(rows: dict[str, tuple[str, int | None]]) -> None:
        """Upsert ``{key: (value, expires_at)}`` in one transaction."""
        if not rows:
            return
        async with db.tx() as conn:
            await conn.executemany(
                "INSERT INTO notification_state (key, value, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "expires_at = excluded.expires_at",
                [(key, value, expires_at) for key, (value, expires_at) in rows.items()],
            )

    @staticmethod
    async def counter_incr(key: str) -> None:
        """Add one to a counter of ``notification_state``: one UPDATE for a known key, an INSERT
        only the first time (quiet hours: one cheap write per suppressed event)."""
        await NotificationJournalRepository.counter_add(key, 1)

    @staticmethod
    async def counter_add(key: str, amount: int) -> None:
        """Add ``amount`` to a counter (put back what a failed digest had taken)."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE notification_state SET value = CAST(CAST(value AS INTEGER) + ? AS TEXT) "
                "WHERE key = ?",
                (amount, key),
            ) as cursor:
                updated = cursor.rowcount
            if not updated:
                await conn.execute(
                    "INSERT INTO notification_state (key, value, expires_at) VALUES (?, ?, NULL) "
                    "ON CONFLICT(key) DO UPDATE SET "
                    "value = CAST(CAST(value AS INTEGER) + ? AS TEXT)",
                    (key, str(amount), amount),
                )

    @staticmethod
    async def counters_take(prefix: str) -> dict[str, int]:
        """Read and delete every counter whose key starts with ``prefix``, atomically."""
        like = _like_prefix(prefix)
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT key, value FROM notification_state WHERE key LIKE ? ESCAPE '\\'",
                (like,),
            ) as cursor:
                rows = await cursor.fetchall()
            if rows:
                await conn.execute(
                    "DELETE FROM notification_state WHERE key LIKE ? ESCAPE '\\'", (like,)
                )
        out: dict[str, int] = {}
        for key, value in rows:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                continue
        return out

    @staticmethod
    async def state_load_values(prefix: str) -> dict[str, str]:
        """``{key: value}`` of every row (expiring or not) whose key starts with ``prefix``."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT key, value FROM notification_state WHERE key LIKE ? ESCAPE '\\'",
                (_like_prefix(prefix),),
            ) as cursor:
                return {row[0]: row[1] for row in await cursor.fetchall()}

    @staticmethod
    async def state_prune(now: int | None = None) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "DELETE FROM notification_state WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (int(time.time()) if now is None else now,),
            )

    @staticmethod
    async def state_claim(key: str, expires_at: int, now: int) -> bool:
        """Take ``key`` until ``expires_at`` unless a live row holds it: True for the caller
        that took it (an expired row is replaced). Atomic, so two callers never both win."""
        async with db.tx() as conn:
            await conn.execute(
                "DELETE FROM notification_state WHERE key = ? "
                "AND expires_at IS NOT NULL AND expires_at <= ?",
                (key, now),
            )
            async with conn.execute(
                "INSERT OR IGNORE INTO notification_state (key, value, expires_at) "
                "VALUES (?, '1', ?)",
                (key, expires_at),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def state_delete(key: str) -> None:
        async with db.tx() as conn:
            await conn.execute("DELETE FROM notification_state WHERE key = ?", (key,))

    @staticmethod
    async def state_trim_prefix(prefix: str, keep: int) -> None:
        """Keep the ``keep`` rows of ``prefix`` that expire last, delete the others."""
        like = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        async with db.tx() as conn:
            await conn.execute(
                "DELETE FROM notification_state WHERE key LIKE ? ESCAPE '\\' AND key NOT IN ("
                "SELECT key FROM notification_state WHERE key LIKE ? ESCAPE '\\' "
                "ORDER BY expires_at DESC, key LIMIT ?)",
                (like, like, keep),
            )


def _like_prefix(prefix: str) -> str:
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


__all__ = [
    "BUILTIN_PUSH_ID",
    "DELIVERIES_PER_CHANNEL",
    "NotificationChannelRepository",
    "NotificationJournalRepository",
    "invalidate_cache",
    "strip_channel_from_rules",
]
