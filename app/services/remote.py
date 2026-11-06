"""Meshloom Remote on the instance: opt-in, registration, devices, enrolment, access
requests (docs/dev/remote.md; contracts in meshloom-community ``docs/contracts/``
``remote-registration.md`` and ``remote-relay.md``).

Remote is a blind relay: no TwinRocket account, no portal, no pairing. The instance
registers its Remote key (``instance_identity``, service ``remote``) and gets a slug;
who may reach it through ``<slug>.meshloom.link`` is decided here alone, by the device
passes this instance signs (``app/services/remote_passes.py``).

- **Opt-in and egress.** ``remote_state.enabled`` (off by default). Every registry
  request checks it first and answers 409 ``remote_disabled`` without any connection.
- **Community required** (MSHL-445). Remote runs on TwinRocket infrastructure, so it
  needs Community on (``meshloom_community.community_enabled()``): ``enable()`` and
  every registry request answer 409 ``remote_community_off`` while Community is off,
  and the relay client stays disconnected. Turning Community off pauses Remote: the
  stored opt-in, the registration and the devices are kept, the relay link closes and
  the pending access requests are dropped; turning Community on again resumes it.
  Requests go through the egress guard to exactly ``RELAY_HOST`` (HTTPS, no redirect,
  answer capped), each with a fresh instance JWT (``mint_remote_jwt``).
- **Attestation** (MSHL-445). ``POST /v1/instances`` carries ``X-Meshloom-Attestation``,
  the Community attestation of the Remote key (``app/services/community_attestation.py``,
  audience ``remote``), on every registration: enable, rotation and the relay client's
  re-registration after a close ``4003``. Without one it is still sent (the relay's grace
  for keys registered before 2026-11-10); a relay that asks for one gets the Community
  reason as the error (``remote_radio_not_heard``, ``remote_community_opted_out``,
  ``remote_community_banned``, ``remote_attestation_unavailable``). A refused one is
  renewed once and the registration retried once (then, for an instance already
  registered, once without it: the grace). The relay socket never needs it.
- **Registration.** ``POST /v1/instances`` (idempotent) stores ``slug``, ``hostname``
  and ``relay_url`` and declares the hostname to the host guard
  (``set_remote_hostname``); disabling Remote withdraws it. ``DELETE /v1/instances/self``
  unregisters and keeps the key (the registry holds the slug 30 days for it).
- **Rotation and restore.** ``rotate()`` replaces the key: the old registration is
  deleted while the old key still signs, then every device, enrolment token and access
  request is forgotten and the instance registers again (a new slug). Any restore
  (JSON or SQLite) runs the ``remote`` on_restore hook: registration, devices and
  tokens are forgotten and Remote is off (the ``instance_identity`` hook replaced the key).
- **Devices and the gate.** A device holds a ``browser`` pass (QR enrolment or an
  approved access request, 90 days, renewed while used: ``renew_browser_pass``) or a
  ``client`` pass (365 days). Revoking one adds
  its id to the revoked list of the current generation; revoking every device bumps
  ``gate_gen``. ``gate_state()`` is ``{gen, revoked}`` (the HELLO's ``gate`` and the
  CONTROL ``gate`` op), pushed at once to the relay link when there is one.
- **Relay link.** The relay client (``app/services/remote_relay.py``) installs a sender with
  ``set_relay_link`` and forwards relay CONTROL ops to ``handle_control``; it reads
  ``instance_info`` and ``gate_state`` for its HELLO and calls ``verify_device_pass`` on
  ``x-meshloom-pass``. Change listeners (``add_change_listener``) tell it to connect or
  disconnect.

- **Custom domain.** ``check_custom_domain()`` (MSHL-428) is the "Test" button of a
  personal domain that redirects (URL redirect, no CNAME) to ``<slug>.meshloom.link``:
  one GET, no redirect followed, to the user's domain through the egress guard (a
  user-chosen destination, so private addresses are refused), HTTPS then HTTP. The route
  takes ``take_domain_test_slot()`` first: one test per 5 s and 6 per minute for the
  whole instance (429 ``rate_limited`` with ``Retry-After``).
- **Alert e-mail.** ``alert_email_state``, ``set_alert_email`` and ``clear_alert_email``
  call the registry's ``/v1/instances/self`` and ``/alert-email``. The address only goes
  to the relay: it is never stored here, never logged, never read back (only its state).
  ``rotate()`` removes it first, while the old key still signs.

Never log a token, a pass, an enrolment URL, an access code or an alert address.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import re
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
from aiolimiter import AsyncLimiter
from fastapi import HTTPException

from app.auth.audit import AuditActor, record
from app.auth.models import User
from app.net.egress_guard import (
    CheckedResponse,
    EgressBlocked,
    HostAllowlist,
    error_code,
    follow_checked_redirects,
    pinned_async_client,
    validate_destination,
)
from app.repository.auth import UserRepository
from app.repository.remote import RemoteDevice, RemoteEnrollToken, RemoteRepository, RemoteState
from app.services import community_attestation, remote_passes
from app.services.backup_sections import RestoreSource, register_on_restore
from app.services.community_attestation import (
    ATTESTATION_HEADER,
    AUDIENCE_REMOTE,
    REJECTION_CODES,
    AttestationUnavailable,
)
from app.services.instance_identity import (
    REMOTE_SERVICE,
    fingerprint,
    instance_public_key,
    mint_remote_jwt,
    regenerate,
    sign_with_instance_key,
)
from app.services.remote_passes import (
    ID_RE,
    PassKind,
    PassSlot,
    PassState,
    new_id,
    normalize_access_code,
    pass_claims,
)
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

# Module constants read at call time (tests monkeypatch them), never an environment variable.
RELAY_HOST = "relay.meshloom.link"
REMOTE_DOMAIN = "meshloom.link"

REGISTRY_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
REGISTRY_TOTAL_TIMEOUT_S = 30.0
REGISTRY_MAX_RESPONSE_BYTES = 16384

ENROLL_TOKEN_LIFETIME_S = 15 * 60
BROWSER_PASS_LIFETIME_S = remote_passes.MAX_LIFETIME_S["browser"]
CLIENT_PASS_LIFETIME_S = remote_passes.MAX_LIFETIME_S["client"]
# A browser pass older than this is renewed by the next signed-in request.
BROWSER_PASS_RENEW_AFTER_S = 86400
# Per device, between two attempts: concurrent requests of a browser renew once, and a
# renewal the browser never received (or refused) is tried again without a surge.
BROWSER_PASS_RENEW_RETRY_S = 600
# Attempts kept in memory: forgetting one costs at most one more renewal.
RENEWAL_ATTEMPTS_MAX = 4096
# remote-relay.md: the instance answers ``enroll`` within 10 s; keep a margin.
ENROLL_ANSWER_TIMEOUT_S = 8.0
ACCESS_REQUEST_TTL_S = 600
ACCESS_REQUESTS_PENDING_MAX = 5
REVOKED_MAX = 1024
USER_AGENT_MAX = 256
LABEL_MAX = 64

# meshloom-remote src/alertEmail.ts (MAX_EMAIL_CHARS, EMAIL): the confirmation mail does
# the real check, this only spares the relay a call.
ALERT_EMAIL_MAX = 254
_ALERT_EMAIL_RE = re.compile(
    r'[^\s@\x00-\x1f\x7f"<>]{1,64}@[^\s@\x00-\x1f\x7f"<>]+\.[^\s@\x00-\x1f\x7f"<>]+'
)

SLUG_RE = re.compile(r"[a-z2-7]{10}")
_REGISTRY_CODES = frozenset(
    {
        "invalid_request",
        "token_malformed",
        "token_audience",
        "token_signature",
        "token_expired",
        "clock_skew",
        "token_replayed",
        "instance_banned",
        "instance_retired",
        "instance_paused",
        "instance_frozen",
        "instance_unknown",
        "payload_too_large",
        "rate_limited",
        "registration_paused",
        *REJECTION_CODES,
    }
)

REMOTE_ERRORS: dict[str, tuple[int, str]] = {
    "remote_disabled": (409, "Meshloom Remote is off"),
    "remote_not_registered": (409, "This instance is not registered with Meshloom Remote yet"),
    "remote_unavailable": (502, "Meshloom Remote did not answer"),
    "remote_registry_error": (502, "Meshloom Remote refused the request"),
    "remote_rate_limited": (429, "Too many requests to Meshloom Remote: try again later"),
    "remote_lan_only": (403, "Enrolment codes are issued on the local network only"),
    "remote_device_not_found": (404, "No such Remote device"),
    "remote_access_request_not_found": (404, "No pending access request matches"),
    "remote_offline": (503, "The relay link is not connected"),
    "remote_community_off": (409, "Meshloom Remote needs Community: turn Community on first"),
    "remote_alert_email_invalid": (400, "This is not a valid e-mail address"),
    "remote_alert_email_frozen": (403, "This address cannot be changed for now"),
    "remote_alert_email_paused": (503, "Alert e-mails cannot be changed right now"),
    # Community attestation of the Remote key (MSHL-445, services/community_attestation.py).
    "remote_radio_not_heard": (
        403,
        "No other station heard this radio recently: let it send an advert, then try again",
    ),
    "remote_community_opted_out": (403, "This radio left Meshloom Community"),
    "remote_community_banned": (403, "Meshloom Community blocked this radio"),
    "remote_attestation_unavailable": (
        503,
        "Meshloom Community could not vouch for this radio: try again later",
    ),
}

# Attestation refusal reason -> local code.
_ATTESTATION_CODES: dict[str, str] = {
    "radio_not_heard": "remote_radio_not_heard",
    "opted_out": "remote_community_opted_out",
    "banned": "remote_community_banned",
    "unavailable": "remote_attestation_unavailable",
}


class RemoteError(HTTPException):
    """A coded Remote error: ``detail = {code, message, params}``."""

    def __init__(self, code: str, **params: Any) -> None:
        status, message = REMOTE_ERRORS[code]
        super().__init__(
            status_code=status, detail={"code": code, "message": message, "params": params}
        )
        self.code = code
        self.params = params


# ---------------------------------------------------------------- in-memory state

ControlSender = Callable[[dict[str, Any]], Awaitable[bool]]


@dataclass
class AccessRequest:
    request_id: str
    code: str  # as the relay sent it (XXXXX-XXXXX)
    normalized: str
    user_agent: str
    created_at: int
    expires_at: int


@dataclass
class _Runtime:
    sender: ControlSender | None = None
    link_status: str | None = None
    access: dict[str, AccessRequest] | None = None
    listeners: list[Callable[[], Any]] | None = None
    lock: asyncio.Lock | None = None
    clock: Callable[[], float] = time.time
    # Device id -> monotonic time of its last renewal attempt, oldest first.
    renewal_attempts: OrderedDict[str, float] = field(default_factory=OrderedDict)


_rt = _Runtime(access={}, listeners=[])


def _now() -> int:
    return int(_rt.clock())


def _lock() -> asyncio.Lock:
    if _rt.lock is None:
        _rt.lock = asyncio.Lock()
    return _rt.lock


def reset_remote_for_tests(clock: Callable[[], float] | None = None) -> None:
    global _rt
    _rt = _Runtime(access={}, listeners=[], clock=clock or time.time)


def set_relay_link(sender: ControlSender | None, status: str | None = None) -> None:
    """The relay client's CONTROL sender (None when disconnected) and its status text."""
    _rt.sender = sender
    _rt.link_status = status if sender is not None or status else None


def set_link_status(status: str | None) -> None:
    """A UI state from the relay client (``connected``, ``replaced``, ``banned``...)."""
    _rt.link_status = status


def add_change_listener(callback: Callable[[], Any]) -> None:
    """Called (sync or async) after Remote is enabled, disabled, registered, rotated,
    unregistered or reset by a restore. Adding the same callback twice is a no-op."""
    assert _rt.listeners is not None
    if callback not in _rt.listeners:
        _rt.listeners.append(callback)


async def _notify() -> None:
    for callback in list(_rt.listeners or ()):
        try:
            result = callback()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("Remote change listener failed")


def _apply_hostname(state: RemoteState) -> None:
    from app.middleware import host_guard

    host_guard.set_remote_hostname(state.hostname if state.enabled and state.hostname else None)


async def load() -> None:
    """At startup: declare the Remote hostname to the host guard when Remote is on."""
    _apply_hostname(await RemoteRepository.get_state())


async def send_control(frame: dict[str, Any]) -> bool:
    """Send one CONTROL op over the relay link; False when there is none or it failed."""
    sender = _rt.sender
    if sender is None:
        return False
    try:
        return bool(await sender(frame))
    except Exception:
        logger.warning("Remote CONTROL %s could not be sent", frame.get("op"))
        return False


# ---------------------------------------------------------------- instance facts


def plugins_info() -> dict[str, Any]:
    """``{enabled, isolation}`` for the registration and the HELLO (isolation None
    when unknown)."""
    try:
        from app.plugins.host import isolation_level, plugin_manager

        enabled = plugin_manager.disabled_source() is None
    except Exception:
        return {"enabled": False, "isolation": None}
    isolation: str | None = None
    if enabled:
        try:
            level = isolation_level()
            isolation = level if level in ("none", "setuid", "bwrap") else None
        except Exception:
            isolation = None
    return {"enabled": enabled, "isolation": isolation}


def instance_info() -> dict[str, Any]:
    """``{version, install_kind}`` as the registry and the HELLO expect them."""
    from app.services.install_kind import detect_install_kind

    try:
        kind, _ = detect_install_kind()
    except Exception:
        kind = "source"
    return {"version": get_app_build_info().version, "install_kind": kind}


# ---------------------------------------------------------------- registry client


def _json(response: CheckedResponse) -> dict[str, Any] | None:
    try:
        value = json.loads(response.content)
    except (ValueError, UnicodeDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _registry_error(response: CheckedResponse) -> RemoteError:
    payload = _json(response) or {}
    code = payload.get("code")
    if response.status_code == 429:
        retry = response.headers.get("retry-after", "")
        return RemoteError(
            "remote_rate_limited", retry_after=int(retry) if retry.isdigit() else None
        )
    if isinstance(code, str) and code in _REGISTRY_CODES:
        return RemoteError("remote_registry_error", registry_code=code, status=response.status_code)
    return RemoteError("remote_unavailable", status=response.status_code)


async def _registry(
    method: str,
    path: str,
    *,
    json_body: dict[str, Any] | None = None,
    attestation: str | None = None,
    language: str | None = None,
) -> CheckedResponse:
    """One request to the registry, only while Remote is on. ``attestation`` is the
    Community attestation of the Remote key (``POST /v1/instances`` only), ``language``
    the language first in the mail it may send (``PUT /alert-email`` only)."""
    state = await RemoteRepository.get_state()
    if not state.enabled:
        raise RemoteError("remote_disabled")
    if not await community_on():
        raise RemoteError("remote_community_off")
    host = RELAY_HOST
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {await mint_remote_jwt()}",
        "User-Agent": f"Meshloom/{get_app_build_info().version}",
    }
    if attestation is not None:
        headers[ATTESTATION_HEADER] = attestation
    if language is not None:
        headers["Accept-Language"] = language
    content: bytes | None = None
    if json_body is not None:
        content = json.dumps(json_body, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    try:
        return await follow_checked_redirects(
            method,
            f"https://{host}{path}",
            timeout=REGISTRY_TIMEOUT,
            total_timeout=REGISTRY_TOTAL_TIMEOUT_S,
            allowed_hosts=HostAllowlist.of(host),
            schemes=frozenset({"https"}),
            max_response_bytes=REGISTRY_MAX_RESPONSE_BYTES,
            content=content,
            headers=headers,
        )
    except (EgressBlocked, httpx.HTTPError, TimeoutError, OSError) as exc:
        logger.warning("Remote registry %s %s failed: %s", method, path, error_code(exc))
        raise RemoteError("remote_unavailable") from None


def _rejected_attestation(response: CheckedResponse) -> bool:
    return response.status_code == 403 and (_json(response) or {}).get("code") in REJECTION_CODES


async def _register_locked(actor: AuditActor) -> RemoteState:
    """``POST /v1/instances`` with the Community attestation of the Remote key.

    Without an attestation (Community refused, a hold) the registration is still sent:
    a key registered before the relay required one may register without it (grace,
    remote-registration.md § *Attestation*). When the relay asks for one anyway, the
    Community reason is the error. A refused attestation is renewed once and the
    registration sent once more; refused again, an instance already registered tries a
    last time without it (the grace again: the relay verifies any header it gets), else
    it is ``remote_attestation_unavailable``. Three requests at most, never a loop.
    """
    body = {**instance_info(), "plugins": plugins_info()}
    try:
        state = await RemoteRepository.get_state()
        if not state.enabled:
            raise RemoteError("remote_disabled")
        if not await community_on():
            raise RemoteError("remote_community_off")
        token: str | None = None
        refusal: AttestationUnavailable | None = None
        try:
            token = await community_attestation.attestation_for(AUDIENCE_REMOTE)
        except AttestationUnavailable as exc:
            refusal = exc
        response = await _registry("POST", "/v1/instances", json_body=body, attestation=token)
        if _rejected_attestation(response):
            if refusal is not None:
                raise RemoteError(_ATTESTATION_CODES[refusal.reason])
            try:
                token = await community_attestation.renew_after_rejection(AUDIENCE_REMOTE, token)
            except AttestationUnavailable as exc:
                refusal, token = exc, None
            if token is not None:
                response = await _registry(
                    "POST", "/v1/instances", json_body=body, attestation=token
                )
            if (token is None or _rejected_attestation(response)) and state.slug:
                # A key registered before may still register without one (grace), and the
                # relay verifies any header it gets: a last try without it.
                logger.warning("Remote registry refused the attestation: trying without")
                response = await _registry("POST", "/v1/instances", json_body=body)
            elif token is None and refusal is not None:
                raise RemoteError(_ATTESTATION_CODES[refusal.reason])
            if _rejected_attestation(response):
                logger.warning("Remote registry refused a renewed attestation")
                raise RemoteError(
                    _ATTESTATION_CODES[refusal.reason]
                    if refusal is not None
                    else "remote_attestation_unavailable"
                )
        if response.status_code not in (200, 201):
            raise _registry_error(response)
        payload = _json(response) or {}
        slug, hostname, relay_url = (
            payload.get("slug"),
            payload.get("hostname"),
            payload.get("relay_url"),
        )
        if (
            not isinstance(slug, str)
            or not SLUG_RE.fullmatch(slug)
            or hostname != f"{slug}.{REMOTE_DOMAIN}"
            or not isinstance(relay_url, str)
            or not relay_url.startswith(f"wss://{RELAY_HOST}/")
        ):
            raise RemoteError("remote_unavailable", status=response.status_code)
    except RemoteError as exc:
        await RemoteRepository.update_state(last_error=exc.params.get("registry_code") or exc.code)
        await record("remote.linked", "failure", actor=actor, details={"error": exc.code})
        raise
    await RemoteRepository.update_state(
        slug=slug, hostname=hostname, relay_url=relay_url, registered_at=_now(), last_error=None
    )
    await record("remote.linked", "success", actor=actor, target=f"remote:{slug}")
    state = await RemoteRepository.get_state()
    _apply_hostname(state)
    return state


async def register(actor: AuditActor) -> RemoteState:
    """Register (or re-register: idempotent) the Remote key; the relay client calls it
    again after a close ``4003``."""
    async with _lock():
        state = await _register_locked(actor)
    await _notify()
    return state


async def enable(actor: AuditActor) -> RemoteState:
    """Turn Remote on and register when not registered yet. A registry failure is kept
    in ``last_error`` (Remote stays on; enabling again retries). Refused with 409
    ``remote_community_off`` while Community is off: nothing is written."""
    async with _lock():
        if not await community_on():
            raise RemoteError("remote_community_off")
        before = await RemoteRepository.get_state()
        if not before.enabled:
            await RemoteRepository.update_state(enabled=True)
            await record("remote.enabled", "success", actor=actor)
        state = await RemoteRepository.get_state()
        if not state.slug:
            try:
                state = await _register_locked(actor)
            except RemoteError:
                state = await RemoteRepository.get_state()
        _apply_hostname(state)
    await _notify()
    return state


async def disable(actor: AuditActor) -> RemoteState:
    """Turn Remote off: nothing leaves towards the relay; the registration is kept."""
    async with _lock():
        before = await RemoteRepository.get_state()
        if before.enabled:
            await RemoteRepository.update_state(enabled=False)
            await record("remote.disabled", "success", actor=actor)
        state = await RemoteRepository.get_state()
        _apply_hostname(state)
        _rt.access = {}
    await _notify()
    return state


async def unregister(actor: AuditActor) -> RemoteState:
    """Delete the registration (``DELETE /v1/instances/self``), keep the key, turn
    Remote off. Needs Remote on (nothing leaves otherwise)."""
    async with _lock():
        state = await RemoteRepository.get_state()
        if not state.enabled:
            raise RemoteError("remote_disabled")
        try:
            response = await _registry("DELETE", "/v1/instances/self")
            payload = _json(response) or {}
            if response.status_code != 204 and payload.get("code") != "instance_unknown":
                raise _registry_error(response)
        except RemoteError as exc:
            await record("remote.unlinked", "failure", actor=actor, details={"error": exc.code})
            raise
        await RemoteRepository.update_state(
            enabled=False, slug="", hostname="", relay_url="", registered_at=None, last_error=None
        )
        await record("remote.unlinked", "success", actor=actor, target=f"remote:{state.slug}")
        state = await RemoteRepository.get_state()
        _apply_hostname(state)
        _rt.access = {}
    await _notify()
    return state


async def rotate(actor: AuditActor) -> RemoteState:
    """A new Remote key: every device enrols again, and a new slug when Remote is on."""
    async with _lock():
        state = await RemoteRepository.get_state()
        if state.enabled and state.slug:
            # Before the registration: the relay refuses that during a pause, not this,
            # and once the key is gone nothing here can reach the address. A failure
            # (a freeze, no network) never blocks the rotation.
            try:
                await clear_alert_email(actor)
            except RemoteError as exc:
                logger.warning("Remote: the alert e-mail could not be removed: %s", exc.code)
            # While the old key still signs. A failure (a pause or a freeze refuses it, no
            # network) leaves the old registration on the relay, unreachable from here.
            try:
                await _registry("DELETE", "/v1/instances/self")
            except RemoteError:
                logger.warning("Remote: the old registration could not be deleted")
        await regenerate(REMOTE_SERVICE)
        await RemoteRepository.reset_all(keep_enabled=True)
        _rt.access = {}
        await record("remote.rotated", "success", actor=actor)
        state = await RemoteRepository.get_state()
        _apply_hostname(state)
        if state.enabled:
            try:
                state = await _register_locked(actor)
            except RemoteError:
                state = await RemoteRepository.get_state()
    await _notify()
    return state


async def _reset_after_restore(source: RestoreSource) -> None:
    """A restored instance is unregistered, with no device and Remote off: its key
    is new (the ``instance_identity`` hook) and every device enrols again."""
    await RemoteRepository.reset_all(keep_enabled=False)
    _rt.access = {}
    _apply_hostname(RemoteState())
    logger.info("Remote state reset after a %s restore", source)
    await _notify()


register_on_restore("remote", _reset_after_restore)


async def community_on() -> bool:
    """Community is on: Remote may reach the relay (MSHL-445)."""
    from app.services.meshloom_community import community_enabled

    return await community_enabled()


async def _on_community_change() -> None:
    """Community turned on or off: the relay client connects or disconnects again.
    Off, the pending access requests go (no relay link can answer them)."""
    if not await community_on():
        _rt.access = {}
    await _notify()


def _listen_to_community() -> None:
    from app.services.meshloom_community import add_enabled_change_listener

    add_enabled_change_listener(_on_community_change)


_listen_to_community()


# ---------------------------------------------------------------- alert e-mail


def _alert_email_error(response: CheckedResponse) -> RemoteError:
    code = (_json(response) or {}).get("code")
    if code == "invalid_request":
        return RemoteError("remote_alert_email_invalid")
    if code == "instance_frozen":
        return RemoteError("remote_alert_email_frozen")
    if code == "registration_paused":
        return RemoteError("remote_alert_email_paused")
    if code == "instance_unknown":
        return RemoteError("remote_not_registered")
    return _registry_error(response)


def _alert_email_state_of(response: CheckedResponse) -> str:
    alert = (_json(response) or {}).get("alert_email")
    state = alert.get("state") if isinstance(alert, dict) else None
    if state not in ("none", "pending", "confirmed"):
        raise RemoteError("remote_unavailable", status=response.status_code)
    return state


def _mail_language(accept_language: str | None) -> str | None:
    """The relay's mail is bilingual: only which language comes first."""
    first = (accept_language or "").split(",")[0].strip().lower()
    return next((lang for lang in ("fr", "en") if first.startswith(lang)), None)


async def alert_email_state() -> str:
    """``none``, ``pending`` or ``confirmed`` (``GET /v1/instances/self``). The registry
    never returns the address."""
    await _require_registered()
    response = await _registry("GET", "/v1/instances/self")
    if response.status_code != 200:
        raise _alert_email_error(response)
    return _alert_email_state_of(response)


async def set_alert_email(
    email: str, actor: AuditActor, *, accept_language: str | None = None
) -> str:
    """Send the address to the relay, which mails a confirmation link: the state is
    ``pending`` until it is followed. Replaces any previous address."""
    address = email.strip()
    if len(address) > ALERT_EMAIL_MAX or not _ALERT_EMAIL_RE.fullmatch(address):
        raise RemoteError("remote_alert_email_invalid")
    await _require_registered()
    try:
        response = await _registry(
            "PUT",
            "/v1/instances/self/alert-email",
            json_body={"email": address},
            language=_mail_language(accept_language),
        )
        if response.status_code != 202:
            raise _alert_email_error(response)
        state = _alert_email_state_of(response)
    except RemoteError as exc:
        await record("remote.alert_email_set", "failure", actor=actor, details={"error": exc.code})
        raise
    await record("remote.alert_email_set", "success", actor=actor)
    return state


async def clear_alert_email(actor: AuditActor) -> None:
    """Make the relay forget the address (``DELETE``, a no-op when there is none)."""
    await _require_registered()
    try:
        response = await _registry("DELETE", "/v1/instances/self/alert-email")
        if response.status_code != 204:
            raise _alert_email_error(response)
    except RemoteError as exc:
        await record(
            "remote.alert_email_cleared", "failure", actor=actor, details={"error": exc.code}
        )
        raise
    await record("remote.alert_email_cleared", "success", actor=actor)


# ---------------------------------------------------------------- gate and passes


async def gate_state() -> dict[str, Any]:
    """``{gen, revoked}``: the pass state the relay enforces (HELLO and CONTROL ``gate``)."""
    state = await RemoteRepository.get_state()
    revoked = await RemoteRepository.revoked_ids(
        state.gate_gen, _now(), grace=remote_passes.CLOCK_SKEW_S
    )
    return {"gen": state.gate_gen, "revoked": revoked}


async def push_gate() -> bool:
    """Send the whole pass state to the relay (it replaces the previous one). Without a
    link, nothing to do: the next HELLO carries it."""
    return await send_control({"op": "gate", **await gate_state()})


async def pass_state(*, seen_jti: frozenset[str] = frozenset()) -> PassState | None:
    """The instance's own view of the gate, None while unregistered."""
    state = await RemoteRepository.get_state()
    public_key = await instance_public_key(REMOTE_SERVICE)
    if not state.slug or public_key is None:
        return None
    revoked = {
        device.id
        for device in await RemoteRepository.list_devices()
        if device.revoked_at is not None and device.gen == state.gate_gen
    }
    return PassState(
        slug=state.slug,
        public_key=public_key,
        gen=state.gate_gen,
        revoked=frozenset(revoked),
        seen_jti=seen_jti,
    )


async def _sign(
    kind: PassKind,
    *,
    slug: str,
    sub: str,
    uid: str,
    gen: int,
    lifetime: int,
    jti: str | None = None,
) -> tuple[str, int]:
    now = _now()
    claims = pass_claims(
        kind, iss=slug, sub=sub, uid=uid, gen=gen, iat=now, lifetime=lifetime, jti=jti
    )
    payload_b64, message = remote_passes.signing_input(claims)
    signature = await sign_with_instance_key(REMOTE_SERVICE, message)
    return remote_passes.assemble_pass(payload_b64, signature), claims["exp"]


def _uid(user_id: int) -> str:
    return str(user_id)


async def verify_device_pass(token: str, slot: PassSlot) -> tuple[str, dict[str, Any] | None]:
    """The third check of the edge lock (``x-meshloom-pass``): the contract's verdict,
    then the device must be one of ours, not revoked, owned by the pass's ``uid``."""
    state = await pass_state()
    if state is None:
        return "pass_issuer", None
    verdict, claims = remote_passes.verify_pass(token, slot, state, _now())
    if verdict != "accept" or claims is None or claims["kind"] == "enroll":
        return verdict, claims
    device = await RemoteRepository.get_device(claims["sub"])
    if device is None or device.revoked_at is not None or _uid(device.user_id) != claims["uid"]:
        return "pass_revoked", None
    return verdict, claims


def _renewal_due(token: str) -> bool:
    """Whether to try renewing this already accepted pass, and record the attempt. No
    ``await``, so concurrent requests of one browser get a single attempt; nothing
    here reads the database or signs, so a pass that is not due costs next to nothing."""
    claims = remote_passes.decode_pass(token)
    if claims is None or claims["kind"] != "browser":
        return False
    if _now() - claims["iat"] < BROWSER_PASS_RENEW_AFTER_S:
        return False
    attempts, sub, moment = _rt.renewal_attempts, claims["sub"], time.monotonic()
    last = attempts.get(sub)
    if last is not None and moment - last < BROWSER_PASS_RENEW_RETRY_S:
        return False
    attempts[sub] = moment
    attempts.move_to_end(sub)
    while len(attempts) > RENEWAL_ATTEMPTS_MAX:
        attempts.popitem(last=False)
    return True


async def renew_browser_pass(token: str) -> str | None:
    """A new 90-day browser pass for the device holding ``token``, or None (contract
    "Renewal"). The relay client calls it when a signed-in request succeeded; the rate
    is ``_renewal_due``'s.

    The pass is checked again here, since the request may have revoked the device. A
    pass past its own ``exp``, which the verifier still accepts for clock skew, gets no
    second life. The new expiry is stored before the pass exists for anyone: the revoked
    list keeps a device until ``pass_expires_at``, and a device revoked meanwhile matches
    no row, so a revocation always wins."""
    if not _renewal_due(token):
        return None
    verdict, claims = await verify_device_pass(token, "cookie")
    if verdict != "accept" or claims is None or claims["exp"] <= _now():
        return None
    renewed, exp = await _sign(
        "browser",
        slug=claims["iss"],
        sub=claims["sub"],
        uid=claims["uid"],
        gen=claims["gen"],
        lifetime=BROWSER_PASS_LIFETIME_S,
    )
    if not await RemoteRepository.extend_device_pass(claims["sub"], exp):
        return None
    return renewed


async def _require_registered() -> RemoteState:
    state = await RemoteRepository.get_state()
    if not state.enabled:
        raise RemoteError("remote_disabled")
    if not state.slug:
        raise RemoteError("remote_not_registered")
    return state


async def _new_device(
    user_id: int, kind: PassKind, origin: str, label: str | None, state: RemoteState
) -> tuple[RemoteDevice, str]:
    device_id = new_id()
    lifetime = CLIENT_PASS_LIFETIME_S if kind == "client" else BROWSER_PASS_LIFETIME_S
    token, exp = await _sign(
        kind,
        slug=state.slug,
        sub=device_id,
        uid=_uid(user_id),
        gen=state.gate_gen,
        lifetime=lifetime,
    )
    device = RemoteDevice(
        id=device_id,
        user_id=user_id,
        kind=kind,
        label=(label or "").strip()[:LABEL_MAX] or None,
        origin=origin,
        gen=state.gate_gen,
        created_at=_now(),
        pass_expires_at=exp,
    )
    await RemoteRepository.add_device(device)
    return device, token


@dataclass(frozen=True)
class EnrollTicket:
    url: str
    expires_at: int


async def issue_enroll_token(user: User, actor: AuditActor) -> EnrollTicket:
    """A 15 min single-use enrolment token for ``user``, as the URL a QR code shows:
    ``https://<hostname>/_meshloom/enroll#token=<token>`` (the fragment never reaches
    a server log)."""
    state = await _require_registered()
    jti = new_id()
    token, exp = await _sign(
        "enroll",
        slug=state.slug,
        sub=new_id(),
        uid=_uid(user.id),
        gen=state.gate_gen,
        lifetime=ENROLL_TOKEN_LIFETIME_S,
        jti=jti,
    )
    await RemoteRepository.add_enroll_token(
        RemoteEnrollToken(
            jti=jti, user_id=user.id, gen=state.gate_gen, created_at=_now(), expires_at=exp
        ),
        _now(),
    )
    await record("remote.enroll_issued", "success", actor=actor, target=f"user:{user.id}")
    return EnrollTicket(
        url=f"https://{state.hostname}/_meshloom/enroll#token={token}", expires_at=exp
    )


async def issue_client_pass(
    user: User, label: str | None, actor: AuditActor
) -> tuple[RemoteDevice, str]:
    """A device with a 365-day ``client`` pass (``X-Meshloom-Pass``), shown once."""
    state = await _require_registered()
    device, token = await _new_device(user.id, "client", "client", label, state)
    await record(
        "remote.device_enrolled",
        "success",
        actor=actor,
        target=f"device:{device.id}",
        details={"origin": "client"},
    )
    return device, token


async def _answer_enroll(request_id: str, token: str) -> dict[str, Any]:
    reply: dict[str, Any] = {"op": "pass", "request_id": request_id}
    claims = remote_passes.decode_pass(token)
    if claims is None or claims["kind"] != "enroll":
        return {**reply, "error": "enroll_refused"}
    if not await RemoteRepository.enroll_token_known(claims["jti"]):
        return {**reply, "error": "enroll_unknown"}
    state = await pass_state()
    if state is None or not (await RemoteRepository.get_state()).enabled:
        return {**reply, "error": "enroll_refused"}
    verdict, checked = remote_passes.verify_pass(token, "enroll", state, _now())
    if verdict != "accept" or checked is None:
        return {**reply, "error": "enroll_refused"}
    row = await RemoteRepository.consume_enroll_token(checked["jti"], _now())
    if row is None or row.gen != state.gen or _uid(row.user_id) != checked["uid"]:
        return {**reply, "error": "enroll_refused"}
    user = await UserRepository.get(row.user_id)
    if user is None or user.disabled:
        return {**reply, "error": "enroll_refused"}
    device, browser_pass = await _new_device(
        row.user_id, "browser", "qr", None, await RemoteRepository.get_state()
    )
    await record(
        "remote.device_enrolled",
        "success",
        actor=AuditActor(via="relay", user_id=row.user_id),
        target=f"device:{device.id}",
        details={"origin": "qr"},
    )
    return {**reply, "pass": browser_pass}


async def answer_enroll(request_id: str, token: str) -> dict[str, Any]:
    """The answer to the relay's CONTROL ``enroll``: ``pass`` with a browser pass, or
    ``error`` (``enroll_unknown``: not a token of ours; ``enroll_refused``: anything
    else, a timeout included)."""
    try:
        return await asyncio.wait_for(_answer_enroll(request_id, token), ENROLL_ANSWER_TIMEOUT_S)
    except TimeoutError:
        return {"op": "pass", "request_id": request_id, "error": "enroll_refused"}


# ---------------------------------------------------------------- access requests


def _prune(now: int) -> dict[str, AccessRequest]:
    assert _rt.access is not None
    for request_id in [r for r, req in _rt.access.items() if req.expires_at < now]:
        del _rt.access[request_id]
    return _rt.access


def receive_access_request(frame: dict[str, Any]) -> AccessRequest | None:
    """Keep a relay ``access_request`` until an enrolled user grants or denies it
    (10 min, at most 5 pending: the oldest goes first). None when malformed."""
    request_id, code = frame.get("request_id"), frame.get("code")
    created_at, user_agent = frame.get("created_at"), frame.get("user_agent")
    normalized = normalize_access_code(code) if isinstance(code, str) else None
    if (
        not isinstance(request_id, str)
        or not ID_RE.fullmatch(request_id)
        or normalized is None
        or not isinstance(created_at, int)
        or isinstance(created_at, bool)
        or not isinstance(user_agent, str)
    ):
        return None
    now = _now()
    pending = _prune(now)
    while len(pending) >= ACCESS_REQUESTS_PENDING_MAX and request_id not in pending:
        oldest = min(pending.values(), key=lambda req: req.created_at)
        del pending[oldest.request_id]
    request = AccessRequest(
        request_id=request_id,
        code=f"{normalized[:5]}-{normalized[5:]}",
        normalized=normalized,
        user_agent=user_agent[:USER_AGENT_MAX],
        created_at=created_at,
        expires_at=now + ACCESS_REQUEST_TTL_S,
    )
    pending[request_id] = request
    return request


def pending_access_requests() -> list[AccessRequest]:
    return sorted(_prune(_now()).values(), key=lambda req: req.created_at)


async def approve_access(
    code: str, user: User, label: str | None, actor: AuditActor
) -> RemoteDevice:
    """Grant the pending request whose code the approver typed: a new browser device
    for the approver, its pass sent with ``access_grant``."""
    normalized = normalize_access_code(code)
    match = next(
        (req for req in _prune(_now()).values() if normalized and req.normalized == normalized),
        None,
    )
    if match is None:
        raise RemoteError("remote_access_request_not_found")
    state = await _require_registered()
    if _rt.sender is None:
        raise RemoteError("remote_offline")
    device, token = await _new_device(user.id, "browser", "approval", label, state)
    if not await send_control(
        {"op": "access_grant", "request_id": match.request_id, "pass": token}
    ):
        await RemoteRepository.delete_device(device.id)
        raise RemoteError("remote_offline")
    assert _rt.access is not None
    _rt.access.pop(match.request_id, None)
    await record("remote.access_granted", "success", actor=actor, target=f"device:{device.id}")
    return device


async def deny_access(request_id: str, actor: AuditActor) -> None:
    pending = _prune(_now())
    if request_id not in pending:
        raise RemoteError("remote_access_request_not_found")
    if not await send_control({"op": "access_deny", "request_id": request_id}):
        raise RemoteError("remote_offline")
    pending.pop(request_id, None)
    await record("remote.access_denied", "success", actor=actor)


# ---------------------------------------------------------------- devices


async def revoke_device(device_id: str, actor: AuditActor, *, owner_id: int | None) -> bool:
    """Revoke one device (``owner_id`` set: only one of that user's). Returns whether
    the new gate state reached the relay."""
    device = await RemoteRepository.get_device(device_id)
    if device is None or (owner_id is not None and device.user_id != owner_id):
        raise RemoteError("remote_device_not_found")
    async with _lock():
        await RemoteRepository.revoke_device(device_id, _now())
        state = await RemoteRepository.get_state()
        revoked = await RemoteRepository.revoked_ids(
            state.gate_gen, _now(), grace=remote_passes.CLOCK_SKEW_S
        )
        if len(revoked) > REVOKED_MAX:
            # The relay keeps at most 1024 ids: past that, every device enrols again.
            logger.warning("Remote: more than %d revoked devices, new pass generation", REVOKED_MAX)
            await RemoteRepository.bump_generation(_now())
    await record("remote.device_revoked", "success", actor=actor, target=f"device:{device_id}")
    return await push_gate()


async def revoke_all_devices(actor: AuditActor) -> bool:
    """Revoke every device (a new generation). Returns whether the relay got it."""
    async with _lock():
        gen = await RemoteRepository.bump_generation(_now())
    await record("remote.devices_revoked", "success", actor=actor, details={"gen": gen})
    return await push_gate()


# ---------------------------------------------------------------- relay CONTROL ops


async def handle_control(frame: dict[str, Any]) -> dict[str, Any] | None:
    """Handle a relay → instance CONTROL op; returns the reply op to send, if any.
    Unknown ops are ignored (the contract)."""
    op = frame.get("op")
    if op == "enroll":
        request_id, token = frame.get("request_id"), frame.get("token")
        if not isinstance(request_id, str) or not ID_RE.fullmatch(request_id):
            return None
        if not isinstance(token, str):
            return {"op": "pass", "request_id": request_id, "error": "enroll_refused"}
        return await answer_enroll(request_id, token)
    if op == "access_request":
        receive_access_request(frame)
        return None
    if op == "status":
        slug, hostname = frame.get("slug"), frame.get("hostname")
        if (
            isinstance(slug, str)
            and SLUG_RE.fullmatch(slug)
            and hostname == f"{slug}.{REMOTE_DOMAIN}"
        ):
            await RemoteRepository.update_state(slug=slug, hostname=hostname)
            _apply_hostname(await RemoteRepository.get_state())
        return None
    return None


# ---------------------------------------------------------------- read side


async def key_fingerprint() -> str | None:
    return await fingerprint(REMOTE_SERVICE)


def link_status() -> str | None:
    return _rt.link_status


def relay_connected() -> bool:
    return _rt.sender is not None


# ---------------------------------------------------------------- custom domain test

DOMAIN_TEST_TIMEOUT = httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=5.0)
DOMAIN_TEST_TOTAL_TIMEOUT_S = 20.0
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
# One limiter for the whole instance (the destination is typed by the caller, up to two
# GETs held for 20 s each): one test every 5 s and a few a minute, never queued.
DOMAIN_TEST_PERIOD_S = 5.0
DOMAIN_TEST_BUDGET = 6
DOMAIN_TEST_BUDGET_PERIOD_S = 60.0
_domain_test_limiters: list[AsyncLimiter] = []


def reset_domain_test_limiter() -> None:
    _domain_test_limiters.clear()


async def take_domain_test_slot() -> bool:
    """Whether a domain test may run now; takes the slot when it may. ``has_capacity()``
    then ``acquire()`` with no suspension between: a refusal never queues and two
    concurrent requests cannot both take the one slot."""
    if not _domain_test_limiters:
        _domain_test_limiters.extend(
            [
                AsyncLimiter(1, DOMAIN_TEST_PERIOD_S),
                AsyncLimiter(DOMAIN_TEST_BUDGET, DOMAIN_TEST_BUDGET_PERIOD_S),
            ]
        )
    if not all(limiter.has_capacity() for limiter in _domain_test_limiters):
        return False
    for limiter in _domain_test_limiters:
        await limiter.acquire()
    return True


_DOMAIN_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


@dataclass(frozen=True)
class DomainCheck:
    """``result`` is ``ok``, ``no_redirect``, ``wrong_target`` or ``invalid_domain``, or
    an ``unreachable`` reason in ``detail`` (an ``error_code``: ``egress_unresolvable``,
    ``egress_private_address``, ``timeout``, ``tls_error``, ``connect_error``...).
    ``target_host`` is the host the domain redirects to (untrusted text, host only)."""

    result: str
    status_code: int | None = None
    target_host: str | None = None
    detail: str | None = None


def normalize_domain(raw: str) -> str | None:
    """A bare host name from what the user typed (``https://`` and a trailing ``/``
    tolerated), or None when it is not a domain name (IP literal, port, path, one label)."""
    text = raw.strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    text = text.rstrip("/").rstrip(".")
    try:
        text = text.encode("idna").decode("ascii")
    except UnicodeError:
        return None
    labels = text.split(".")
    if len(text) > 253 or len(labels) < 2 or not all(_DOMAIN_LABEL_RE.fullmatch(x) for x in labels):
        return None
    if labels[-1].isdigit():  # 1.2.3.4
        return None
    return text


def _location_host(location: str) -> str | None:
    try:
        host = httpx.URL(location).host
    except httpx.InvalidURL:
        return None
    return host.rstrip(".").lower() or None


async def _probe_domain(scheme: str, domain: str) -> httpx.Response | None:
    """One GET without following anything; the headers are all that is read."""
    dest = await validate_destination(f"{scheme}://{domain}/", schemes=frozenset({scheme}))
    async with pinned_async_client(dest, timeout=DOMAIN_TEST_TIMEOUT) as client:
        async with client.stream(
            "GET",
            headers={
                "Accept": "*/*",
                "User-Agent": f"Meshloom/{get_app_build_info().version}",
            },
        ) as response:
            return httpx.Response(response.status_code, headers=response.headers)


async def check_custom_domain(raw_domain: str) -> DomainCheck:
    """Does ``raw_domain`` redirect to this instance's ``<slug>.meshloom.link``?

    409 ``remote_disabled`` while Remote is off and ``remote_not_registered`` without a
    hostname (nothing to compare with). Nothing is sent to the relay: the only
    destination is the user's domain, through the egress guard (private, loopback and
    metadata addresses refused), with no redirect followed and no cookie or credential.
    """
    state = await RemoteRepository.get_state()
    if not state.enabled:
        raise RemoteError("remote_disabled")
    if not state.hostname:
        raise RemoteError("remote_not_registered")
    domain = normalize_domain(raw_domain)
    if domain is None:
        return DomainCheck("invalid_domain")
    response: httpx.Response | None = None
    failure = "connect_error"
    try:
        async with asyncio.timeout(DOMAIN_TEST_TOTAL_TIMEOUT_S):
            for scheme in ("https", "http"):
                try:
                    response = await _probe_domain(scheme, domain)
                    break
                except (EgressBlocked, httpx.HTTPError, OSError) as exc:
                    failure = error_code(exc)
                    # A refusal or an unknown name will not change with the scheme.
                    if isinstance(exc, EgressBlocked) or failure == "timeout":
                        break
    except TimeoutError:
        failure = "timeout"
    if response is None:
        logger.info("Remote custom domain test failed: %s", failure)
        return DomainCheck("unreachable", detail=failure)
    location = response.headers.get("location")
    if response.status_code not in _REDIRECT_STATUSES or not location:
        return DomainCheck("no_redirect", status_code=response.status_code)
    target = _location_host(location)
    if target != state.hostname.lower():
        return DomainCheck("wrong_target", status_code=response.status_code, target_host=target)
    return DomainCheck("ok", status_code=response.status_code, target_host=target)
