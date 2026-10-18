"""Passkeys (WebAuthn) on py_webauthn (Access T52).

Rules (``docs/dev/auth.md``, "Passkeys"):

- **Relying party.** WebAuthn needs a secure context and a domain name, so
  the RP id is the host of the request only when it is one of:
  ``localhost`` (a secure context over http too), the host of a declared public
  address (``auth_settings.public_urls``, https only, so the request must be
  https: ``scope["scheme"]`` as the trusted-proxy interpreter left it), or the
  Remote hostname (``host_guard.remote_hostnames()``), through the relay or over
  https. Plain http to anything else, an IP literal, an undeclared name and Home
  Assistant ingress are refused (``PasskeyUnavailable``, 400
  ``auth_passkey_unsupported`` with ``params.reason``). The expected origins
  are derived the same way: the declared addresses of that host, the Remote
  hostname over https, or ``localhost`` with the request's scheme and port.
  ``GET /auth/session`` advertises ``methods.passkey`` only when this resolves.
- **Ceremonies.** Each options call creates a 32-byte challenge kept in memory
  (``Ceremonies``, ``CEREMONY_TTL_S``, ``CEREMONY_MAX_ENTRIES``) with its
  purpose, the RP id, the account and a binding: the session for a
  registration, the pending-login token and the client for a second factor, the
  client (``totp.client_binding``) for a passwordless sign-in. The verify call
  finds it by the challenge the browser signed (``clientDataJSON``) and removes
  it before any check: a challenge is used once, right or wrong.
- **Policy.** Registration: resident key preferred, user verification
  preferred, no attestation (``none``), the account's passkeys excluded.
  Passwordless sign-in: discoverable credentials, user verification
  **required**. Second factor: the account's credentials, user verification
  preferred. A user handle in an assertion must be the owner's
  (``user_handle``: derived, never the username).
- **Counter.** py_webauthn refuses an assertion whose counter did not grow when
  either side is non-zero (a cloned authenticator, or a replay); the new value
  is stored with a conditional ``UPDATE``, so two concurrent uses of one
  assertion cannot both pass. Authenticators that always report 0 (synced
  passkeys) stay at 0.
- **Accounts.** Only a local account with a password, outside ``kiosk``
  (``applicable``): a passkey adds a way in and never replaces the password, so
  removing the last one locks nobody out. Home Assistant and kiosk identities
  have none.
- **Never log** a challenge, a credential or a pending token.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.types import Scope
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.authentication.verify_authentication_response import VerifiedAuthentication
from webauthn.helpers import (
    parse_authentication_credential_json,
    parse_authenticator_data,
    parse_client_data_json,
    parse_registration_credential_json,
)
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    AuthenticatorTransport,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.registration.verify_registration_response import VerifiedRegistration

from app.auth.models import User
from app.repository.auth import StoredPasskey

logger = logging.getLogger(__name__)

CEREMONY_TTL_S = 300.0
# Live ceremonies kept per purpose, so the public passwordless options never
# touch a registration or second-factor challenge (``Ceremonies``).
CEREMONY_MAX_ENTRIES: dict[str, int] = {"login": 1024, "register": 256, "second_factor": 1024}
CHALLENGE_BYTES = 32
# The browser's own timeout for the ceremony (milliseconds), within the TTL.
TIMEOUT_MS = 120_000
RP_NAME_FALLBACK = "Meshloom"

# Passwordless options per client address and minute (a public route).
OPTIONS_PER_MINUTE = 20
# The name the options route checks the login limiter's address rules under:
# no failure is ever recorded for it, so only the per-address rules apply.
OPTIONS_LIMITER_NAME = "\x00passkey-options"
_OPTIONS_TABLE_MAX = 4096

Purpose = Literal["register", "login", "second_factor"]
UnavailableReason = Literal["ingress", "insecure", "host"]


class PasskeyUnavailable(Exception):
    """This request cannot run a WebAuthn ceremony (not a secure context with a
    domain this instance declares)."""

    def __init__(self, reason: UnavailableReason) -> None:
        super().__init__(reason)
        self.reason: UnavailableReason = reason


class PasskeyInvalid(Exception):
    """A ceremony that did not check. ``reason`` goes to the audit row only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class RelyingParty:
    rp_id: str
    origins: tuple[str, ...]


# --- Relying party --------------------------------------------------------------


def _host_and_port(raw: str | None) -> tuple[str, int | None] | None:
    from app.middleware.host_guard import request_host

    host = request_host(raw or "")
    if not host:
        return None
    try:
        port = urlsplit(f"http://{(raw or '').strip()}").port
    except ValueError:
        return None
    return host, port


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def relying_party_for(
    scope: Scope,
    public_urls: tuple[str, ...] | list[str],
    remote_hostnames: frozenset[str],
) -> RelyingParty:
    """The RP id and origins of this request, or ``PasskeyUnavailable``."""
    from app.auth.middleware import request_via
    from app.origin_policy import request_is_secure

    via = request_via(scope)
    if via == "ingress":
        raise PasskeyUnavailable("ingress")
    parsed = _host_and_port(Headers(scope=scope).get("host"))
    if parsed is None:
        raise PasskeyUnavailable("host")
    host, port = parsed
    if via == "relay":
        # The relay terminates TLS on the Remote hostname.
        if host in remote_hostnames:
            return RelyingParty(host, (f"https://{host}",))
        raise PasskeyUnavailable("host")
    secure = request_is_secure(scope.get("scheme", "http"))
    if host == "localhost":
        scheme = "https" if secure else "http"
        default = 443 if secure else 80
        netloc = host if port in (None, default) else f"{host}:{port}"
        return RelyingParty(host, (f"{scheme}://{netloc}",))
    if not secure:
        raise PasskeyUnavailable("insecure")
    if _is_ip(host):
        raise PasskeyUnavailable("host")
    origins: list[str] = []
    for url in public_urls:
        parts = urlsplit(url)
        if parts.scheme == "https" and (parts.hostname or "").rstrip(".").lower() == host:
            origin = f"https://{parts.netloc.lower()}"
            if origin not in origins:
                origins.append(origin)
    if host in remote_hostnames and f"https://{host}" not in origins:
        origins.append(f"https://{host}")
    if not origins:
        raise PasskeyUnavailable("host")
    return RelyingParty(host, tuple(origins))


async def relying_party(scope: Scope) -> RelyingParty:
    """``relying_party_for`` with the stored public addresses. An unreadable
    setting makes passkeys unavailable (fail closed)."""
    from app.middleware import host_guard
    from app.repository.auth import AuthSettingsRepository

    try:
        settings = await AuthSettingsRepository.get()
    except Exception:
        logger.exception("Could not read the public addresses: passkeys unavailable")
        raise PasskeyUnavailable("host") from None
    return relying_party_for(scope, settings.public_urls, host_guard.remote_hostnames())


async def available(scope: Scope) -> bool:
    try:
        await relying_party(scope)
    except PasskeyUnavailable:
        return False
    return True


# --- Accounts -------------------------------------------------------------------


def applicable(user: User) -> bool:
    """Only a local account with a password, outside ``kiosk``, has passkeys."""
    return user.provider == "local" and user.has_password and user.role != "kiosk"


def user_handle(user: User) -> bytes:
    """The WebAuthn user handle of an account: stable, opaque, no username."""
    return hashlib.sha256(f"meshloom-webauthn-user\0{user.id}\0{user.created_at}".encode()).digest()


# --- Ceremonies -----------------------------------------------------------------


@dataclass
class Ceremony:
    purpose: Purpose
    challenge: bytes
    rp_id: str
    user_id: int | None
    binding: str
    expires_at: float


def _key(challenge: bytes) -> str:
    return hashlib.sha256(challenge).hexdigest()


class CeremoniesFull(Exception):
    """The passwordless table is full of live challenges: refuse, never evict."""


class Ceremonies:
    """Challenges handed out and not used yet. Keyed by the SHA-256 of the
    challenge; bounded per purpose (``CEREMONY_MAX_ENTRIES``). A full ``login``
    table (public, anonymous) refuses a new challenge (``CeremoniesFull``)
    instead of evicting one before its TTL; the ``register`` (a session) and
    ``second_factor`` (a checked password) tables drop their own oldest entry.
    No purpose can push another one's challenges out."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._entries: dict[str, Ceremony] = {}

    def issue(self, purpose: Purpose, rp_id: str, user_id: int | None, binding: str) -> bytes:
        now = self._clock()
        self._sweep(now)
        same = [key for key, entry in self._entries.items() if entry.purpose == purpose]
        limit = CEREMONY_MAX_ENTRIES[purpose]
        if len(same) >= limit:
            if purpose == "login":
                raise CeremoniesFull()
            for key in same[: len(same) - limit + 1]:
                self._entries.pop(key, None)
        challenge = secrets.token_bytes(CHALLENGE_BYTES)
        self._entries[_key(challenge)] = Ceremony(
            purpose=purpose,
            challenge=challenge,
            rp_id=rp_id,
            user_id=user_id,
            binding=binding,
            expires_at=now + CEREMONY_TTL_S,
        )
        return challenge

    def take(self, challenge: bytes, purpose: Purpose, binding: str, rp_id: str) -> Ceremony:
        """Remove the ceremony of ``challenge`` and return it when it is live and
        matches; ``PasskeyInvalid`` otherwise. Removed in every case: one try."""
        entry = self._entries.pop(_key(challenge), None)
        if entry is None or entry.expires_at <= self._clock():
            raise PasskeyInvalid("challenge_unknown")
        if (
            entry.purpose != purpose
            or not secrets.compare_digest(entry.binding, binding)
            or entry.rp_id != rp_id
        ):
            raise PasskeyInvalid("challenge_mismatch")
        return entry

    def __len__(self) -> int:
        return len(self._entries)

    def _sweep(self, now: float) -> None:
        for key in [k for k, entry in self._entries.items() if entry.expires_at <= now]:
            self._entries.pop(key, None)


ceremonies = Ceremonies()


class OptionsLimiter:
    """A fixed one-minute window per client address for the public options route."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._windows: dict[str, tuple[float, int]] = {}

    def allow(self, address: str | None) -> bool:
        if address is None:
            address = ""
        now = self._clock()
        start, count = self._windows.pop(address, (now, 0))
        if now - start >= 60.0:
            start, count = now, 0
        while len(self._windows) >= _OPTIONS_TABLE_MAX:
            self._windows.pop(next(iter(self._windows)))
        self._windows[address] = (start, count + 1)
        return count < OPTIONS_PER_MINUTE

    def retry_after(self, address: str | None) -> int:
        start, _ = self._windows.get(address or "", (self._clock(), 0))
        return max(1, int(60.0 - (self._clock() - start)) + 1)


options_limiter = OptionsLimiter()


def reset_state() -> None:
    """Tests: forget every ceremony and options window."""
    ceremonies._entries.clear()
    options_limiter._windows.clear()


def registration_binding(session_id: str | None) -> str:
    return f"session:{session_id or ''}"


def second_factor_binding(pending_token_hash: str, client: str) -> str:
    return f"pending:{pending_token_hash}:{client}"


def login_binding(client: str) -> str:
    return f"client:{client}"


# --- Options --------------------------------------------------------------------


def _descriptor(passkey: StoredPasskey) -> PublicKeyCredentialDescriptor:
    transports: list[AuthenticatorTransport] = []
    for item in passkey.transports:
        try:
            transports.append(AuthenticatorTransport(item))
        except ValueError:
            continue
    return PublicKeyCredentialDescriptor(id=passkey.credential_id, transports=transports or None)


def _to_json(options: Any) -> dict[str, Any]:
    return json.loads(options_to_json(options))


def registration_options(
    rp: RelyingParty,
    user: User,
    existing: list[StoredPasskey],
    challenge: bytes,
    rp_name: str | None,
) -> dict[str, Any]:
    """``PublicKeyCredentialCreationOptions`` as the browser's JSON."""
    options = generate_registration_options(
        rp_id=rp.rp_id,
        rp_name=rp_name or RP_NAME_FALLBACK,
        user_name=user.username,
        user_id=user_handle(user),
        user_display_name=user.display_name or user.username,
        challenge=challenge,
        timeout=TIMEOUT_MS,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
        exclude_credentials=[_descriptor(passkey) for passkey in existing],
    )
    return _to_json(options)


def authentication_options(
    rp: RelyingParty,
    challenge: bytes,
    allowed: list[StoredPasskey] | None,
    *,
    require_user_verification: bool,
) -> dict[str, Any]:
    """``PublicKeyCredentialRequestOptions``: ``allowed`` None means
    discoverable credentials (the passwordless sign-in)."""
    options = generate_authentication_options(
        rp_id=rp.rp_id,
        challenge=challenge,
        timeout=TIMEOUT_MS,
        allow_credentials=[_descriptor(passkey) for passkey in allowed or []],
        user_verification=(
            UserVerificationRequirement.REQUIRED
            if require_user_verification
            else UserVerificationRequirement.PREFERRED
        ),
    )
    return _to_json(options)


# --- Responses ------------------------------------------------------------------


def credential_challenge(credential: dict[str, Any]) -> bytes:
    """The challenge the browser signed, read from ``clientDataJSON`` before
    any verification (to find the ceremony). ``PasskeyInvalid`` when unreadable."""
    response = credential.get("response") if isinstance(credential, dict) else None
    raw = response.get("clientDataJSON") if isinstance(response, dict) else None
    if not isinstance(raw, str):
        raise PasskeyInvalid("malformed")
    try:
        from webauthn import base64url_to_bytes

        return parse_client_data_json(base64url_to_bytes(raw)).challenge
    except Exception:
        raise PasskeyInvalid("malformed") from None


def verify_registration(
    rp: RelyingParty, credential: dict[str, Any], ceremony: Ceremony
) -> tuple[VerifiedRegistration, list[str]]:
    """Verify an attestation; returns it with the transports the browser listed."""
    try:
        parsed = parse_registration_credential_json(credential)
        verified = verify_registration_response(
            credential=parsed,
            expected_challenge=ceremony.challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=list(rp.origins),
            require_user_verification=False,
        )
    except Exception as exc:
        logger.info("Passkey registration refused (%s)", type(exc).__name__)
        raise PasskeyInvalid("verification_failed") from None
    transports = [transport.value for transport in parsed.response.transports or []]
    return verified, transports


def assertion_credential_id(credential: dict[str, Any]) -> tuple[bytes, bytes | None]:
    """The credential id and user handle of an assertion, unverified."""
    try:
        parsed = parse_authentication_credential_json(credential)
    except Exception:
        raise PasskeyInvalid("malformed") from None
    return parsed.raw_id, parsed.response.user_handle


def verify_assertion(
    rp: RelyingParty,
    credential: dict[str, Any],
    ceremony: Ceremony,
    passkey: StoredPasskey,
    owner: User,
    *,
    require_user_verification: bool,
) -> VerifiedAuthentication:
    """Verify an assertion by ``passkey``, owned by ``owner``."""
    try:
        parsed = parse_authentication_credential_json(credential)
    except Exception:
        raise PasskeyInvalid("malformed") from None
    handle = parsed.response.user_handle
    if handle is not None and not secrets.compare_digest(handle, user_handle(owner)):
        raise PasskeyInvalid("user_handle_mismatch")
    try:
        return verify_authentication_response(
            credential=parsed,
            expected_challenge=ceremony.challenge,
            expected_rp_id=rp.rp_id,
            expected_origin=list(rp.origins),
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=require_user_verification,
        )
    except Exception as exc:
        reason = "verification_failed"
        try:
            reported = parse_authenticator_data(parsed.response.authenticator_data).sign_count
            if (reported > 0 or passkey.sign_count > 0) and reported <= passkey.sign_count:
                reason = "sign_count_not_increased"
        except Exception:
            pass
        logger.info("Passkey assertion refused (%s, %s)", type(exc).__name__, reason)
        raise PasskeyInvalid(reason) from None
