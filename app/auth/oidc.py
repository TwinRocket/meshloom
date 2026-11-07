"""Sign-in with an OpenID Connect provider the admin brings (MSHL-427).

Configured only in the UI (Settings → Connection → OpenID Connect, stored in
``oidc_provider``, ``app/repository/oidc.py``); no environment variable changes any
of it. Contract and rules: ``docs/dev/auth.md``, "OpenID Connect".

- **Flow.** Authorization code with PKCE (S256), ``state`` and ``nonce``. ``GET
  /api/auth/oidc/start`` keeps the request in memory (``AuthRequests``: 256-bit
  state, single use, 10 minutes, 1024 at most, a restart forgets them) bound to the
  browser by a short ``HttpOnly`` cookie (its SHA-256 is kept, compared in constant
  time) and to the client (``totp.client_binding``), then redirects to the provider.
  ``GET /api/auth/oidc/callback`` takes the request back, exchanges the code at the
  token endpoint (client secret by HTTP Basic or in the form) and verifies the ID
  token before anything else.
- **ID token.** joserfc, asymmetric algorithms only (``ALLOWED_ALGORITHMS``: never
  ``none`` or an HMAC one), keys from the provider's JWKS (refetched once when a key
  id is unknown, at most every ``JWKS_REFRESH_MIN_S``). ``iss`` must be the configured
  issuer exactly, ``aud`` must hold the client id (and ``azp`` be it when there are
  several audiences), ``exp``/``iat`` with ``CLOCK_LEEWAY_S``, ``nonce`` the one sent.
  The discovery document's ``issuer`` must equal the configured one too.
- **Account.** An existing local account only, by default: a link written by an admin
  (``oidc_links``) on the provider's subject, or on an e-mail that the provider says is
  verified (``email_verified`` true); the first sign-in through an e-mail link records
  the subject. ``auto_create`` (off by default) creates a ``viewer`` (``AUTO_CREATE_ROLE``,
  the lowest role of a person; never ``kiosk``, never more) linked to the subject. A
  disabled account, a kiosk, a Home Assistant or system account is never signed in.
  The account's role decides what it may do, as for a password sign-in.
- **Second factor.** An account with TOTP on gets the pending-login token of a password
  sign-in (``POST /api/auth/login/2fa`` finishes it): the provider never stands in for
  the local factor.
- **Egress.** Every request to the provider goes through ``app/net/egress_guard.py``
  (pinned addresses, no redirect for the token request, 256 KiB at most, 10 s). https
  only, unless ``allow_private_network`` (a provider on the local network): then http
  too, but only to a private address (``auth_oidc_http_public`` otherwise).
- **Audit.** A refused callback writes a ``login`` failure row (``method: oidc``),
  throttled by ``FailureAudit`` (one row per address per minute, 10 in all); none
  while the provider is off or incomplete.
- **Never** in a log or an audit row: the code, a token, the client secret, the
  state or the nonce. Errors are stable codes (``OidcError.code``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
from joserfc import errors as jose_errors
from joserfc import jwt
from joserfc.jwk import JWKRegistry, KeySet
from joserfc.jwt import JWTClaimsRegistry

from app.auth.models import Role, User
from app.auth.tokens import hash_token, new_token
from app.net import egress_guard
from app.repository.auth import (
    InvalidUsernameError,
    UsernameTakenError,
    UserRepository,
    normalize_username,
)
from app.repository.oidc import (
    EMAIL_MAX_LENGTH,
    SUBJECT_MAX_LENGTH,
    LinkExistsError,
    OidcLink,
    OidcLinkRepository,
    OidcProvider,
)

logger = logging.getLogger(__name__)

DISCOVERY_PATH = "/.well-known/openid-configuration"
CALLBACK_PATH = "/api/auth/oidc/callback"
HTTP_TIMEOUT_S = 10.0
MAX_DOCUMENT_BYTES = 256 * 1024
MAX_URL_LENGTH = 2048
METADATA_TTL_S = 3600.0
JWKS_REFRESH_MIN_S = 60.0
REQUEST_TTL_S = 600.0
REQUEST_MAX_ENTRIES = 1024
FAILURE_AUDIT_WINDOW_S = 60.0
FAILURE_AUDIT_MAX_PER_WINDOW = 10
CLOCK_LEEWAY_S = 60
ALLOWED_ALGORITHMS = (
    "RS256",
    "RS384",
    "RS512",
    "PS256",
    "PS384",
    "PS512",
    "ES256",
    "ES384",
    "ES512",
    "EdDSA",
)
AUTO_CREATE_ROLE: Role = "viewer"
STATE_COOKIE = "meshloom_oidc"
SECURE_STATE_COOKIE = "__Host-meshloom_oidc"
_SCOPE = re.compile(r"[A-Za-z0-9_:./\-]{1,64}")


class OidcError(Exception):
    """A refused sign-in or provider answer. ``code`` is translated by the UI
    (``errors.<code>``); ``params`` never holds a secret."""

    def __init__(self, code: str, **params: str) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def clock() -> float:
    return time.time()


# --- Configuration helpers ----------------------------------------------------


def schemes_for(allow_private_network: bool) -> frozenset[str]:
    return frozenset({"https", "http"}) if allow_private_network else frozenset({"https"})


def valid_issuer(issuer: str, allow_private_network: bool) -> bool:
    """An absolute https URL (http too with the private-network opt-in), no user
    info, query or fragment."""
    if not issuer or len(issuer) > 512 or any(ch.isspace() for ch in issuer):
        return False
    try:
        parts = urlsplit(issuer)
    except ValueError:
        return False
    return (
        parts.scheme in schemes_for(allow_private_network)
        and bool(parts.hostname)
        and "@" not in parts.netloc
        and not parts.query
        and not parts.fragment
    )


def normalize_scopes(scopes: str) -> str | None:
    """Space-separated scope tokens with ``openid`` first, or None when one is malformed."""
    tokens = scopes.split()
    if not tokens or any(not _SCOPE.fullmatch(token) for token in tokens) or len(tokens) > 16:
        return None
    ordered = ["openid", *[t for t in dict.fromkeys(tokens) if t != "openid"]]
    return " ".join(ordered)


def normalize_email(value: Any) -> str | None:
    """A comparable e-mail: NFC, trimmed, lowercased; None when it is not one."""
    if not isinstance(value, str):
        return None
    email = unicodedata.normalize("NFC", value).strip().lower()
    if not email or len(email) > EMAIL_MAX_LENGTH or email.count("@") != 1:
        return None
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") for ch in email):
        return None
    local, domain = email.split("@")
    return email if local and domain else None


def valid_subject(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= SUBJECT_MAX_LENGTH
        and not any(unicodedata.category(ch).startswith("C") for ch in value)
    )


# --- Provider documents ---------------------------------------------------------


@dataclass(frozen=True)
class Metadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str | None = None


@dataclass
class _Cached:
    at: float
    value: Any


_metadata_cache: dict[tuple[str, bool], _Cached] = {}
_jwks_cache: dict[tuple[str, bool], _Cached] = {}


def reset_caches() -> None:
    """Forget the discovery documents and keys (a settings change, tests)."""
    _metadata_cache.clear()
    _jwks_cache.clear()


async def _check_http(url: str, allow_private_network: bool) -> None:
    """Plain http only to a private address, and only with the opt-in."""
    if urlsplit(url).scheme.lower() != "http":
        return
    if not allow_private_network:
        raise OidcError("auth_oidc_unavailable", reason="egress_scheme")
    dest = await egress_guard.validate_destination(
        url, allow_private_network=True, schemes=frozenset({"http"})
    )
    if not all(egress_guard.is_private_address(ip) for ip in dest.ips):
        raise OidcError("auth_oidc_http_public")


async def _send(
    method: str,
    url: str,
    allow_private_network: bool,
    *,
    content: bytes | None = None,
    headers: dict[str, str] | None = None,
    max_redirects: int = 0,
) -> egress_guard.CheckedResponse:
    try:
        await _check_http(url, allow_private_network)
        return await egress_guard.follow_checked_redirects(
            method,
            url,
            timeout=HTTP_TIMEOUT_S,
            total_timeout=HTTP_TIMEOUT_S * 2,
            allow_private_network=allow_private_network,
            schemes=schemes_for(allow_private_network),
            max_redirects=max_redirects,
            max_response_bytes=MAX_DOCUMENT_BYTES,
            content=content,
            headers=headers,
        )
    except OidcError:
        raise
    except (egress_guard.EgressBlocked, httpx.HTTPError, OSError, TimeoutError) as exc:
        raise OidcError("auth_oidc_unavailable", reason=egress_guard.error_code(exc)) from None


def _json_object(response: egress_guard.CheckedResponse, code: str) -> dict[str, Any]:
    try:
        value = json.loads(response.content)
    except (ValueError, UnicodeDecodeError):
        raise OidcError(code) from None
    if not isinstance(value, dict):
        raise OidcError(code)
    return value


def _endpoint(doc: dict[str, Any], name: str, allow_private_network: bool) -> str | None:
    value = doc.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > MAX_URL_LENGTH:
        raise OidcError("auth_oidc_metadata_invalid", field=name)
    try:
        parts = urlsplit(value)
    except ValueError:
        raise OidcError("auth_oidc_metadata_invalid", field=name) from None
    if (
        parts.scheme not in schemes_for(allow_private_network)
        or not parts.hostname
        or "@" in parts.netloc
        or parts.fragment
    ):
        raise OidcError("auth_oidc_metadata_invalid", field=name)
    return value


async def metadata(config: OidcProvider, *, refresh: bool = False) -> Metadata:
    """The provider's discovery document (cached ``METADATA_TTL_S``)."""
    key = (config.issuer, config.allow_private_network)
    cached = _metadata_cache.get(key)
    if cached is not None and not refresh and clock() - cached.at < METADATA_TTL_S:
        return cached.value
    if not valid_issuer(config.issuer, config.allow_private_network):
        raise OidcError("auth_oidc_issuer_invalid")
    url = config.issuer.rstrip("/") + DISCOVERY_PATH
    response = await _send(
        "GET",
        url,
        config.allow_private_network,
        headers={"Accept": "application/json"},
        max_redirects=2,
    )
    if response.status_code != 200:
        raise OidcError("auth_oidc_unavailable", reason=f"http_{response.status_code}")
    doc = _json_object(response, "auth_oidc_metadata_invalid")
    issuer = doc.get("issuer")
    if not isinstance(issuer, str) or issuer != config.issuer:
        found = issuer[:512] if isinstance(issuer, str) else ""
        raise OidcError("auth_oidc_issuer_mismatch", issuer=found)
    allow = config.allow_private_network
    authorization = _endpoint(doc, "authorization_endpoint", allow)
    token = _endpoint(doc, "token_endpoint", allow)
    jwks = _endpoint(doc, "jwks_uri", allow)
    if authorization is None or token is None or jwks is None:
        raise OidcError("auth_oidc_metadata_invalid")
    value = Metadata(
        issuer=issuer,
        authorization_endpoint=authorization,
        token_endpoint=token,
        jwks_uri=jwks,
        userinfo_endpoint=_endpoint(doc, "userinfo_endpoint", allow),
    )
    _metadata_cache[key] = _Cached(clock(), value)
    return value


def _import_keys(doc: dict[str, Any]) -> KeySet:
    """The signing keys of a JWKS; keys of another use or type are skipped."""
    raw = doc.get("keys")
    keys = []
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict) or entry.get("kty") not in ("RSA", "EC", "OKP"):
            continue
        if entry.get("use", "sig") != "sig":
            continue
        try:
            keys.append(JWKRegistry.import_key(entry))
        except (jose_errors.JoseError, ValueError, TypeError, KeyError):
            continue
    if not keys:
        raise OidcError("auth_oidc_metadata_invalid", field="jwks_uri")
    return KeySet(keys)


async def keyset(meta: Metadata, allow_private_network: bool, *, refresh: bool = False) -> KeySet:
    key = (meta.jwks_uri, allow_private_network)
    cached = _jwks_cache.get(key)
    now = clock()
    if cached is not None:
        fresh = now - cached.at < METADATA_TTL_S
        if fresh and (not refresh or now - cached.at < JWKS_REFRESH_MIN_S):
            return cached.value
    response = await _send(
        "GET", meta.jwks_uri, allow_private_network, headers={"Accept": "application/json"}
    )
    if response.status_code != 200:
        raise OidcError("auth_oidc_unavailable", reason=f"http_{response.status_code}")
    value = _import_keys(_json_object(response, "auth_oidc_metadata_invalid"))
    _jwks_cache[key] = _Cached(now, value)
    return value


# --- Pending authorization requests ------------------------------------------------


@dataclass
class AuthRequest:
    issuer: str
    nonce: str
    verifier: str
    redirect_uri: str
    browser_hash: str
    client: str
    remember: bool
    expires_at: float


@dataclass
class IssuedRequest:
    state: str
    browser_token: str
    request: AuthRequest = field(repr=False)


class AuthRequests:
    """Started sign-ins, keyed by the SHA-256 of their state. Single use, bounded."""

    def __init__(self, clock_fn: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock_fn
        self._entries: dict[str, AuthRequest] = {}

    def issue(
        self, *, issuer: str, redirect_uri: str, client: str, remember: bool
    ) -> IssuedRequest:
        now = self._clock()
        for key in [k for k, v in self._entries.items() if v.expires_at <= now]:
            self._entries.pop(key, None)
        while len(self._entries) >= REQUEST_MAX_ENTRIES:
            self._entries.pop(next(iter(self._entries)))
        state, browser = new_token(), new_token()
        entry = AuthRequest(
            issuer=issuer,
            nonce=new_token(),
            verifier=new_token(),
            redirect_uri=redirect_uri,
            browser_hash=hash_token(browser),
            client=client,
            remember=remember,
            expires_at=now + REQUEST_TTL_S,
        )
        self._entries[hash_token(state)] = entry
        return IssuedRequest(state=state, browser_token=browser, request=entry)

    def take(self, state: str, browser_token: str | None, client: str) -> AuthRequest | None:
        """The request of ``state``, removed whatever the outcome; None when it is
        unknown, expired, or comes from another browser or client."""
        if not state or len(state) > 128:
            return None
        entry = self._entries.pop(hash_token(state), None)
        if entry is None or entry.expires_at <= self._clock():
            return None
        if not browser_token or not hmac.compare_digest(
            entry.browser_hash, hash_token(browser_token)
        ):
            return None
        if not hmac.compare_digest(entry.client, client):
            return None
        return entry

    def __len__(self) -> int:
        return len(self._entries)


auth_requests = AuthRequests()


def reset_auth_requests() -> None:
    global auth_requests
    auth_requests = AuthRequests()


class FailureAudit:
    """Which refused callbacks get a ``login`` failure row. The callback is a public
    GET that any page can make a browser load, so its failures are not all written:
    in each ``FAILURE_AUDIT_WINDOW_S`` window, at most one row per client address
    (``ratelimit.LoginLimiter.address_key``; one shared key when addresses do not
    identify clients) and ``FAILURE_AUDIT_MAX_PER_WINDOW`` rows in all. Memory is
    bounded by that cap. The sign-in itself is answered the same either way."""

    def __init__(self, clock_fn: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock_fn
        self._window_start = clock_fn()
        self._keys: set[str] = set()
        self._suppressed = 0

    def allow(self, key: str) -> bool:
        now = self._clock()
        if now - self._window_start >= FAILURE_AUDIT_WINDOW_S:
            self._window_start, self._suppressed = now, 0
            self._keys.clear()
        if key in self._keys or len(self._keys) >= FAILURE_AUDIT_MAX_PER_WINDOW:
            if self._suppressed == 0:
                logger.warning(
                    "OpenID Connect callback failures are arriving faster than they are "
                    "audited: further ones are not written to the audit log for up to %d s",
                    int(FAILURE_AUDIT_WINDOW_S),
                )
            self._suppressed += 1
            return False
        self._keys.add(key)
        return True


failure_audit = FailureAudit()


def reset_failure_audit() -> None:
    global failure_audit
    failure_audit = FailureAudit()


def code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def authorization_url(config: OidcProvider, meta: Metadata, issued: IssuedRequest) -> str:
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "redirect_uri": issued.request.redirect_uri,
        "scope": normalize_scopes(config.scopes) or "openid",
        "state": issued.state,
        "nonce": issued.request.nonce,
        "code_challenge": code_challenge(issued.request.verifier),
        "code_challenge_method": "S256",
    }
    joiner = "&" if "?" in meta.authorization_endpoint else "?"
    return meta.authorization_endpoint + joiner + urlencode(params)


def state_cookie_name(secure: bool) -> str:
    return SECURE_STATE_COOKIE if secure else STATE_COOKIE


def state_cookie_header(value: str, *, secure: bool) -> str:
    parts = [
        f"{state_cookie_name(secure)}={value}",
        "HttpOnly",
        "SameSite=Lax",
        "Path=/",
        f"Max-Age={int(REQUEST_TTL_S)}",
    ]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def clear_state_cookie_header(*, secure: bool) -> str:
    parts = [f"{state_cookie_name(secure)}=", "HttpOnly", "SameSite=Lax", "Path=/", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def read_state_cookie(cookie_header: str | None, *, secure: bool) -> str | None:
    if not cookie_header:
        return None
    name = state_cookie_name(secure)
    for part in cookie_header.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name and value:
            return value[:128]
    return None


# --- Code exchange and ID token -------------------------------------------------------


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    email: str | None = None
    email_verified: bool = False
    preferred_username: str | None = None
    name: str | None = None


def _verified(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


async def _exchange(
    config: OidcProvider, meta: Metadata, request: AuthRequest, code: str, secret: str | None
) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": request.redirect_uri,
        "code_verifier": request.verifier,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if secret is not None and config.token_auth_method == "client_secret_basic":
        pair = f"{quote(config.client_id, safe='')}:{quote(secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(pair.encode()).decode("ascii")
    else:
        form["client_id"] = config.client_id
        if secret is not None:
            form["client_secret"] = secret
    response = await _send(
        "POST",
        meta.token_endpoint,
        config.allow_private_network,
        content=urlencode(form).encode("ascii"),
        headers=headers,
    )
    if response.status_code != 200:
        raise OidcError("auth_oidc_exchange_failed", reason=f"http_{response.status_code}")
    body = _json_object(response, "auth_oidc_exchange_failed")
    if not isinstance(body.get("id_token"), str):
        raise OidcError("auth_oidc_exchange_failed", reason="no_id_token")
    return body


def _decode(id_token: str, keys: KeySet) -> jwt.Token:
    return jwt.decode(id_token, keys, algorithms=list(ALLOWED_ALGORITHMS))


async def verify_id_token(
    config: OidcProvider, meta: Metadata, id_token: str, nonce: str
) -> dict[str, Any]:
    """The claims of a verified ID token (signature, issuer, audience, times, nonce)."""
    if len(id_token) > 16384:
        raise OidcError("auth_oidc_token_invalid")
    keys = await keyset(meta, config.allow_private_network)
    try:
        token = _decode(id_token, keys)
    except jose_errors.InvalidKeyIdError:
        keys = await keyset(meta, config.allow_private_network, refresh=True)
        try:
            token = _decode(id_token, keys)
        except (jose_errors.JoseError, ValueError, TypeError):
            raise OidcError("auth_oidc_token_invalid") from None
    except (jose_errors.JoseError, ValueError, TypeError):
        raise OidcError("auth_oidc_token_invalid") from None
    claims = token.claims
    registry = JWTClaimsRegistry(
        now=int(clock()),
        leeway=CLOCK_LEEWAY_S,
        iss={"essential": True, "value": meta.issuer},
        sub={"essential": True},
        aud={"essential": True, "value": config.client_id},
        exp={"essential": True},
        iat={"essential": True},
        nonce={"essential": True, "value": nonce},
    )
    try:
        registry.validate(claims)
    except (jose_errors.JoseError, ValueError, TypeError):
        raise OidcError("auth_oidc_token_invalid") from None
    audience = claims.get("aud")
    if isinstance(audience, list) and len(audience) > 1 and claims.get("azp") != config.client_id:
        raise OidcError("auth_oidc_token_invalid")
    if "azp" in claims and claims.get("azp") != config.client_id:
        raise OidcError("auth_oidc_token_invalid")
    if not valid_subject(claims.get("sub")):
        raise OidcError("auth_oidc_token_invalid")
    return claims


async def _userinfo(
    config: OidcProvider, meta: Metadata, access_token: str, subject: str
) -> dict[str, Any]:
    """The userinfo claims, only when they name the same subject (else nothing)."""
    if meta.userinfo_endpoint is None or not access_token:
        return {}
    try:
        response = await _send(
            "GET",
            meta.userinfo_endpoint,
            config.allow_private_network,
            headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
        )
    except OidcError:
        return {}
    if response.status_code != 200:
        return {}
    try:
        claims = _json_object(response, "auth_oidc_token_invalid")
    except OidcError:
        return {}
    return claims if claims.get("sub") == subject else {}


def _short(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", value).strip()
    return text[:limit] or None


async def complete(
    config: OidcProvider, request: AuthRequest, code: str, secret: str | None
) -> Identity:
    """Exchange the code and verify the ID token: the identity it proves."""
    if not code or len(code) > 4096:
        raise OidcError("auth_oidc_exchange_failed", reason="no_code")
    meta = await metadata(config)
    tokens = await _exchange(config, meta, request, code, secret)
    claims = await verify_id_token(config, meta, tokens["id_token"], request.nonce)
    subject = claims["sub"]
    email_claims: dict[str, Any] = claims
    if "email" not in claims:
        access = tokens.get("access_token")
        extra = await _userinfo(config, meta, access if isinstance(access, str) else "", subject)
        if "email" in extra:
            email_claims = extra
    return Identity(
        issuer=meta.issuer,
        subject=subject,
        email=normalize_email(email_claims.get("email")),
        email_verified=_verified(email_claims.get("email_verified")),
        preferred_username=_short(claims.get("preferred_username"), 64),
        name=_short(claims.get("name"), 128),
    )


# --- Local account ------------------------------------------------------------------


@dataclass(frozen=True)
class SignIn:
    user: User
    link: OidcLink
    # "subject", "email" (the link was matched on the e-mail, now bound) or "created".
    matched: str


def eligible(user: User | None) -> bool:
    """A person's enabled local account: never kiosk, Home Assistant or system."""
    return (
        user is not None and not user.disabled and user.provider == "local" and user.role != "kiosk"
    )


def _username_candidates(identity: Identity) -> list[str]:
    bases = []
    for value in (
        identity.preferred_username,
        identity.email.split("@")[0] if identity.email else None,
        identity.name,
    ):
        if value:
            bases.append(value[:56])
    bases.append("oidc-user")
    out: list[str] = []
    for base in bases:
        try:
            name = normalize_username(base)
        except InvalidUsernameError:
            continue
        out.append(name)
        out.extend(f"{name}-{n}" for n in range(2, 10))
    return out


async def _create_account(identity: Identity) -> SignIn:
    email = identity.email if identity.email_verified else None
    for candidate in _username_candidates(identity):
        try:
            user = await UserRepository.create(
                candidate, AUTO_CREATE_ROLE, display_name=identity.name or ""
            )
        except (UsernameTakenError, InvalidUsernameError):
            continue
        for link_email in (email, None) if email else (None,):
            try:
                link = await OidcLinkRepository.add(
                    user.id, identity.issuer, subject=identity.subject, email=link_email
                )
            except LinkExistsError:
                continue
            return SignIn(user=user, link=link, matched="created")
        # Another sign-in of the same subject won the race: keep its account.
        await UserRepository.delete(user.id)
        existing = await OidcLinkRepository.by_subject(identity.issuer, identity.subject)
        if existing is not None:
            found = await UserRepository.get(existing.user_id)
            if eligible(found):
                assert found is not None
                return SignIn(user=found, link=existing, matched="subject")
        break
    raise OidcError("auth_oidc_no_account")


async def resolve_account(config: OidcProvider, identity: Identity) -> SignIn:
    """The local account this identity signs in (``OidcError`` ``auth_oidc_no_account``)."""
    link = await OidcLinkRepository.by_subject(identity.issuer, identity.subject)
    matched = "subject"
    if link is None and identity.email and identity.email_verified:
        by_email = await OidcLinkRepository.by_email(identity.issuer, identity.email)
        if by_email is not None and by_email.subject is None:
            if await OidcLinkRepository.bind_subject(by_email.id, identity.subject):
                link, matched = by_email, "email"
            else:
                link = await OidcLinkRepository.by_subject(identity.issuer, identity.subject)
    if link is None:
        if config.auto_create:
            return await _create_account(identity)
        raise OidcError("auth_oidc_no_account")
    user = await UserRepository.get(link.user_id)
    if not eligible(user):
        raise OidcError("auth_oidc_no_account")
    assert user is not None
    return SignIn(user=user, link=link, matched=matched)
