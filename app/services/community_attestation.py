"""Community attestations of the instance keys (MSHL-445).

Links (``meshl.ink``) and the Remote relay (``relay.meshloom.link``) accept a creation
or a registration only from an instance key that holds a Community **attestation**:
a short-lived token Community signs once it heard the radio from another station
(contract ``meshloom-community/docs/contracts/attestation.md``). This module is the
only place that asks for one. The browser never does.

- **Request.** ``POST /v1/me/attestations`` on the Community API through
  ``meshloom_community.stats_request`` (the Community egress guard: nothing leaves while
  Community is off, waiting for a position or leaving), with the Community API JWT of
  the radio key, body ``{"instance_key", "audience"}``. One attestation per service key:
  the Links key (``instance_identity.SHARE_SERVICE``) with the audience ``links``, the
  Remote key (``REMOTE_SERVICE``) with ``remote``.
- **Cache.** The last token of each audience is kept in ``community_attestations``
  (migration 142) with its ``expires_at``, so a restart does not ask again. A token is
  reused while more than ``RENEW_BEFORE_S`` (24 h) are left; under that it is renewed,
  and the old one is still sent while more than ``MIN_REMAINING_S`` are left if the
  renewal fails. A token of another instance key (rotation, restore) is never sent.
- **Rejection.** After a service answers ``attestation_missing``, ``attestation_invalid``
  or ``attestation_expired`` the caller asks ``renew_after_rejection`` once and retries
  once (``share.share_request``, ``remote._register_locked``); a second rejection is
  reported, never looped. A token fetched less than ``FORCED_RENEW_MIN_INTERVAL_S`` ago
  and refused anyway is dropped without asking Community again.
- **Rate limits.** Community allows 12 requests per radio key and per rolling hour. One
  request at a time per audience (a lock), at most ``FETCH_BUDGET`` per audience per
  ``FETCH_WINDOW_S`` (6 each, 12 together), and a hold after a refusal: ``radio_not_heard``
  15 min, ``opted_out`` and ``banned`` 1 h, a 429 its ``Retry-After`` or 15 min, a
  ``503 attestation_unconfigured`` 15 min, other failures (network, 5xx, malformed
  answer) an exponential backoff from 1 to 15 min. A hold answers at once, without a
  request. Holds and budget live in memory (a restart starts them again).
- **Reasons** (``AttestationUnavailable.reason``), which Links and Remote turn into their
  own coded errors: ``radio_not_heard``, ``opted_out``, ``banned`` and ``unavailable``
  (everything else: try later).
- **Community off.** Turning Community off (or a restore) forgets every cached token and
  hold: a later join asks again.

Never log a token. Tests: ``tests/test_community_attestation.py``.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from fastapi import HTTPException

from app.repository.community_attestations import (
    CommunityAttestationRepository,
    StoredAttestation,
)
from app.services.backup_sections import RestoreSource, register_on_restore

logger = logging.getLogger(__name__)

AUDIENCE_LINKS: Literal["links"] = "links"
AUDIENCE_REMOTE: Literal["remote"] = "remote"
Audience = Literal["links", "remote"]

ATTESTATIONS_PATH = "/v1/me/attestations"
ATTESTATION_HEADER = "X-Meshloom-Attestation"
# Service answers that call for a new attestation (attestation.md, § Verification).
REJECTION_CODES = frozenset({"attestation_missing", "attestation_invalid", "attestation_expired"})

RENEW_BEFORE_S = 24 * 3600
MIN_REMAINING_S = 300
FORCED_RENEW_MIN_INTERVAL_S = 600
FETCH_BUDGET = 6
FETCH_WINDOW_S = 3600
HOLD_S: dict[str, int] = {"radio_not_heard": 900, "opted_out": 3600, "banned": 3600}
RATE_LIMIT_HOLD_S = 900
UNCONFIGURED_HOLD_S = 900
BACKOFF_MIN_S = 60
BACKOFF_MAX_S = 900
TOKEN_MAX_LEN = 2048
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[0-9a-fA-F]{128}")
_CODE_RE = re.compile(r"[a-z_]{1,32}")

Reason = Literal["radio_not_heard", "opted_out", "banned", "unavailable"]


class AttestationUnavailable(Exception):
    """No usable attestation now. ``reason`` is what the user is told."""

    def __init__(self, reason: Reason, retry_after: int | None = None) -> None:
        super().__init__(reason)
        self.reason: Reason = reason
        self.retry_after = retry_after


@dataclass
class _Hold:
    reason: Reason
    until: float


@dataclass
class _State:
    clock: Callable[[], float] = time.time
    locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    holds: dict[str, _Hold] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)
    fetches: dict[str, deque[float]] = field(default_factory=dict)


_state = _State()


def reset_attestations_for_tests(clock: Callable[[], float] | None = None) -> None:
    global _state
    _state = _State(clock=clock or time.time)


def _lock(audience: str) -> asyncio.Lock:
    return _state.locks.setdefault(audience, asyncio.Lock())


def _service_of(audience: Audience) -> str:
    from app.services.instance_identity import REMOTE_SERVICE, SHARE_SERVICE

    return SHARE_SERVICE if audience == AUDIENCE_LINKS else REMOTE_SERVICE


async def _instance_key_hex(audience: Audience) -> str:
    from app.services.instance_identity import ensure_instance_key

    key = await ensure_instance_key(_service_of(audience))
    return bytes(key.verify_key).hex().upper()


def _usable(stored: StoredAttestation | None, key: str, now: float) -> StoredAttestation | None:
    if stored is None or stored.instance_key != key:
        return None
    return stored if stored.expires_at - now > MIN_REMAINING_S else None


async def attestation_for(audience: Audience) -> str:
    """A token to send for ``audience``: the cached one, or a new one from Community.

    Raises ``AttestationUnavailable`` when there is none (Community refused, a hold,
    the local budget, Community unreachable).
    """
    key = await _instance_key_hex(audience)
    async with _lock(audience):
        now = _state.clock()
        usable = _usable(await CommunityAttestationRepository.get(audience), key, now)
        if usable is not None and usable.expires_at - now > RENEW_BEFORE_S:
            return usable.token
        try:
            return (await _fetch(audience, key)).token
        except AttestationUnavailable:
            if usable is not None:
                return usable.token
            raise


async def renew_after_rejection(audience: Audience, rejected: str | None) -> str:
    """A new token after a service refused ``rejected`` (one renewal, never a loop).

    Another request may have renewed it meanwhile: that token is returned. A token
    fetched less than ``FORCED_RENEW_MIN_INTERVAL_S`` ago and refused anyway is
    dropped without asking again (``unavailable``).
    """
    key = await _instance_key_hex(audience)
    async with _lock(audience):
        now = _state.clock()
        stored = await CommunityAttestationRepository.get(audience)
        usable = _usable(stored, key, now)
        if usable is not None and usable.token != rejected:
            return usable.token
        await CommunityAttestationRepository.delete(audience)
        if (
            stored is not None
            and stored.token == rejected
            and now - stored.fetched_at < FORCED_RENEW_MIN_INTERVAL_S
        ):
            logger.warning("A fresh %s attestation was refused: not asking again yet", audience)
            raise _hold(audience, "unavailable", FORCED_RENEW_MIN_INTERVAL_S)
        return (await _fetch(audience, key)).token


def _hold(audience: str, reason: Reason, seconds: float) -> AttestationUnavailable:
    _state.holds[audience] = _Hold(reason, _state.clock() + seconds)
    return AttestationUnavailable(reason, retry_after=max(1, round(seconds)))


def _backoff(audience: str) -> AttestationUnavailable:
    failures = _state.failures.get(audience, 0) + 1
    _state.failures[audience] = failures
    seconds = min(BACKOFF_MAX_S, BACKOFF_MIN_S * 2 ** min(failures - 1, 10))
    return _hold(audience, "unavailable", seconds)


def _take_budget(audience: str, now: float) -> None:
    window = _state.fetches.setdefault(audience, deque())
    while window and now - window[0] >= FETCH_WINDOW_S:
        window.popleft()
    if len(window) >= FETCH_BUDGET:
        wait = FETCH_WINDOW_S - (now - window[0])
        logger.warning("Community attestation budget for %s spent for this hour", audience)
        raise AttestationUnavailable("unavailable", retry_after=max(1, round(wait)))
    window.append(now)


async def _fetch(audience: Audience, key: str) -> StoredAttestation:
    """One request to Community, under the audience lock; holds and budget first."""
    now = _state.clock()
    hold = _state.holds.get(audience)
    if hold is not None:
        if now < hold.until:
            raise AttestationUnavailable(hold.reason, retry_after=max(1, round(hold.until - now)))
        del _state.holds[audience]
    _take_budget(audience, now)
    try:
        response = await _request_attestation(key, audience)
    except HTTPException as exc:
        raise _local_refusal(audience, exc) from None
    attestation = _parse(response, audience, key, _state.clock())
    if attestation is None:
        raise _refusal(audience, response)
    _state.failures.pop(audience, None)
    await CommunityAttestationRepository.put(attestation)
    return attestation


async def _request_attestation(instance_key: str, audience: Audience) -> httpx.Response:
    """``POST /v1/me/attestations`` (the only network call of this module)."""
    from app.services.meshloom_community import stats_request

    return await stats_request(
        "POST",
        ATTESTATIONS_PATH,
        auth=True,
        json_body={"instance_key": instance_key, "audience": audience},
    )


def _payload(response: httpx.Response) -> dict[str, Any]:
    try:
        value = response.json()
    except (ValueError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _token_claims(token: str) -> dict[str, Any]:
    """The payload of a token (not verified: the services check the signature)."""
    try:
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
    except (IndexError, ValueError, binascii.Error, UnicodeDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _parse(
    response: httpx.Response, audience: str, key: str, now: float
) -> StoredAttestation | None:
    """The ``201`` answer when it keeps the contract, else None."""
    if response.status_code != 201:
        return None
    body = _payload(response)
    token, issued_at, expires_at = (
        body.get("attestation"),
        body.get("issued_at"),
        body.get("expires_at"),
    )
    if (
        not isinstance(token, str)
        or len(token) > TOKEN_MAX_LEN
        or not _TOKEN_RE.fullmatch(token)
        or body.get("audience") != audience
        or not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or expires_at <= issued_at
        or expires_at - now <= MIN_REMAINING_S
        or _token_claims(token).get("sub") != key
        or _token_claims(token).get("aud") != audience
    ):
        if response.status_code == 201:
            logger.warning("Community answered an attestation outside the contract")
        return None
    return StoredAttestation(
        audience=audience,
        instance_key=key,
        token=token,
        issued_at=issued_at,
        expires_at=expires_at,
        fetched_at=int(now),
    )


def _refusal(audience: str, response: httpx.Response) -> AttestationUnavailable:
    """A Community answer that is not a usable ``201``."""
    status = response.status_code
    code = _payload(response).get("code")
    if status == 403 and isinstance(code, str) and code in HOLD_S:
        logger.info("Community refused a %s attestation: %s", audience, code)
        reason: Reason = (
            "radio_not_heard"
            if code == "radio_not_heard"
            else ("opted_out" if code == "opted_out" else "banned")
        )
        return _hold(audience, reason, HOLD_S[code])
    if status == 429:
        retry = response.headers.get("retry-after", "")
        seconds = int(retry) if retry.isdigit() and int(retry) > 0 else RATE_LIMIT_HOLD_S
        logger.warning("Community attestation rate limit reached (%s)", audience)
        return _hold(audience, "unavailable", min(seconds, FETCH_WINDOW_S))
    if status == 503 and code == "attestation_unconfigured":
        logger.warning("Community does not issue attestations yet (%s)", audience)
        return _hold(audience, "unavailable", UNCONFIGURED_HOLD_S)
    if status != 201:
        shown = code if isinstance(code, str) and _CODE_RE.fullmatch(code) else "-"
        logger.warning(
            "Community answered %s %s to a %s attestation request", status, shown, audience
        )
    return _backoff(audience)


def _local_refusal(audience: str, exc: HTTPException) -> AttestationUnavailable:
    """``stats_request`` refused before or around the request (off, banned, breaker)."""
    from app.services.community_ban import COMMUNITY_BANNED_ERROR

    detail = exc.detail
    if isinstance(detail, dict) and detail.get("code") == COMMUNITY_BANNED_ERROR:
        return _hold(audience, "banned", HOLD_S["banned"])
    if exc.status_code == 403:
        # Community off, waiting for a position or leaving: nothing left. No hold, so
        # the first call once Community is back asks at once.
        return AttestationUnavailable("unavailable")
    return _backoff(audience)


async def forget_attestations() -> None:
    """Forget every cached token and hold (Community off, a restore)."""
    _state.holds.clear()
    _state.failures.clear()
    await CommunityAttestationRepository.delete()


async def _on_community_change() -> None:
    from app.services.meshloom_community import community_enabled

    if not await community_enabled():
        await forget_attestations()


async def _forget_after_restore(source: RestoreSource) -> None:
    await forget_attestations()


def _listen() -> None:
    from app.services.meshloom_community import add_enabled_change_listener

    add_enabled_change_listener(_on_community_change)


_listen()
register_on_restore("community_attestation", _forget_after_restore)
