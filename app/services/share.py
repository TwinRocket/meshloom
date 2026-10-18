"""Client of the share service (Links & Files): hosts, opt-in, errors, read protections.

The share service is a Cloudflare Worker on its own domain (contract:
``meshloom-community/docs/contracts/links-files.md``). It is a distinct opt-in
from Community, with its own hosts and its own switch, never an exception to
the Community guard.

- **Hosts.** ``SHARE_HOST`` is the write host, ``SHARE_HOSTS`` every host a
  link may carry (the current one and the former ones). Module constants read
  at call time (tests monkeypatch them), never an environment variable.
- **Opt-in.** ``share_egress_state()`` is None unless ``app_settings.share_enabled``;
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
  (``SHARE_ERRORS``). Any answer that is not a contract JSON body (a Cloudflare
  1027 page, a challenge, an HTML 404) is ``share_unavailable``.
- **Banned.** A 403 ``banned`` holds a banned state for ``BANNED_HOLD_S`` (in
  memory): every later write is refused locally (403 ``share_banned``) with no
  request. Reads stay open (the service keeps them open). A restart or any
  change of the share settings ends it (``note_share_settings_changed``).
- **Read protections.** Two ``aiolimiter`` buckets (``meta`` 60 and ``bytes`` 20
  per minute) that refuse rather than wait (429 ``share_rate_limited``,
  ``params.scope = "local"``); a negative cache of the service's JSON 404 and
  410 answers for ``NEGATIVE_CACHE_S``; and a circuit breaker
  (``app/net/breaker.py``): ``BREAKER_FAILURE_THRESHOLD`` consecutive failures
  (network, timeout, guard refusal, 5xx, non-JSON) stop every call for
  ``BREAKER_OPEN_SECONDS`` and answer ``share_unavailable`` locally.

Never log a token, a request body or a response body. Tests:
``tests/test_share_service.py``, ``tests/test_share_off_no_egress.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import httpx
from aiolimiter import AsyncLimiter
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict

from app.net.breaker import CircuitBreaker
from app.net.egress_guard import (
    CheckedResponse,
    EgressBlocked,
    HostAllowlist,
    error_code,
    follow_checked_redirects,
)
from app.repository import AppSettingsRepository
from app.services.instance_identity import mint_share_jwt
from app.services.ttl_lru import TtlLruCache
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

# Write host, and every host a link may carry (current first, then former ones).
SHARE_HOST = "meshl.ink"
SHARE_HOSTS: tuple[str, ...] = ("meshl.ink",)

# The id grammar of the contract: 7 characters of the Bitcoin base58 alphabet.
SHARE_ID_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{7}$")

SHARE_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=60.0, pool=5.0)
# Bound on one whole call, so a server dripping bytes cannot hold it open.
SHARE_TOTAL_TIMEOUT_S = 100.0
# Response caps: JSON answers are small; files are at most 10 MiB (contract).
MAX_JSON_RESPONSE_BYTES = 64 * 1024
MAX_OBJECT_RESPONSE_BYTES = 11 * 1024 * 1024
# What GET /{id} may answer as bytes: the image types the contract serves,
# and raw bytes (an encrypted object). Never text/html or anything else.
OBJECT_CONTENT_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/gif", "image/webp", "application/octet-stream"}
)

BREAKER_FAILURE_THRESHOLD = 3
BREAKER_OPEN_SECONDS = 300.0
BANNED_HOLD_S = 12 * 3600.0
NEGATIVE_CACHE_S = 600.0
NEGATIVE_CACHE_SIZE = 1024
# Reads per minute and per instance, refused (never queued) past the bucket.
READ_LIMITS: dict[str, int] = {"meta": 60, "bytes": 20}
READ_LIMIT_PERIOD_S = 60.0

ReadKind = Literal["meta", "bytes"]

# Local code -> (HTTP status, message). The UI translates errors.<code>.
SHARE_ERRORS: dict[str, tuple[int, str]] = {
    "share_disabled": (403, "Sharing is turned off on this instance"),
    "share_unavailable": (502, "The share service is unavailable"),
    "share_quota_exceeded": (429, "The share quota of this instance is reached"),
    "share_rate_limited": (429, "Too many share requests, try again later"),
    "share_banned": (403, "This instance is banned from the share service"),
    "share_too_large": (413, "The file is too large"),
    "share_type_refused": (415, "This file type is refused"),
    "share_not_found": (404, "No shared object with this id"),
    "share_gone": (410, "This shared object expired or was removed"),
    "share_target_blocked": (422, "This link target is refused"),
    "share_invalid_url": (400, "This URL cannot be shortened"),
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
}
# Service answers a read may cache as final (the contract never answers them
# for a storage failure).
_NEGATIVE_CODES = frozenset({"share_not_found", "share_gone"})
_SCOPE_RE = re.compile(r"^[a-z_]{1,32}$")


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


def is_share_host(host: str) -> bool:
    """True for a host of ``SHARE_HOSTS`` (compared without case)."""
    return host.lower().rstrip(".") in {h.lower() for h in SHARE_HOSTS}


async def share_egress_state() -> ShareEgress | None:
    """The share egress config when sharing is on, else None (no connection)."""
    settings = await AppSettingsRepository.get()
    if not settings.share_enabled:
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
            shown = service_code if _SCOPE_RE.match(service_code) else "unknown"
            logger.warning("Share service answered %s %s", response.status_code, shown)
        else:
            logger.warning("Share service answered %s (not a JSON error)", response.status_code)
        return ShareError("share_unavailable")
    if code in ("share_quota_exceeded", "share_rate_limited"):
        params: dict[str, Any] = {"retry_after": _retry_after(response)}
        if code == "share_quota_exceeded":
            scope = payload.get("scope") if payload is not None else None
            params["scope"] = scope if isinstance(scope, str) and _SCOPE_RE.match(scope) else None
        return ShareError(code, **params)
    return ShareError(code)


async def share_request(
    method: str,
    path: str,
    *,
    binding: str | None,
    json_body: dict[str, Any] | None = None,
    read: ReadKind | None = None,
    accept: str = "application/json",
    max_response_bytes: int = MAX_JSON_RESPONSE_BYTES,
) -> CheckedResponse:
    """One request to ``SHARE_HOST``; the 2xx answer, else a ``ShareError``.

    ``binding`` is the contract's binding string of a write (``None`` for a
    read, which carries no token). ``read`` names the read bucket and enables
    the negative cache.
    """
    generation = _state.generation
    state = await share_egress_state()
    if state is None:
        raise ShareError("share_disabled")
    _check_generation(generation)
    if binding is not None and share_banned():
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
    headers = {"Accept": accept, "User-Agent": f"Meshloom/{get_app_build_info().version}"}
    content: bytes | None = None
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
    except (httpx.HTTPError, EgressBlocked, TimeoutError, OSError) as exc:
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
    # a redirect or a contract error says it is.
    if response.status_code >= 500 or (payload is None and response.status_code >= 400):
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


def _checked_id(share_id: str) -> str:
    """A grammar id, or a local 404 (the service would answer ``not_found``)."""
    if not SHARE_ID_RE.match(share_id):
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
    if not SHARE_ID_RE.match(link.id) or not is_share_host(host):
        logger.warning("Share service answered a link outside the grammar")
        raise ShareError("share_unavailable")
    return link


async def get_meta(share_id: str) -> ShareMeta:
    """``GET /v1/meta/{id}``: what a link or file is, without following it."""
    path = f"/v1/meta/{_checked_id(share_id)}"
    return _parse(ShareMeta, await share_request("GET", path, binding=None, read="meta"))


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
