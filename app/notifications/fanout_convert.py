"""Fanout Apprise rows as notification channels (MSHL-134).

The fanout Apprise type is deprecated in 5.0.0 (removed in 5.2.0, T34). Nothing changes at
upgrade: a user converts each row by hand (``POST /api/fanout/{id}/convert-to-notification-channel``).

**Conversion.** ``convert_fanout_row`` creates a channel through the one creation path
(``NotificationChannelRepository.create``: the egress guard, the secret store), adds the rules
that reproduce the row's scope, then disables the row and stores ``config.converted_to``.

- A single URL a preset can rebuild exactly (``match_preset``: Telegram, Gotify) becomes that
  preset, anything else a generic ``apprise`` channel. The URLs are secrets: sealed in the
  channel's ``secrets``, never in a response or a log line.
- ``markdown_format`` becomes ``config.format``; ``preserve_identity`` (on by default) is
  baked into the stored Discord URLs (``avatar=no``), as the fanout module sent them.
- ``body_format_dm`` / ``body_format_channel`` become ``template_dm`` / ``template_channel``
  (MSHL-127) when set; one the template checks refuse (over 500 characters) is dropped and
  named in ``notes``. Empty formats keep the channel's default body.
- The scope becomes rules (``scope_to_rules``): ``"all"`` lists the channel on ``message.dm``
  and ``message.channel``; a list of keys becomes ``true`` conversation exceptions; an
  ``{"except": [...]}`` filter lists the channel and adds ``false`` exceptions. The rules
  notify a channel with its own key only through an exception, where the fanout ``"all"``
  reached it: ``notes`` carries ``private_channels_need_exception``.
- ``include_outgoing`` has no equivalent (notifications never carry outgoing messages):
  ``notes`` carries ``include_outgoing_unsupported``.
- The channel keeps the row's text (``include_message_text: always``), its
  ``allow_private_network`` and its on/off state.
- Idempotent: a row whose ``converted_to`` channel still exists is disabled again and that
  channel returned, nothing created. Not atomic across the two tables, so the row is marked
  ``converting_to`` (the new channel's id) before the channel is created: a retry after a
  failure between the steps reuses that channel, or creates it under that id, never a second. ``into`` (the duplicate prompt) attaches the row to an
  existing channel instead: only the rules are added, the channel's settings stay. A channel
  an interrupted conversion left under ``converting_to`` is deleted first (MSHL-403), with its
  secrets and rules, unless it is the ``into`` channel itself.

**Duplicates.** ``fanout_duplicates`` compares a channel's destination with the active
(enabled, not converted) fanout Apprise rows. A converted row turned back on (the UI asks
first) is not reported: the owner chose to keep both, and its channel already exists. URLs are compared through an HMAC keyed per
process (``_fingerprints``) and ``hmac.compare_digest``: no URL or token is returned or
logged, only the matching row's id and name.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets as secrets_mod
import uuid
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit

from app.net.apprise_urls import split_apprise_urls
from app.notifications.channels import Channel, ChannelCreate, ChannelError, get_type

logger = logging.getLogger(__name__)

CONVERTED_TO = "converted_to"
# Write-ahead mark: the id of the channel a conversion is creating, set on the row before the
# channel exists and dropped once the row is disabled. A retry after a failure in between
# reuses (or creates under) that id instead of making a second channel.
CONVERTING_TO = "converting_to"
CONVERSION_KEYS = (CONVERTED_TO, CONVERTING_TO)
# The conversation keys the rules accept (``routers/notifications.CONVERSATION_KEY``).
_CONVERSATION_KEY = re.compile(r"^(contact|channel)-[0-9A-Za-z]{1,128}$")
_FINGERPRINT_KEY = secrets_mod.token_bytes(32)

_TELEGRAM = re.compile(
    r"^tgram://(?P<bot_token>[0-9]+:[A-Za-z0-9_-]+)/"
    r"(?P<chat_id>-?[0-9]{1,32})(?::(?P<topic_id>[0-9]{1,12}))?/?$"
)
_GOTIFY = re.compile(
    r"^(?P<scheme>gotifys?)://(?P<netloc>[A-Za-z0-9.\-\[\]:]+)(?P<path>(?:/[A-Za-z0-9_.~-]+)*?)"
    r"/(?P<token>[A-Za-z0-9_.-]{4,128})/?"
    r"(?:\?priority=(?P<priority>low|moderate|normal|high|emergency))?$"
)


@dataclass
class Conversion:
    channel: Channel
    fanout: dict[str, Any]
    already_converted: bool = False
    notes: list[str] = field(default_factory=list)
    # True only when this call created the channel (a retry that found it is False).
    created: bool = False


class ConversionError(Exception):
    """A coded refusal (``{code, params}``), never a URL."""

    def __init__(self, code: str, status: int = 400, **params: Any) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.params = params


# ── Presets ──────────────────────────────────────────────────────────────


def match_preset(url: str) -> tuple[str, dict[str, str]] | None:
    """The preset (id and fields) that compiles back to exactly ``url``, or None."""
    from app.notifications.presets import PresetError, compile_url, preset_for

    candidates: list[tuple[str, dict[str, str], str]] = []
    line = url.strip()
    if m := _TELEGRAM.match(line):
        values = {k: v for k, v in m.groupdict().items() if v}
        candidates.append(("telegram", values, line))
    if m := _GOTIFY.match(line):
        scheme = "https" if m["scheme"] == "gotifys" else "http"
        priority = m["priority"] or "normal"
        values = {
            "server": f"{scheme}://{m['netloc']}{m['path']}",
            "token": m["token"],
            "priority": priority,
        }
        expected = line if m["priority"] else f"{line.rstrip('/')}/?priority={priority}"
        candidates.append(("gotify", values, expected))
    for preset_id, values, expected in candidates:
        preset = preset_for(preset_id)
        if preset is None:
            continue
        try:
            compiled = compile_url(preset, values)
        except PresetError:
            continue
        if unquote(compiled).rstrip("/") == unquote(expected).rstrip("/"):
            return preset_id, values
    return None


# ── Scope to rules ───────────────────────────────────────────────────────


def scope_to_rules(scope: dict[str, Any]) -> tuple[list[str], dict[str, bool], list[str]]:
    """The events to list the channel on, the conversation exceptions and the notes."""
    messages = scope.get("messages", "all")
    events: list[str] = []
    exceptions: dict[str, bool] = {}
    notes: list[str] = []
    if messages == "all":
        messages = {"channels": "all", "contacts": "all"}
    if not isinstance(messages, dict):
        return events, exceptions, notes
    for sub, event, prefix in (
        ("contacts", "message.dm", "contact"),
        ("channels", "message.channel", "channel"),
    ):
        selected = messages.get(sub, "none")
        if selected == "all":
            events.append(event)
        elif isinstance(selected, list):
            for key in selected:
                exceptions[f"{prefix}-{key}"] = True
        elif isinstance(selected, dict) and isinstance(selected.get("except"), list):
            events.append(event)
            for key in selected["except"]:
                exceptions[f"{prefix}-{key}"] = False
        else:
            continue
        if prefix == "channel" and event in events:
            notes.append("private_channels_need_exception")
    exceptions = {k: v for k, v in exceptions.items() if _CONVERSATION_KEY.match(k)}
    return events, exceptions, notes


async def _add_rules(channel_id: str, events: list[str], exceptions: dict[str, bool]) -> None:
    from app.notifications.legacy_sync import sync_columns
    from app.repository.notification_channels import NotificationChannelRepository

    if not events and not exceptions:
        return
    rules = await NotificationChannelRepository.get_rules()
    stored_events = dict(rules.get("events") or {})
    for event_id in events:
        entry = dict(stored_events.get(event_id) or {})
        ids = [str(i) for i in entry.get("channels") or []]
        if channel_id not in ids:
            ids.append(channel_id)
        entry["channels"] = ids
        stored_events[event_id] = entry
    rules["events"] = stored_events
    conversations = dict(rules.get("conversations") or {})
    for key, value in exceptions.items():
        conversations[key] = {**(conversations.get(key) or {}), channel_id: value}
    rules["conversations"] = conversations
    rules.setdefault("version", 1)
    await NotificationChannelRepository.set_rules(rules)
    if events:
        await sync_columns()


# ── Conversion ───────────────────────────────────────────────────────────


def _row_urls(config: dict[str, Any]) -> list[str]:
    from app.fanout.apprise_mod import _normalize_discord_url

    urls = split_apprise_urls(str(config.get("urls") or ""))
    if config.get("preserve_identity", True):
        urls = [_normalize_discord_url(u) for u in urls]
    return urls


def _templates(config: dict[str, Any], notes: list[str]) -> dict[str, str]:
    from app.notifications.templates import validate_template

    out: dict[str, str] = {}
    for source, target in (
        ("body_format_dm", "template_dm"),
        ("body_format_channel", "template_channel"),
    ):
        value = config.get(source)
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            out[target] = validate_template(value, field=target)
        except ChannelError:
            notes.append(f"{target}_dropped")
    return out


def _channel_body(row: dict[str, Any], notes: list[str]) -> ChannelCreate:
    config = row.get("config") or {}
    urls = _row_urls(config)
    if not urls:
        raise ConversionError("config_required", fields=["urls"])
    templates = _templates(config, notes)
    markdown = bool(config.get("markdown_format", True))
    common: dict[str, Any] = {
        "name": (str(row.get("name") or "Apprise").strip() or "Apprise")[:80],
        "enabled": bool(row.get("enabled")),
        "allow_private_network": bool(config.get("allow_private_network", False)),
        "include_message_text": "always",
        **templates,
    }
    matched = match_preset(urls[0]) if len(urls) == 1 else None
    if matched is not None:
        from app.notifications.presets import preset_for

        preset = preset_for(matched[0])
        # A Markdown template on a plain-text service would show its asterisks.
        if preset is not None and not (templates and markdown and preset.format == "text"):
            if preset.text_policy_locked:
                common["include_message_text"] = None
            return ChannelCreate(type="apprise", preset=matched[0], config=matched[1], **common)
    return ChannelCreate(
        type="apprise",
        config={"urls": "\n".join(urls), "format": "markdown" if markdown else "text"},
        **common,
    )


async def _disable_row(row: dict[str, Any], channel_id: str) -> dict[str, Any]:
    from app.fanout.manager import fanout_manager
    from app.repository.fanout import FanoutConfigRepository

    config = {**(row.get("config") or {}), CONVERTED_TO: channel_id}
    config.pop(CONVERTING_TO, None)
    updated = await FanoutConfigRepository.update(row["id"], enabled=False, config=config)
    await fanout_manager.reload_config(row["id"])
    if updated is None:
        raise ConversionError("fanout_missing", 404)
    return updated


async def _drop_pending_channel(channel_id: str) -> None:
    """Delete the channel an interrupted conversion created (``CONVERTING_TO``) when the
    duplicate prompt attaches the row elsewhere: it would be left orphaned. The row keeps the
    mark until ``_disable_row``, so a retry finds the channel gone and deletes nothing."""
    from app.repository.notification_channels import NotificationChannelRepository

    try:
        await NotificationChannelRepository.delete(channel_id)
    except ChannelError as exc:
        if exc.code != "channel_missing":
            raise
        return
    logger.info("Pending conversion channel %s deleted (row attached elsewhere)", channel_id)


async def convert_fanout_row(config_id: str, *, into: str | None = None) -> Conversion:
    """Convert one fanout Apprise row (module doc). Raises ``ConversionError`` or
    ``ChannelError`` (the channel checks), coded, never with a URL."""
    from app.fanout.manager import is_reserved_fanout_id
    from app.repository.fanout import FanoutConfigRepository
    from app.repository.notification_channels import NotificationChannelRepository

    row = None if is_reserved_fanout_id(config_id) else await FanoutConfigRepository.get(config_id)
    if row is None:
        raise ConversionError("fanout_missing", 404)
    if row["type"] != "apprise":
        raise ConversionError("fanout_not_apprise")
    if row.get("secret_unreadable"):
        raise ConversionError("secret_unreadable", 409)
    config = row.get("config") or {}

    previous = config.get(CONVERTED_TO)
    if isinstance(previous, str) and previous:
        channel = await NotificationChannelRepository.get(previous)
        if channel is not None and (into is None or into == previous):
            fanout = await _disable_row(row, previous) if row.get("enabled") else row
            return Conversion(channel, fanout, already_converted=True)

    notes: list[str] = []
    if config.get("include_outgoing"):
        notes.append("include_outgoing_unsupported")
    events, exceptions, scope_notes = scope_to_rules(row.get("scope") or {})
    notes.extend(scope_notes)

    created = False
    if into is not None:
        channel = await NotificationChannelRepository.get(into)
        if channel is None:
            raise ConversionError("channel_missing", 404)
        pending = config.get(CONVERTING_TO)
        if isinstance(pending, str) and pending and pending != channel.id:
            await _drop_pending_channel(pending)
    else:
        body = _channel_body(row, notes)
        pending = config.get(CONVERTING_TO)
        channel = (
            await NotificationChannelRepository.get(pending)
            if isinstance(pending, str) and pending
            else None
        )
        if channel is None:
            if isinstance(pending, str) and pending:
                channel_id = pending
            else:
                channel_id = f"{get_type(body.type, body.preset).id}-{uuid.uuid4().hex[:8]}"
                # Write-ahead: mark the row before the channel exists (CONVERTING_TO).
                marked = {**config, CONVERTING_TO: channel_id}
                if await FanoutConfigRepository.update(row["id"], config=marked) is None:
                    raise ConversionError("fanout_missing", 404)
                row = {**row, "config": marked}
            channel = await NotificationChannelRepository.create(
                body.model_copy(update={"id": channel_id})
            )
            created = True
    await _add_rules(channel.id, events, exceptions)
    fanout = await _disable_row(row, channel.id)
    logger.info("Fanout Apprise row %s converted to channel %s", config_id, channel.id)
    return Conversion(channel, fanout, notes=list(dict.fromkeys(notes)), created=created)


# ── Duplicates ───────────────────────────────────────────────────────────


def _fingerprint(url: str) -> bytes | None:
    """A keyed digest of a URL's destination: everything after the scheme (so ``gotify`` and
    ``gotifys``, ``json`` and ``jsons`` match), the trailing slash, the fragment and Discord's
    ``avatar`` left out, the query sorted. Never stored, never logged."""
    line = url.strip()
    if "://" not in line:
        return None
    parts = urlsplit(line)
    query = sorted(
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "avatar"
    )
    canonical = f"{unquote(parts.netloc)}{unquote(parts.path).rstrip('/')}?{urlencode(query)}"
    return hmac.new(_FINGERPRINT_KEY, canonical.encode(), sha256).digest()


def _fingerprints(urls: list[str]) -> list[bytes]:
    return [fp for fp in map(_fingerprint, urls) if fp is not None]


def channel_urls(channel: Channel, secret_values: dict[str, str]) -> list[str]:
    """The destination URLs of a channel, for the comparison only (never returned)."""
    from app.notifications.presets import PresetError, compile_url, preset_for

    if channel.type == "apprise" and channel.preset:
        preset = preset_for(channel.preset)
        if preset is None:
            return []
        try:
            return [compile_url(preset, {**channel.config, **secret_values})]
        except PresetError:
            return []
    if channel.type == "apprise":
        return split_apprise_urls(secret_values.get("urls", ""))
    if channel.type == "discord" and secret_values.get("webhook_url"):
        return [secret_values["webhook_url"]]
    return []


async def fanout_duplicates(channel_id: str) -> list[dict[str, str]]:
    """The active fanout Apprise rows that send where ``channel_id`` sends: ``{id, name}``."""
    from app.fanout.manager import is_reserved_fanout_id
    from app.repository.fanout import FanoutConfigRepository
    from app.repository.notification_channels import NotificationChannelRepository

    found = await NotificationChannelRepository.get_for_send(channel_id)
    if found is None:
        return []
    mine = _fingerprints(channel_urls(*found))
    if not mine:
        return []
    matches: list[dict[str, str]] = []
    for row in await FanoutConfigRepository.get_all():
        config = row.get("config") or {}
        if (
            row.get("type") != "apprise"
            or not row.get("enabled")
            or config.get(CONVERTED_TO)
            or is_reserved_fanout_id(str(row["id"]))
        ):
            continue
        theirs = _fingerprints(_row_urls(config))
        if any(hmac.compare_digest(a, b) for a in mine for b in theirs):
            matches.append({"id": str(row["id"]), "name": str(row.get("name") or "")})
    return matches
