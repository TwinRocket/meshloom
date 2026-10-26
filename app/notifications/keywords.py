"""Keyword rules: the matcher and the validation behind ``message.keyword`` (MSHL-128).

A rule is ``{id, pattern, match: contains|word, scope: all|dms|[conversation keys], channels,
cooldown_s}`` stored in ``app_settings.notification_rules["keywords"]``. The text comes from
the mesh and the pattern from the admin, and **no regex engine ever sees either**: both are
NFC-normalised and casefolded, then compared with ``str.find``, so a match costs one linear
scan per rule (at most ``MAX_KEYWORDS`` rules of ``MAX_PATTERN`` characters) and cannot
backtrack (ReDoS).

``word`` asks for a non-word character (or the edge of the text) on both sides of the match,
as the mention matcher does (``mention.py``). A side is only checked when the pattern itself
starts (or ends) with a word character: a pattern that starts or ends with a symbol or an emoji
(``#paris``, ``🆘``) has no word boundary on that side, so ``help🆘now`` matches ``🆘``.

Channel messages are not authenticated: anyone in range can type a keyword. Nothing here makes
a match urgent, and the cooldowns (per rule, and per rule and sender) bound what one sender
can trigger.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from collections.abc import Collection, Mapping
from typing import Any

MAX_KEYWORDS = 20
MAX_PATTERN = 64
MAX_SCOPE_KEYS = 200
MAX_RULE_CHANNELS = 50
DEFAULT_COOLDOWN_S = 300
MAX_COOLDOWN_S = 86_400
# A sender cannot trigger one rule again sooner than this (or the rule's own cooldown when that
# is longer): channel messages are forgeable, and the rule's cooldown alone would let one
# sender use the whole window.
SENDER_COOLDOWN_S = 900

MATCH_MODES = ("contains", "word")
SCOPE_WORDS = ("all", "dms")

_ID = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_CONVERSATION_KEY = re.compile(r"^(contact|channel)-[0-9A-Za-z]{1,128}$")


class KeywordError(ValueError):
    """A keyword rule that is refused; ``args[0]`` is the offending field (``pattern``...)."""


def normalise(text: str) -> str:
    """NFC, casefolded, NFC again (casefolding can leave a decomposed form)."""
    return unicodedata.normalize("NFC", unicodedata.normalize("NFC", text).casefold())


def _is_word(char: str) -> bool:
    return char == "_" or char.isalnum()


def text_matches(text: str, pattern: str, mode: str) -> bool:
    """Whether the already normalised ``text`` holds the already normalised ``pattern``."""
    if not pattern:
        return False
    if mode != "word":
        return pattern in text
    start = text.find(pattern)
    while start != -1:
        end = start + len(pattern)
        left = not _is_word(pattern[0]) or start == 0 or not _is_word(text[start - 1])
        right = not _is_word(pattern[-1]) or end == len(text) or not _is_word(text[end])
        if left and right:
            return True
        start = text.find(pattern, start + 1)
    return False


def message_body(payload: Mapping[str, Any]) -> str:
    """The text a rule is matched against: the message without the ``"Name: "`` prefix a
    channel message is stored with (the sender's name must not trigger a rule)."""
    text = str(payload.get("text") or "")
    name = str(payload.get("sender_name") or "")
    if payload.get("type") == "CHAN" and name and text.startswith(f"{name}: "):
        return text[len(name) + 2 :]
    return text


def state_key(payload: Mapping[str, Any]) -> str:
    kind = "contact" if payload.get("type") == "PRIV" else "channel"
    return f"{kind}-{payload.get('conversation_key') or ''}"


def scope_allows(scope: object, payload: Mapping[str, Any]) -> bool:
    if scope == "all":
        return True
    if scope == "dms":
        return payload.get("type") == "PRIV"
    if isinstance(scope, list):
        key = state_key(payload).lower()
        return any(isinstance(k, str) and k.lower() == key for k in scope)
    return False


def matching_rules(rules: Mapping[str, Any], payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The stored keyword rules a message triggers (scope and text; not the cooldowns, not the
    channels). Never raises: an entry that is not a rule is skipped."""
    stored = rules.get("keywords")
    if not isinstance(stored, list) or not stored:
        return []
    body = normalise(message_body(payload))
    if not body:
        return []
    hits: list[Mapping[str, Any]] = []
    for rule in stored[:MAX_KEYWORDS]:
        if not isinstance(rule, Mapping):
            continue
        pattern = rule.get("pattern")
        if not isinstance(pattern, str) or not scope_allows(rule.get("scope"), payload):
            continue
        if text_matches(body, normalise(pattern), str(rule.get("match") or "contains")):
            hits.append(rule)
    return hits


def sender_id(payload: Mapping[str, Any]) -> str:
    """Who a cooldown counts as the sender: its key when the message has one, else its name
    (a channel message often has no key), else the conversation (a direct message)."""
    return str(
        payload.get("sender_key")
        or payload.get("sender_name")
        or payload.get("conversation_key")
        or ""
    ).lower()


def sender_cooldown_s(rule: Mapping[str, Any]) -> int:
    cooldown = rule.get("cooldown_s")
    return max(SENDER_COOLDOWN_S, cooldown if isinstance(cooldown, int) else 0)


# --- validation --------------------------------------------------------------------


def clean_pattern(value: object) -> str:
    """The pattern as stored: NFC, trimmed, 1 to ``MAX_PATTERN`` characters, no control
    character."""
    if not isinstance(value, str):
        raise KeywordError("pattern")
    pattern = unicodedata.normalize("NFC", value).strip()
    if not pattern or len(pattern) > MAX_PATTERN:
        raise KeywordError("pattern")
    if any(unicodedata.category(c).startswith("C") for c in pattern):
        raise KeywordError("pattern")
    return pattern


def clean_scope(value: object) -> str | list[str]:
    if isinstance(value, str) and value in SCOPE_WORDS:
        return value
    if (
        isinstance(value, list)
        and 0 < len(value) <= MAX_SCOPE_KEYS
        and all(isinstance(k, str) and _CONVERSATION_KEY.match(k) for k in value)
    ):
        return list(dict.fromkeys(value))
    raise KeywordError("scope")


def clean_rule(raw: object, *, known_channels: Collection[str] | None = None) -> dict[str, Any]:
    """One rule as stored, or ``KeywordError`` naming the field. A missing ``id`` is generated.

    ``known_channels`` (the API; the backup restore checks channels itself) refuses an unknown
    channel id. Web Push (``builtin:push``) is always refused: it is decided per account
    (``user_rules.py``) and a keyword rule is an instance setting."""
    from app.notifications.channels import BUILTIN_PUSH_ID

    if not isinstance(raw, Mapping):
        raise KeywordError("keywords")
    rule_id = raw.get("id") or f"kw_{secrets.token_hex(4)}"
    if not isinstance(rule_id, str) or not _ID.match(rule_id):
        raise KeywordError("id")
    match = raw.get("match", "contains")
    if match not in MATCH_MODES:
        raise KeywordError("match")
    channels = raw.get("channels", [])
    if (
        not isinstance(channels, list)
        or len(channels) > MAX_RULE_CHANNELS
        or not all(isinstance(c, str) and c and c != BUILTIN_PUSH_ID for c in channels)
    ):
        raise KeywordError("channels")
    channels = list(dict.fromkeys(channels))
    if known_channels is not None and any(c not in known_channels for c in channels):
        raise KeywordError("channels")
    cooldown = raw.get("cooldown_s", DEFAULT_COOLDOWN_S)
    if (
        not isinstance(cooldown, int)
        or isinstance(cooldown, bool)
        or not 0 <= cooldown <= MAX_COOLDOWN_S
    ):
        raise KeywordError("cooldown_s")
    return {
        "id": rule_id,
        "pattern": clean_pattern(raw.get("pattern")),
        "match": match,
        "scope": clean_scope(raw.get("scope", "all")),
        "channels": channels,
        "cooldown_s": cooldown,
    }


def clean_rules(
    raw: object, *, known_channels: Collection[str] | None = None
) -> list[dict[str, Any]]:
    """The whole list: at most ``MAX_KEYWORDS`` rules with unique ids."""
    if not isinstance(raw, list) or len(raw) > MAX_KEYWORDS:
        raise KeywordError("keywords")
    rules = [clean_rule(r, known_channels=known_channels) for r in raw]
    if len({r["id"] for r in rules}) != len(rules):
        raise KeywordError("id")
    return rules
