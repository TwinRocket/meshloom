"""Client of the share service (Links & Files): hosts, opt-in, errors, read protections.

The share service is a Cloudflare Worker on its own domain (contract:
``meshloom-community/docs/contracts/links-files.md``). It is a distinct opt-in
from Community, with its own hosts and its own switch, never an exception to
the Community guard.

- **Hosts.** ``SHARE_HOST`` is the write host, ``SHARE_HOSTS`` every host a
  link may carry (the current one and the former ones). Module constants read
  at call time (tests monkeypatch them), never an environment variable.
- **Opt-in.** ``share_egress_state()`` is None unless ``app_settings.share_enabled`` and the
  recorded ``share_terms_version`` is ``SHARE_TERMS_VERSION`` (re-consent when the terms change);
  ``share_request()`` checks it before anything else and answers 403
  ``share_disabled`` without any connection.
- **Egress.** Every request goes through the egress guard
  (``app/net/egress_guard.py``, ``follow_checked_redirects``): an allowlist of
  exactly ``SHARE_HOST``, HTTPS only, no redirect followed, the answer capped,
  the connection pinned to a validated public address. Timeouts: connect 5 s,
  read 30 s, upload 60 s, plus a bound on the whole call.
- **Auth.** A write carries ``Authorization: Bearer`` minted for that request by
  ``mint_share_jwt(binding)``; a read carries none. ``User-Agent`` is
  ``Meshloom/<version>``.
- **Errors** are coded ``HTTPException`` details ``{code, message, params}``
  (``SHARE_ERRORS``). A 429 ``quota_exceeded`` with a contract ``scope`` (``key``,
  ``ip``, ``global``) becomes ``share_quota_key`` / ``_ip`` / ``_global`` (generic
  ``share_quota_exceeded`` without or with an unknown scope); never a remaining
  count. Any answer that is not a contract JSON body (a Cloudflare
  1027 page, a challenge, an HTML 404) is ``share_unavailable``.
- **Banned.** A 403 ``banned`` holds a banned state for ``BANNED_HOLD_S`` (in
  memory): every later tokened request is refused locally (403 ``share_banned``)
  with no request, except ``list_mine`` (``banned_ok``: the contract lets a
  banned key read ``GET /v1/mine``). Reads stay open (the service keeps them open). A restart or any
  change of the share settings ends it (``note_share_settings_changed``).
- **Read protections.** Two ``aiolimiter`` buckets (``meta`` 60 and ``bytes`` 20
  per minute) that refuse rather than wait (429 ``share_rate_limited``,
  ``params.scope = "local"``); a negative cache of the service's JSON 404 and
  410 answers for ``NEGATIVE_CACHE_S`` (and ``get_meta`` replays a good answer
  for ``META_CACHE_S``); and a circuit breaker
  (``app/net/breaker.py``): ``BREAKER_FAILURE_THRESHOLD`` consecutive failures
  (network, timeout, guard refusal, 5xx, non-JSON) stop every call for
  ``BREAKER_OPEN_SECONDS`` and answer ``share_unavailable`` locally. A JSON
  503 ``kill_switch`` is not a failure (the service answered).
- **Objects.** ``read_object`` serves ``GET /{id}`` from the disk cache
  (``app/services/share_cache.py``) or fetches it once: the bytes are sniffed
  (PNG, JPEG, GIF, WebP magic), the dimensions re-read with ``imagesize`` (at
  most ``MAX_IMAGE_PIXELS``) and only a checked image is served as an image;
  any other object is raw bytes (``application/octet-stream``). An answer
  over ``MAX_OBJECT_RESPONSE_BYTES`` is ``share_too_large``; a refused object
  is negatively cached like a 404.
- **Own objects.** ``list_mine`` reads ``GET /v1/mine`` (binding = ``mine``;
  entries outside the contract are dropped, a blocked category outside the
  contract list becomes ``other``). ``delete_object`` sends
  ``DELETE /v1/objects/{id}`` (binding = the id), then drops the disk-cache
  entry and the cached meta and records the id as gone in the negative cache,
  so the next read answers ``share_gone`` without a request. The in-memory
  state is updated before the disk delete, and the id is tombstoned for as
  long as a disk entry can live (``_state.deleted``, checked by
  ``read_object`` before and after the disk cache and by ``get_meta`` before
  and after its fetch), so a failed disk delete or a read racing the delete
  never serves the object (or its meta) again. The disk cache also keeps a
  tombstone of its own (``ShareCache.delete``), so an entry whose disk delete
  failed is not served after a restart either. A 410 (the operator
  blocked it) is ``share_object_blocked`` and a 403 ``not_owner``
  ``share_not_owner``; the local copies go on a 404 or 410 too.

Never log a token, a request body or a response body. Tests:
``tests/test_share_service.py``, ``tests/test_share_off_no_egress.py``.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
import imagesize
from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from app.net.breaker import CircuitBreaker
from app.net.egress_guard import (
    CheckedResponse,
    EgressBlocked,
    HostAllowlist,
    StreamedBody,
    error_code,
    follow_checked_redirects,
)
from app.repository import AppSettingsRepository
from app.services.instance_identity import mint_share_jwt
from app.services.share_cache import CachedObject, reset_share_cache_for_tests, share_cache
from app.services.ttl_lru import TtlLruCache
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

# Write host, and every host a link may carry (current first, then former ones).
# Version of the share terms the consent dialog shows. Raise it when the terms change in a
# way that needs a new acceptance: sharing then stays off until the owner accepts again
# (MSHL-292). ``app_settings.share_terms_version`` records the accepted one.
SHARE_TERMS_VERSION = 1
SHARE_HOST = "meshl.ink"
SHARE_HOSTS: tuple[str, ...] = ("meshl.ink",)

# The id grammar of the contract: 7 characters of the Bitcoin base58 alphabet.
# Always used with fullmatch: "$" alone would accept a trailing newline.
SHARE_ID_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{7}")

SHARE_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=60.0, pool=5.0)
# Bound on one whole call, so a server dripping bytes cannot hold it open.
SHARE_TOTAL_TIMEOUT_S = 100.0
# Response caps: JSON answers are small. Contract links-files.md ("Files API v1"
# and the encrypted envelope): a stored image is at most 10 MiB (10 485 760
# bytes), an encrypted envelope at most 10 489 891 bytes (35-byte header, 4096
# bytes of padding room, 10 MiB of data); a bytes answer is capped at the latter.
MAX_JSON_RESPONSE_BYTES = 64 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ENVELOPE_BYTES = 35 + 4096 + MAX_IMAGE_BYTES
MAX_OBJECT_RESPONSE_BYTES = MAX_ENVELOPE_BYTES
# Contract limits of a stored image: GIF at most 5 MiB, at most 40 megapixels.
MAX_GIF_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
OCTET_STREAM = "application/octet-stream"
IMAGE_CONTENT_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
# What GET /{id} may answer as bytes: the image types the contract serves,
# and raw bytes (an encrypted object). Never text/html or anything else.
OBJECT_CONTENT_TYPES = IMAGE_CONTENT_TYPES | {OCTET_STREAM}
# File name extension of a served object (Content-Disposition).
OBJECT_EXTENSIONS: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    OCTET_STREAM: "bin",
}

BREAKER_FAILURE_THRESHOLD = 3
BREAKER_OPEN_SECONDS = 300.0
BANNED_HOLD_S = 12 * 3600.0
NEGATIVE_CACHE_S = 600.0
NEGATIVE_CACHE_SIZE = 1024
# A successful meta answer is replayed for as long (a link does not change).
META_CACHE_S = 600.0
META_CACHE_SIZE = 256
# Reads per minute and per instance, refused (never queued) past the bucket.
READ_LIMITS: dict[str, int] = {"meta": 60, "bytes": 20}
READ_LIMIT_PERIOD_S = 60.0

ReadKind = Literal["meta", "bytes"]

# Local code -> (HTTP status, message). The UI translates errors.<code>.
SHARE_ERRORS: dict[str, tuple[int, str]] = {
    "share_disabled": (403, "Sharing is turned off on this instance"),
    "share_unavailable": (502, "The share service is unavailable"),
    "share_quota_exceeded": (429, "The share quota of this instance is reached"),
    "share_quota_key": (429, "The share quota of this instance key is reached"),
    "share_quota_ip": (429, "The share quota of this network address is reached"),
    "share_quota_global": (429, "The share service reached its daily budget"),
    "share_rate_limited": (429, "Too many share requests, try again later"),
    "share_banned": (403, "This instance is banned from the share service"),
    "share_too_large": (413, "The file is too large"),
    "share_type_refused": (415, "This file type is refused"),
    "share_not_found": (404, "No shared object with this id"),
    "share_gone": (410, "This shared object expired or was removed"),
    "share_target_blocked": (422, "This link target is refused"),
    "share_invalid_url": (400, "This URL cannot be shortened"),
    "share_cross_site": (403, "Sharing requests from another site are refused"),
    "share_invalid_id": (400, "This is not a shared object id"),
    "share_invalid_request": (400, "Unexpected parameter in a share request"),
    "share_mode_unavailable": (422, "This share mode is not available yet"),
    "share_mode_not_allowed": (422, "Encrypted sharing is not possible in this conversation"),
    "share_radio_key_unavailable": (
        503,
        "The radio private key is not available yet, encrypted sharing is impossible",
    ),
    "share_undecryptable": (422, "This file cannot be decrypted here"),
    "share_id_taken": (409, "The proposed share id is already in use"),
    "share_upload_timeout": (408, "The upload took too long to arrive"),
    "share_storage_failed": (500, "The upload could not be stored on this instance"),
    "share_not_owner": (403, "This shared object was not posted by this instance"),
    "share_role_forbidden": (403, "This account cannot read the message this object was shared in"),
    "share_object_blocked": (
        409,
        "This shared object was blocked by the share service and cannot be deleted",
    ),
}

# The contract's optional ``scope`` of a 429 ``quota_exceeded`` -> local code. An
# absent or unknown scope keeps the generic ``share_quota_exceeded``.
_QUOTA_SCOPE_CODES: dict[str, str] = {
    "key": "share_quota_key",
    "ip": "share_quota_ip",
    "global": "share_quota_global",
}

# Service error code (contract) -> local code. Every other code (the token_*
# family, invalid_request, kill_switch, unavailable...) is share_unavailable.
_SERVICE_CODES: dict[str, str] = {
    "banned": "share_banned",
    "quota_exceeded": "share_quota_exceeded",
    "rate_limited": "share_rate_limited",
    "too_large": "share_too_large",
    "type_refused": "share_type_refused",
    "not_found": "share_not_found",
    "gone": "share_gone",
    "target_blocked": "share_target_blocked",
    "invalid_url": "share_invalid_url",
    # Only ``POST /v1/files`` with ``X-Share-Id``: the upload retries with a new id.
    "id_taken": "share_id_taken",
    # Only ``DELETE /v1/objects/{id}``: the key is valid but not the author's.
    "not_owner": "share_not_owner",
}
# Service answers a read may cache as final (the contract never answers them
# for a storage failure).
_NEGATIVE_CODES = frozenset({"share_not_found", "share_gone"})
_SCOPE_RE = re.compile(r"[a-z_]{1,32}")


class ShareError(HTTPException):
    """A coded share error: ``detail = {code, message, params}``."""

    def __init__(self, code: str, **params: Any) -> None:
        status, message = SHARE_ERRORS[code]
        super().__init__(
            status_code=status, detail={"code": code, "message": message, "params": params}
        )
        self.code = code
        self.params = params


@dataclass(frozen=True)
class ShareEgress:
    """What a share request needs once sharing is on."""

    host: str


class ShareLink(BaseModel):
    """``POST /v1/links`` answer."""

    model_config = ConfigDict(extra="ignore")

    id: str
    url: str
    expires_at: int


class ShareMeta(BaseModel):
    """``GET /v1/meta/{id}`` answer (v1: links; the files contract adds members)."""

    model_config = ConfigDict(extra="allow")

    id: str
    kind: str
    target_host: str | None = None
    created_at: int | None = None
    expires_at: int | None = None
    # Files (contract "Files API v1"): a public file has content_type and size;
    # an encrypted one (M3) has size_kib and no content_type.
    mode: str | None = None
    content_type: str | None = None
    size: int | None = None
    size_kib: int | None = None


class ShareMineObject(BaseModel):
    """One object of ``GET /v1/mine`` (a link or a file; clients ignore unknown members)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    kind: Literal["link", "file"]
    status: Literal["active", "blocked"]
    blocked_category: str | None = None
    created_at: int | None = None
    expires_at: int | None = None
    target_host: str | None = None
    mode: Literal["public", "encrypted"] | None = None
    content_type: str | None = None
    size: int | None = None
    size_kib: int | None = None


class ShareMine(BaseModel):
    """``GET /v1/mine`` answer: one page, newest first."""

    model_config = ConfigDict(extra="ignore")

    objects: list[dict[str, Any]]
    next_cursor: str | None = None


@dataclass(frozen=True)
class ShareMinePage:
    objects: list[ShareMineObject]
    next_cursor: str | None


@dataclass(frozen=True)
class ShareObject:
    """The bytes of ``GET /{id}``."""

    content: bytes
    content_type: str


class _State:
    def __init__(self, clock: Callable[[], float]) -> None:
        self.clock = clock
        self.breaker = CircuitBreaker(
            threshold=BREAKER_FAILURE_THRESHOLD, open_seconds=BREAKER_OPEN_SECONDS, clock=clock
        )
        self.limiters = {
            kind: AsyncLimiter(rate, READ_LIMIT_PERIOD_S) for kind, rate in READ_LIMITS.items()
        }
        self.negative: TtlLruCache[str, tuple[str, dict[str, Any]]] = TtlLruCache(
            NEGATIVE_CACHE_SIZE
        )
        self.meta: TtlLruCache[str, ShareMeta] = TtlLruCache(META_CACHE_SIZE)
        # Ids deleted from this instance, kept as long as a disk-cache entry can
        # live (``share_cache.TTL_S``): ``read_object`` checks it around the disk
        # cache and ``get_meta`` around its fetch, so a failed or racing disk
        # delete never serves a deleted object or re-caches its meta.
        self.deleted: TtlLruCache[str, bool] = TtlLruCache(NEGATIVE_CACHE_SIZE)
        self.banned_until: float | None = None
        # Bumped when sharing is turned off: a request that passed the opt-in
        # check before must not leave, and its answer must not be used.
        self.generation = 0
        self.inflight: set[asyncio.Future[CheckedResponse]] = set()


_state = _State(time.monotonic)


def share_breaker() -> CircuitBreaker:
    return _state.breaker


def reset_share_client_for_tests(clock: Callable[[], float] | None = None) -> None:
    global _state
    _state = _State(clock or time.monotonic)
    reset_share_cache_for_tests()


def is_share_host(host: str) -> bool:
    """True for a host of ``SHARE_HOSTS`` (compared without case)."""
    return host.lower().rstrip(".") in {h.lower() for h in SHARE_HOSTS}


def share_terms_current(settings: Any) -> bool:
    """The owner accepted the current version of the share terms."""
    return settings.share_terms_version == SHARE_TERMS_VERSION


def share_is_effective(settings: Any) -> bool:
    """Sharing is actually on: consent given and the accepted terms are current.

    The single definition behind ``share_egress_state()``, ``GET /api/share/status``
    (``enabled``) and ``GET /api/settings`` (``share_effective``).
    """
    return bool(settings.share_enabled) and share_terms_current(settings)


async def share_egress_state() -> ShareEgress | None:
    """The share egress config when sharing is on, else None (no connection)."""
    settings = await AppSettingsRepository.get()
    if not share_is_effective(settings):
        return None
    return ShareEgress(host=SHARE_HOST)


# Banned state ---------------------------------------------------------------


def share_banned() -> bool:
    """True while a ``banned`` answer holds (``BANNED_HOLD_S`` since the last one)."""
    until = _state.banned_until
    if until is None:
        return False
    if _state.clock() < until:
        return True
    _state.banned_until = None
    return False


def _note_banned() -> None:
    started = not share_banned()
    _state.banned_until = _state.clock() + BANNED_HOLD_S
    if started:
        logger.warning(
            "The share service banned this instance: writes stop until a restart, "
            "a share settings change, or %.0f h",
            BANNED_HOLD_S / 3600,
        )


def note_share_settings_changed(*, enabled: bool) -> None:
    """A share setting changed (a user action or a restore): end the banned state.

    With sharing now off, every request in flight is cancelled at once (its
    caller gets ``share_disabled``) and none started before leaves afterwards.
    """
    _state.banned_until = None
    if not enabled:
        _state.meta.clear()
        _state.generation += 1
        for task in list(_state.inflight):
            task.cancel()


def _check_generation(generation: int) -> None:
    if generation != _state.generation:
        raise ShareError("share_disabled")


# Requests -------------------------------------------------------------------


def _retry_after(response: CheckedResponse) -> int | None:
    raw = response.headers.get("retry-after", "").strip()
    return int(raw) if raw.isdigit() else None


def _json_object(response: CheckedResponse) -> dict[str, Any] | None:
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json" and not content_type.endswith("+json"):
        return None
    try:
        payload = json.loads(response.content)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _service_error(response: CheckedResponse, payload: dict[str, Any] | None) -> ShareError:
    """The local error for a non-2xx answer (``share_unavailable`` unless coded)."""
    service_code = payload.get("code") if payload is not None else None
    code = _SERVICE_CODES.get(service_code) if isinstance(service_code, str) else None
    if code is None or response.status_code >= 500:
        if isinstance(service_code, str):
            shown = service_code if _SCOPE_RE.fullmatch(service_code) else "unknown"
            logger.warning("Share service answered %s %s", response.status_code, shown)
        else:
            logger.warning("Share service answered %s (not a JSON error)", response.status_code)
        return ShareError("share_unavailable")
    if code in ("share_quota_exceeded", "share_rate_limited"):
        params: dict[str, Any] = {"retry_after": _retry_after(response)}
        if code == "share_quota_exceeded":
            scope = payload.get("scope") if payload is not None else None
            params["scope"] = (
                scope if isinstance(scope, str) and _SCOPE_RE.fullmatch(scope) else None
            )
            code = _QUOTA_SCOPE_CODES.get(params["scope"] or "", code)
        return ShareError(code, **params)
    return ShareError(code)


async def share_request(
    method: str,
    path: str,
    *,
    binding: str | None,
    json_body: dict[str, Any] | None = None,
    body: StreamedBody | None = None,
    content_type: str | None = None,
    extra_headers: dict[str, str] | None = None,
    read: ReadKind | None = None,
    accept: str = "application/json",
    max_response_bytes: int = MAX_JSON_RESPONSE_BYTES,
    banned_ok: bool = False,
) -> CheckedResponse:
    """One request to ``SHARE_HOST``; the 2xx answer, else a ``ShareError``.

    ``binding`` is the contract's binding string of a write (``None`` for a
    read, which carries no token). ``read`` names the read bucket and enables
    the negative cache. ``body`` streams a raw upload (``POST /v1/files``) with
    its ``content_type`` and ``extra_headers`` (the ``X-Share-*`` headers).
    ``banned_ok`` lets a tokened read through the local banned hold
    (``GET /v1/mine``: a banned key may still see why).
    """
    generation = _state.generation
    state = await share_egress_state()
    if state is None:
        raise ShareError("share_disabled")
    _check_generation(generation)
    if binding is not None and not banned_ok and share_banned():
        raise ShareError("share_banned")
    cache_key = f"{method.upper()} {path}"
    if read is not None:
        cached = _state.negative.get(cache_key, _state.clock())
        if cached is not None:
            code, params = cached
            raise ShareError(code, **params)
    breaker = _state.breaker
    if breaker.cooling():
        raise ShareError("share_unavailable")
    limiter = _state.limiters[read] if read is not None else None
    if limiter is not None and not limiter.has_capacity():
        raise ShareError(
            "share_rate_limited",
            retry_after=max(1, round(READ_LIMIT_PERIOD_S / limiter.max_rate)),
            scope="local",
        )
    # Checked last: a half-open trial granted here must reach the network.
    if not breaker.allow():
        raise ShareError("share_unavailable")
    headers = {
        **(extra_headers or {}),
        "Accept": accept,
        "User-Agent": f"Meshloom/{get_app_build_info().version}",
    }
    content: bytes | StreamedBody | None = body
    if body is not None and content_type is not None:
        headers["Content-Type"] = content_type
    try:
        if limiter is not None:
            await limiter.acquire()
        if binding is not None:
            headers["Authorization"] = f"Bearer {await mint_share_jwt(binding)}"
        if json_body is not None:
            content = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        # Read again after the awaits above: an opt-out may have landed meanwhile.
        _check_generation(generation)
        send = asyncio.ensure_future(
            follow_checked_redirects(
                method,
                f"https://{state.host}{path}",
                timeout=SHARE_TIMEOUT,
                total_timeout=SHARE_TOTAL_TIMEOUT_S,
                allowed_hosts=HostAllowlist.of(state.host),
                schemes=frozenset({"https"}),
                max_response_bytes=max_response_bytes,
                content=content,
                headers=headers,
            )
        )
        _state.inflight.add(send)
        try:
            response = await send
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if send.cancelled() and (current is None or current.cancelling() == 0):
                # Cancelled by the opt-out, not by our caller.
                raise ShareError("share_disabled") from None
            raise
        finally:
            _state.inflight.discard(send)
        # Never use an answer from before an opt-out.
        _check_generation(generation)
    except ShareError:
        breaker.release_trial()
        raise
    except EgressBlocked as exc:
        if read == "bytes" and exc.code == "egress_response_too_large":
            # The service answered, with more than a file may hold: not an outage.
            breaker.record_success()
            _note_refused_object(cache_key, "share_too_large")
            raise ShareError("share_too_large") from None
        breaker.record_failure()
        logger.warning("Share service %s failed: %s", method.upper(), error_code(exc))
        raise ShareError("share_unavailable") from None
    except (httpx.HTTPError, TimeoutError, OSError) as exc:
        breaker.record_failure()
        logger.warning("Share service %s failed: %s", method.upper(), error_code(exc))
        raise ShareError("share_unavailable") from None
    except BaseException:
        # Cancellation or a local bug: give the half-open trial back unjudged.
        breaker.release_trial()
        raise
    if 200 <= response.status_code < 300:
        breaker.record_success()
        return response
    payload = _json_object(response)
    # An edge page (1027, a challenge) or a 5xx says the service is not there;
    # a redirect or a contract error says it is. A received status is
    # authoritative: a JSON 503 ``kill_switch`` is an operator's answer from a
    # service that is there (an upload it refused before reading the body
    # included: httpcore reads the answer after a write error), not an outage.
    kill_switch = payload is not None and payload.get("code") == "kill_switch"
    if not kill_switch and (
        response.status_code >= 500 or (payload is None and response.status_code >= 400)
    ):
        breaker.record_failure()
    else:
        breaker.record_success()
    error = _service_error(response, payload)
    if error.code == "share_banned":
        _note_banned()
    if read is not None and error.code in _NEGATIVE_CODES:
        now = _state.clock()
        _state.negative.set(cache_key, (error.code, error.params), now + NEGATIVE_CACHE_S, now)
    raise error


def _note_refused_object(cache_key: str, code: str) -> None:
    now = _state.clock()
    _state.negative.set(cache_key, (code, {}), now + NEGATIVE_CACHE_S, now)


def is_share_id(share_id: str) -> bool:
    """True for an id of the contract grammar."""
    return SHARE_ID_RE.fullmatch(share_id) is not None


def _checked_id(share_id: str) -> str:
    """A grammar id, or a local 404 (the service would answer ``not_found``)."""
    if not is_share_id(share_id):
        raise ShareError("share_not_found")
    return share_id


def _parse(model: type[BaseModel], response: CheckedResponse) -> Any:
    payload = _json_object(response)
    try:
        if payload is None:
            raise ValueError("not a JSON object")
        return model.model_validate(payload)
    except ValueError:  # ValidationError included
        logger.warning("Share service answered an unexpected body")
        raise ShareError("share_unavailable") from None


async def create_link(url: str) -> ShareLink:
    """``POST /v1/links``: a short link to ``url`` (binding = the url)."""
    response = await share_request("POST", "/v1/links", binding=url, json_body={"url": url})
    link: ShareLink = _parse(ShareLink, response)
    host = httpx.URL(link.url).host
    if not is_share_id(link.id) or not is_share_host(host):
        logger.warning("Share service answered a link outside the grammar")
        raise ShareError("share_unavailable")
    return link


async def get_meta(share_id: str) -> ShareMeta:
    """``GET /v1/meta/{id}``: what a link or file is, without following it.

    An id deleted here (``_state.deleted``) is ``share_gone`` before the cache
    and again after the fetch, so a request in flight during the delete never
    re-caches the meta (name, type) of the deleted object.
    """
    path = f"/v1/meta/{_checked_id(share_id)}"
    if await share_egress_state() is None:
        raise ShareError("share_disabled")
    if _deleted(share_id):
        raise ShareError("share_gone")
    cached = _state.meta.get(share_id, _state.clock())
    if cached is not None:
        return cached
    meta: ShareMeta = _parse(ShareMeta, await share_request("GET", path, binding=None, read="meta"))
    if _deleted(share_id):  # deleted while it was fetched: never cache or serve it
        raise ShareError("share_gone")
    now = _state.clock()
    _state.meta.set(share_id, meta, now + META_CACHE_S, now)
    return meta


async def fetch_object(share_id: str) -> ShareObject:
    """``GET /{id}`` as bytes (``Accept: application/octet-stream``)."""
    response = await share_request(
        "GET",
        f"/{_checked_id(share_id)}",
        binding=None,
        read="bytes",
        accept="application/octet-stream",
        max_response_bytes=MAX_OBJECT_RESPONSE_BYTES,
    )
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in OBJECT_CONTENT_TYPES:
        # An edge page, a challenge or the HTML image page is never the object.
        logger.warning("Share service answered an object of an unexpected type")
        raise ShareError("share_unavailable")
    return ShareObject(content=response.content, content_type=content_type)


async def report_object(share_id: str, reason: str, details: str) -> None:
    """``POST /v1/reports`` (JWT variant, binding = ``id + "\\n" + reason``)."""
    share_id = _checked_id(share_id)
    await share_request(
        "POST",
        "/v1/reports",
        binding=f"{share_id}\n{reason}",
        json_body={"id": share_id, "reason": reason, "details": details},
    )


# Cursors are opaque to us: only bounded, URL-safe text is passed back.
MINE_CURSOR_RE = re.compile(r"[A-Za-z0-9_.~=-]{1,512}")
MINE_LIMIT_MAX = 200
_BLOCKED_CATEGORIES = frozenset(
    {"illegal", "child_abuse", "phishing", "malware", "spam", "copyright", "privacy", "other"}
)


def _mine_object(raw: dict[str, Any]) -> ShareMineObject | None:
    """A listed object we can show, else None (logged, never the raw body)."""
    try:
        obj = ShareMineObject.model_validate(raw)
    except ValueError:
        return None
    if not is_share_id(obj.id):
        return None
    if obj.status == "blocked":
        category = obj.blocked_category if obj.blocked_category in _BLOCKED_CATEGORIES else "other"
        obj = obj.model_copy(update={"blocked_category": category})
    else:
        obj = obj.model_copy(update={"blocked_category": None})
    if obj.content_type is not None and obj.content_type not in OBJECT_CONTENT_TYPES:
        obj = obj.model_copy(update={"content_type": None})
    return obj


async def list_mine(limit: int, cursor: str | None) -> ShareMinePage:
    """``GET /v1/mine``: this instance key's active and blocked objects, newest first.

    Tokened (binding = the literal ``mine``) but a read: it goes through even
    while a banned state holds. An entry outside the contract is dropped.
    """
    if not 1 <= limit <= MINE_LIMIT_MAX:
        raise ShareError("share_invalid_request")
    path = f"/v1/mine?limit={limit}"
    if cursor is not None:
        if not MINE_CURSOR_RE.fullmatch(cursor):
            raise ShareError("share_invalid_request")
        path += f"&cursor={cursor}"
    response = await share_request("GET", path, binding="mine", banned_ok=True)
    page: ShareMine = _parse(ShareMine, response)
    objects = [obj for raw in page.objects if (obj := _mine_object(raw)) is not None]
    if len(objects) != len(page.objects):
        logger.warning(
            "Share service listed %d objects outside the contract", len(page.objects) - len(objects)
        )
    next_cursor = page.next_cursor
    if next_cursor is not None and not MINE_CURSOR_RE.fullmatch(next_cursor):
        logger.warning("Share service answered a cursor outside the expected form")
        next_cursor = None
    return ShareMinePage(objects=objects, next_cursor=next_cursor)


def _deleted(share_id: str) -> bool:
    return _state.deleted.get(share_id, _state.clock()) is not None


async def _forget_object(share_id: str) -> None:
    """Drop every local copy of a removed object and remember it gone.

    The in-memory state goes first, with no await in between: the id is
    tombstoned for as long as a disk-cache entry can live, the meta dropped and
    both read keys negatively cached. Only then is the disk entry deleted, so a
    disk failure (logged, exception type only) or a read racing the delete never
    serves the object again.
    """
    now = _state.clock()
    _state.deleted.set(share_id, True, now + share_cache().ttl_s, now)
    _state.meta.pop(share_id)
    for key in (f"GET /v1/meta/{share_id}", f"GET /{share_id}"):
        _state.negative.set(key, ("share_gone", {}), now + NEGATIVE_CACHE_S, now)
    try:
        await share_cache().delete(share_id)
    except Exception as exc:  # noqa: BLE001 - the tombstone already hides it
        # The entry expires on its own (TTL_S); until then the tombstone hides it.
        logger.warning("Share cache eviction failed after a delete: %s", type(exc).__name__)


async def delete_object(share_id: str) -> None:
    """``DELETE /v1/objects/{id}`` (binding = the id), then evict the local copies.

    Idempotent on the service (an expired or already deleted object answers 204
    again). A 410 here means the operator blocked it: ``share_object_blocked``,
    the author cannot delete it (the service keeps the evidence). The local
    copies are dropped on success and whenever the object is gone or unknown; a
    local disk error never reports a failure (``_forget_object`` logs it).
    """
    share_id = _checked_id(share_id)
    try:
        await share_request("DELETE", f"/v1/objects/{share_id}", binding=share_id)
    except ShareError as exc:
        if exc.code in ("share_gone", "share_not_found"):
            await _forget_object(share_id)
        if exc.code == "share_gone":
            raise ShareError("share_object_blocked") from None
        raise
    await _forget_object(share_id)


# Objects --------------------------------------------------------------------


def sniff_image_type(content: bytes) -> str | None:
    """The image type the magic bytes say (PNG, JPEG, GIF, WebP), else None."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_dimensions(content: bytes | Path) -> tuple[int, int] | None:
    """Width and height read from the header (bytes or a file), or None when unreadable.

    The stored dimensions (``exif_rotation=False``): a rotation never changes the
    pixel count the caps bound.
    """
    source = content if isinstance(content, Path) else io.BytesIO(content)
    try:
        width, height = imagesize.get(source, exif_rotation=False)
    except Exception:  # noqa: BLE001 - any parser failure means "not an image"
        return None
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        return None
    return width, height


def checked_object(obj: ShareObject) -> ShareObject:
    """The object as it may be served, else a ``ShareError``.

    An image is served as an image only when the declared type, the magic bytes
    and a readable header of at most ``MAX_IMAGE_PIXELS`` agree (a GIF at most
    ``MAX_GIF_BYTES``). Raw bytes (``application/octet-stream``, an encrypted
    object) stay raw bytes, served as an attachment. Never anything else.
    """
    if len(obj.content) > MAX_OBJECT_RESPONSE_BYTES:
        raise ShareError("share_too_large")
    if obj.content_type == OCTET_STREAM:
        return ShareObject(content=obj.content, content_type=OCTET_STREAM)
    if obj.content_type not in OBJECT_CONTENT_TYPES:
        raise ShareError("share_type_refused")
    if len(obj.content) > MAX_IMAGE_BYTES:
        raise ShareError("share_too_large")
    if sniff_image_type(obj.content) != obj.content_type:
        raise ShareError("share_type_refused")
    if obj.content_type == "image/gif" and len(obj.content) > MAX_GIF_BYTES:
        raise ShareError("share_too_large")
    dimensions = image_dimensions(obj.content)
    if dimensions is None or dimensions[0] * dimensions[1] > MAX_IMAGE_PIXELS:
        raise ShareError("share_type_refused")
    return obj


async def read_object(share_id: str) -> ShareObject:
    """``GET /{id}`` through the disk cache: a checked object, else a ``ShareError``.

    Off means off: the opt-in is checked before the cache, so a cached object
    is never served while sharing is off. A hit makes no request; a miss
    fetches (read bucket, breaker, negative cache), checks and stores.
    """
    share_id = _checked_id(share_id)
    if await share_egress_state() is None:
        raise ShareError("share_disabled")
    # Deleted here: answered before the disk cache, which may still hold it.
    if _deleted(share_id):
        raise ShareError("share_gone")
    cache = share_cache()
    cached = await cache.get(share_id)
    if _deleted(share_id):  # deleted while the disk cache was read
        raise ShareError("share_gone")
    if cached is not None:
        return ShareObject(content=cached.content, content_type=cached.content_type)
    fetched = await fetch_object(share_id)
    try:
        obj = checked_object(fetched)
    except ShareError as exc:
        logger.warning("Share object refused locally: %s", exc.code)
        _note_refused_object(f"GET /{share_id}", exc.code)
        raise
    if _deleted(share_id):  # deleted while it was fetched: never store it
        raise ShareError("share_gone")
    await cache.set(share_id, CachedObject(content=obj.content, content_type=obj.content_type))
    if _deleted(share_id):  # deleted while it was stored: drop the copy just written
        try:
            await cache.delete(share_id)
        except Exception as exc:  # noqa: BLE001 - the tombstone still hides it
            logger.warning("Share cache eviction failed after a delete: %s", type(exc).__name__)
        raise ShareError("share_gone")
    return obj


async def clear_object_cache() -> int:
    """Empty the disk cache of objects (local only); the bytes freed."""
    return await share_cache().clear()
