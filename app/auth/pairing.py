"""Device pairing: a single-use code, shown as a QR, that a device trades for a device token.

Contract: ``docs/dev/device-pairing.md`` (meshenger reads it; ADR 001 E2).
Routes: ``app/routers/auth_pairing.py``.

- **Code.** ``mlp_`` + 128 random bits (``secrets.token_urlsafe(16)``), shown
  once, in the answer that created it (and inside its QR payload). The
  database keeps only its SHA-256 (``pairing_codes.code_hash``); the lookup
  goes by that hash and the stored hash is compared with
  ``hmac.compare_digest``. A value without the prefix or longer than
  ``MAX_CODE_LENGTH`` is refused before any query.
- **Bound to its creator.** A ``member`` or ``admin`` (``TOKEN_ROLES``) creates
  it from a signed-in session (never with a token), with one to five scopes
  its role grants (``scope_violation``) and a lifetime of at most
  ``MAX_TTL_S``. The device token it gives belongs to that account, with
  exactly those scopes, origin ``pairing``, no expiry.
- **Single use, checked again at redemption.** ``PairingCodeRepository.redeem``
  spends the code and inserts the token in one transaction. The creator must
  still exist, be enabled and hold the scopes (``authorize``): otherwise the
  code is spent and gives nothing (fail closed). Expiry and revocation apply
  at once: the expiry is compared at redemption, a revocation is one
  ``UPDATE``.
- **One answer for every bad code.** Unknown, malformed, used, expired,
  revoked, or refused at redemption: 410 ``auth_pairing_expired``, so the
  answer says nothing about which. Each is a failure for the login delays
  (``app/auth/ratelimit.py``) under the key ``LIMIT_KEY``, which no username
  can produce: the per-address rules (and the shared per-IP budget with
  ``POST /auth/login``) and the global slow-down apply.
- **Never under ingress.** A redemption through the Home Assistant Supervisor
  is refused (403 ``auth_pairing_ingress``) before any lookup: the device
  token would be refused there anyway (``app/auth/providers/token.py``).
- **QR payload.** ``meshloom://pair?d=<base64url, no padding, of the compact
  JSON {"v":1,"urls":[...],"code":"mlp_..."}>``. ``urls`` are the declared
  public addresses (``auth_settings.public_urls``), the Remote hostname when
  linked, then the address the creator used when it is a LAN one
  (``candidate_urls``).

Nothing here logs a code or a token.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import secrets
import time
from typing import Any

from app.auth.models import PairingCode, User
from app.auth.providers.token import TOKEN_ROLES, fresh_token, scope_violation
from app.auth.tokens import hash_token
from app.middleware import host_guard
from app.repository.auth import (
    AuthSettingsRepository,
    PairingCodeRepository,
    PairingRedemption,
)

CODE_PREFIX = "mlp_"
CODE_BYTES = 16
MAX_CODE_LENGTH = 128
DEFAULT_TTL_S = 300
MIN_TTL_S = 30
MAX_TTL_S = 600
PAYLOAD_VERSION = 1
PAYLOAD_SCHEME = "meshloom://pair"
MAX_URLS = 8
# The opaque ``extensions`` object a device may send, as compact JSON.
MAX_EXTENSIONS_BYTES = 4096

# The login limiter's "username" for redemptions: a control character, which
# no account name may contain (``normalize_username``).
LIMIT_KEY = "\x00pairing"


def fresh_code() -> str:
    """A fresh pairing code: ``mlp_`` + 128 random bits."""
    return CODE_PREFIX + secrets.token_urlsafe(CODE_BYTES)


def looks_like_code(value: str) -> bool:
    return value.startswith(CODE_PREFIX) and len(value) <= MAX_CODE_LENGTH


def qr_payload(code: str, urls: list[str]) -> str:
    """The text the QR encodes (``docs/dev/device-pairing.md``, "QR payload")."""
    body = json.dumps(
        {"v": PAYLOAD_VERSION, "urls": urls, "code": code},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    encoded = base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")
    return f"{PAYLOAD_SCHEME}?d={encoded}"


def decode_qr_payload(payload: str) -> dict[str, Any]:
    """The JSON object of a QR payload (tests, and the reference for clients)."""
    prefix = f"{PAYLOAD_SCHEME}?d="
    if not payload.startswith(prefix):
        raise ValueError("not a Meshloom pairing payload")
    data = payload[len(prefix) :]
    raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("payload is not an object")
    return value


def _lan_candidate(scope: Any, public_urls: list[str]) -> str | None:
    """The base URL the creator used, when a device on the same network could
    use it too: a direct request (not ingress or the relay) whose ``Host``
    names this instance (``host_guard.host_allowed``) and is not loopback."""
    if scope.get("auth_via", "lan") != "lan":
        return None
    raw_host = None
    for name, value in scope.get("headers") or ():
        if name.lower() == b"host":
            raw_host = value.decode("latin-1").strip()
            break
    if not raw_host or not host_guard.host_allowed(raw_host, public_urls):
        return None
    host = host_guard.request_host(raw_host)
    if host is None or host == "localhost":
        return None
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
    except ValueError:
        pass
    scheme = "https" if scope.get("scheme") in ("https", "wss") else "http"
    return f"{scheme}://{raw_host.lower()}"


async def candidate_urls(scope: Any) -> list[str]:
    """The addresses a device may try, best first: the declared public
    addresses, the Remote hostname, then the creator's LAN address."""
    public_urls = list((await AuthSettingsRepository.get()).public_urls)
    urls = [*public_urls, *(f"https://{host}" for host in sorted(host_guard.remote_hostnames()))]
    lan = _lan_candidate(scope, public_urls)
    if lan is not None:
        urls.append(lan)
    seen: set[str] = set()
    unique = [url for url in urls if not (url in seen or seen.add(url))]
    return unique[:MAX_URLS]


async def create_pairing(
    user: User, label: str, scopes: list[str], ttl: int, urls: list[str]
) -> tuple[PairingCode, str]:
    """Store a pending code for ``user`` and return its row and the code, to
    be shown once. The caller checked the role and the scopes. Raises
    ``PairingLimitError``."""
    code = fresh_code()
    now = int(time.time())
    row = await PairingCodeRepository.create(
        user.id,
        label,
        hash_token(code),
        sorted(set(scopes)),
        urls,
        expires_at=now + ttl,
        now=now,
    )
    return row, code


def authorize(owner: User | None, scopes: list[str]) -> str | None:
    """Whether the code's creator may still hold a token with ``scopes``:
    None, or the refusal reason (the audit's ``details.reason``)."""
    if owner is None or owner.disabled:
        return "owner_unavailable"
    if owner.role not in TOKEN_ROLES:
        return "owner_role"
    if not scopes or scope_violation(owner.role, scopes) is not None:
        return "scope"
    return None


async def redeem(
    presented: str, device_name: str, extensions: dict[str, Any] | None
) -> tuple[PairingRedemption, str | None]:
    """Trade a code for a device token. Returns what happened and, on
    success, the token secret (shown once)."""
    if not looks_like_code(presented):
        return PairingRedemption(reason="malformed"), None
    token = fresh_token()
    outcome = await PairingCodeRepository.redeem(
        hash_token(presented),
        device_name=device_name,
        extensions=extensions,
        token_hash=hash_token(token),
        authorize=authorize,
    )
    return outcome, token if outcome.reason is None else None


async def purge_pairings() -> int:
    """Delete the codes that can no longer give a token (hourly housekeeping)."""
    return await PairingCodeRepository.purge()
