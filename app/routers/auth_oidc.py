"""``/api/auth/oidc/*``: sign-in with an OpenID Connect provider (MSHL-427).

Included by ``app/routers/auth.py``. Rules in ``app/auth/oidc.py``; contract in
``docs/dev/auth.md``, "OpenID Connect".

- ``GET /auth/oidc/start?remember=&base=`` (public): a browser navigation. 303 to
  the provider with a state cookie, or back to the app with ``?oidc_error=<code>``.
  ``base`` is the app's own address (``new URL('./', location)``): it gives the
  redirect URI its path prefix behind a reverse proxy under a subpath, and is used
  only when it is this request's origin or a declared public address.
- ``GET /auth/oidc/callback`` (public): the provider's redirect. On success the
  session cookie and 303 back to the app; for an account with TOTP on, back with
  ``#oidc_2fa=<pending token>&methods=...&expires_in=...`` (a fragment: never sent
  to a server or logged), finished by ``POST /auth/login/2fa``; else
  ``?oidc_error=<code>``. Refused under ingress (no cookie there). While the provider
  is off or incomplete: ``auth_oidc_disabled``, no audit row. Other refusals write a
  ``login`` failure row, throttled per address (``oidc.FailureAudit``).
- Every way back is the relative ``../../../`` (from ``<base>api/auth/oidc/*`` to
  ``<base>``): it keeps the subpath without trusting any header.
- ``GET/PATCH /auth/oidc/settings``, ``POST /auth/oidc/test``, ``GET/POST
  /auth/oidc/links``, ``DELETE /auth/oidc/links/{id}``: admin only.

The client secret is write-only: never answered, never audited, never logged.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import quote, urlencode, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool

from app import secret_store
from app.auth import audit, oidc, ratelimit, totp
from app.auth.middleware import client_ip
from app.auth.permissions import public, require
from app.repository.auth import AuthSettingsRepository, UserRepository
from app.repository.oidc import (
    CLIENT_ID_MAX_LENGTH,
    CLIENT_SECRET_MAX_LENGTH,
    EMAIL_MAX_LENGTH,
    ISSUER_MAX_LENGTH,
    LABEL_MAX_LENGTH,
    MAX_LINKS,
    SCOPES_MAX_LENGTH,
    SUBJECT_MAX_LENGTH,
    LinkExistsError,
    LinkLimitError,
    OidcLink,
    OidcLinkRepository,
    OidcProvider,
    OidcProviderRepository,
    Preset,
    TokenAuthMethod,
)

router = APIRouter(prefix="/oidc")

_ADMIN = [Depends(require("admin"))]

_MESSAGES = {
    "auth_oidc_disabled": "Sign-in with OpenID Connect is off",
    "auth_oidc_incomplete": "Set the issuer and the client id first",
    "auth_oidc_issuer_invalid": "The issuer must be an https address (http only on the local network)",
    "auth_oidc_issuer_mismatch": "The provider announces another issuer",
    "auth_oidc_metadata_invalid": "The provider's configuration is not usable",
    "auth_oidc_unavailable": "The provider could not be reached",
    "auth_oidc_http_public": "Plain http is accepted only for a provider on the local network",
    "auth_oidc_scopes_invalid": "Invalid scopes",
    "auth_oidc_secret_store_unavailable": "The secrets key is not available: the client secret cannot be stored",
    "auth_oidc_link_invalid": "Give either a subject or an e-mail address",
    "auth_oidc_link_user_invalid": "Only an enabled local account (not a kiosk) can be linked",
    "auth_oidc_link_exists": "This identity is already linked",
    "auth_oidc_link_limit": "Too many linked identities",
    "auth_oidc_link_not_found": "Link not found",
}


def _error(status: int, code: str, params: dict | None = None) -> HTTPException:
    detail: dict = {"code": code, "message": _MESSAGES.get(code, code)}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail)


def _secure(request: Request) -> bool:
    return bool(request.scope.get("auth_secure", False))


def _via(request: Request) -> str:
    return request.scope.get("auth_via", "lan")


# From ``<base>api/auth/oidc/<route>`` back to ``<base>``, the app.
_APP_HOME = "../../../"
_BASE_MAX_LENGTH = 512
_BASE_PATH = re.compile(r"/(?:[A-Za-z0-9._~%-]+/)*")


def _own_origin(request: Request) -> str:
    host = request.headers.get("host") or request.url.netloc
    return f"{request.url.scheme}://{host.lower()}"


async def _declared_bases() -> list[str]:
    """The declared public addresses, each as an app base (trailing slash)."""
    try:
        settings = await AuthSettingsRepository.get()
    except Exception:  # unreadable settings: no declared address
        return []
    return [url.rstrip("/") + "/" for url in settings.public_urls]


async def app_base(request: Request, base: str | None) -> str:
    """The app's address the redirect URI hangs from: ``base`` when it is this
    request's origin or a declared public address (with its path prefix), else this
    request's origin at ``/``."""
    own = _own_origin(request)
    if base:
        try:
            parts = urlsplit(base)
            parts.port  # noqa: B018 (raises on a bad port)
        except ValueError:
            parts = None
        if (
            parts is not None
            and parts.scheme in ("http", "https")
            and parts.netloc
            and parts.username is None
            and parts.password is None
            and not parts.query
            and not parts.fragment
            and "\\" not in base
            and _BASE_PATH.fullmatch(parts.path)
            and ".." not in parts.path.split("/")
        ):
            candidate = f"{parts.scheme}://{parts.netloc.lower()}{parts.path}"
            if candidate.startswith(own + "/"):
                return candidate
            for declared in await _declared_bases():
                if candidate.startswith(declared):
                    return candidate
    return own + "/"


def redirect_uri(app: str) -> str:
    return app + oidc.CALLBACK_PATH.lstrip("/")


def _client(request: Request) -> str:
    return totp.client_binding(client_ip(request.scope), request.headers.get("user-agent"))


def _back(error: str | None = None, fragment: str | None = None) -> RedirectResponse:
    target = _APP_HOME
    if error:
        target += "?" + urlencode({"oidc_error": error})
    if fragment:
        target += "#" + fragment
    response = RedirectResponse(target, status_code=303)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _clear_state(response: RedirectResponse, request: Request) -> RedirectResponse:
    response.headers.append("set-cookie", oidc.clear_state_cookie_header(secure=_secure(request)))
    return response


async def _audit_failure(request: Request, reason: str, target: str | None = None) -> None:
    """A refused callback: at most one row per address per window (``oidc.FailureAudit``),
    since any page can make a browser load the callback."""
    address = ratelimit.login_limiter.address_key(request.scope)
    if not oidc.failure_audit.allow(address or "*"):
        return
    await audit.record_request(
        request,
        "login",
        "failure",
        target=target,
        details={"method": "oidc", "reason": reason},
    )


# --- Sign-in ------------------------------------------------------------------


@router.get("/start", dependencies=[Depends(public())], response_class=RedirectResponse)
async def start(
    request: Request,
    remember: bool = Query(default=False),
    base: str = Query(default="", max_length=_BASE_MAX_LENGTH),
) -> RedirectResponse:
    """Redirect the browser to the provider (a navigation, not a fetch)."""
    if _via(request) == "ingress":
        return _back("auth_ingress_only")
    config = await OidcProviderRepository.get()
    if not config.ready:
        return _back("auth_oidc_disabled")
    try:
        meta = await oidc.metadata(config)
    except oidc.OidcError as exc:
        return _back(exc.code)
    issued = oidc.auth_requests.issue(
        issuer=config.issuer,
        redirect_uri=redirect_uri(await app_base(request, base)),
        client=_client(request),
        remember=remember,
    )
    response = RedirectResponse(oidc.authorization_url(config, meta, issued), status_code=303)
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers.append(
        "set-cookie", oidc.state_cookie_header(issued.browser_token, secure=_secure(request))
    )
    return response


@router.get("/callback", dependencies=[Depends(public())], response_class=RedirectResponse)
async def callback(
    request: Request,
    state: str = Query(default="", max_length=128),
    code: str = Query(default="", max_length=4096),
    error: str = Query(default="", max_length=256),
) -> RedirectResponse:
    """The provider's redirect: verify, find the local account, open the session."""
    from app.routers import auth as auth_routes

    if _via(request) == "ingress":
        return _back("auth_ingress_only")
    config = await OidcProviderRepository.get()
    if not config.ready:
        # Off or incomplete: nothing was attempted, nothing is audited.
        return _clear_state(_back("auth_oidc_disabled"), request)
    secure = _secure(request)
    browser = oidc.read_state_cookie(request.headers.get("cookie"), secure=secure)
    pending = oidc.auth_requests.take(state, browser, _client(request))
    if pending is None or pending.issuer != config.issuer:
        await _audit_failure(request, "state_invalid")
        return _clear_state(_back("auth_oidc_state_invalid"), request)
    if error:
        await _audit_failure(request, "denied")
        return _clear_state(_back("auth_oidc_denied"), request)
    try:
        secret = await OidcProviderRepository.client_secret()
    except secret_store.SecretUnreadable:
        await _audit_failure(request, "secret_unreadable")
        return _clear_state(_back("auth_oidc_secret_unreadable"), request)
    try:
        identity = await oidc.complete(config, pending, code, secret)
        signed = await oidc.resolve_account(config, identity)
    except oidc.OidcError as exc:
        await _audit_failure(request, exc.code.removeprefix("auth_oidc_"))
        return _clear_state(_back(exc.code), request)
    user = signed.user
    target = audit.user_target(user.id)
    if signed.matched == "created":
        await audit.record_request(
            request,
            "user.created",
            "success",
            target=target,
            details={"role": user.role, "provider": "oidc"},
        )
    await OidcLinkRepository.touch(signed.link.id)
    if user.totp_enabled_at is not None:
        payload = await auth_routes._second_factor_challenge(request, user, pending.remember)
        challenge = payload.second_factor
        assert challenge is not None
        fragment = urlencode(
            {
                "oidc_2fa": challenge.pending_token,
                "methods": ",".join(challenge.methods),
                "expires_in": str(challenge.expires_in),
            },
            quote_via=quote,
        )
        return _clear_state(_back(fragment=fragment), request)
    response = _back()
    principal = await auth_routes._start_session(request, response, user, remember=pending.remember)
    await audit.record_request(
        request,
        "login",
        "success",
        principal=principal,
        target=target,
        details={"method": "oidc", "matched": signed.matched},
    )
    return _clear_state(response, request)


# --- Settings (admin) -------------------------------------------------------------


class OidcSettingsView(OidcProvider):
    # The redirect URIs to register at the provider: the app's own address first
    # (``base``, checked by ``app_base``), then each declared public address.
    redirect_uris: list[str] = Field(default_factory=list)
    # A client secret can be stored (``app/secret_store.py`` has its key).
    secret_store_available: bool = True


class OidcSettingsPatch(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    enabled: StrictBool | None = None
    preset: Preset | None = None
    label: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    issuer: str | None = Field(default=None, max_length=ISSUER_MAX_LENGTH)
    client_id: str | None = Field(default=None, max_length=CLIENT_ID_MAX_LENGTH)
    # Write-only. Absent or null keeps the stored one; ``clear_client_secret`` removes it.
    client_secret: SecretStr | None = Field(default=None, max_length=CLIENT_SECRET_MAX_LENGTH)
    clear_client_secret: StrictBool = False
    scopes: str | None = Field(default=None, max_length=SCOPES_MAX_LENGTH)
    token_auth_method: TokenAuthMethod | None = None
    auto_create: StrictBool | None = None
    allow_private_network: StrictBool | None = None


async def _settings_view(
    config: OidcProvider, request: Request, base: str | None = None
) -> OidcSettingsView:
    uris = [redirect_uri(await app_base(request, base))]
    for declared in await _declared_bases():
        candidate = redirect_uri(declared)
        if candidate not in uris:
            uris.append(candidate)
    return OidcSettingsView(
        **config.model_dump(),
        redirect_uris=uris,
        secret_store_available=secret_store.available(),
    )


@router.get("/settings", response_model=OidcSettingsView, dependencies=_ADMIN)
async def get_settings(
    request: Request, base: str = Query(default="", max_length=_BASE_MAX_LENGTH)
) -> OidcSettingsView:
    return await _settings_view(await OidcProviderRepository.get(), request, base)


@router.patch("/settings", response_model=OidcSettingsView, dependencies=_ADMIN)
async def patch_settings(
    body: OidcSettingsPatch,
    request: Request,
    base: str = Query(default="", max_length=_BASE_MAX_LENGTH),
) -> OidcSettingsView:
    current = await OidcProviderRepository.get()
    fields: dict[str, object] = {}
    for name in (
        "enabled",
        "preset",
        "token_auth_method",
        "auto_create",
        "allow_private_network",
    ):
        value = getattr(body, name)
        if value is not None and value != getattr(current, name):
            fields[name] = value
    for name in ("label", "issuer", "client_id"):
        value = getattr(body, name)
        if value is not None and value.strip() != getattr(current, name):
            fields[name] = value.strip()
    if body.scopes is not None:
        scopes = oidc.normalize_scopes(body.scopes)
        if scopes is None:
            raise _error(400, "auth_oidc_scopes_invalid")
        if scopes != current.scopes:
            fields["scopes"] = scopes
    merged = current.model_copy(update=fields)
    if merged.issuer and not oidc.valid_issuer(merged.issuer, merged.allow_private_network):
        raise _error(400, "auth_oidc_issuer_invalid")
    if merged.enabled and (not merged.issuer or not merged.client_id):
        raise _error(400, "auth_oidc_incomplete")
    secret = body.client_secret.get_secret_value() if body.client_secret is not None else None
    if secret == "":
        secret = None
    changed = sorted(fields)
    if secret is not None or body.clear_client_secret and current.client_secret_set:
        changed.append("client_secret")
    if not changed:
        return await _settings_view(current, request, base)
    try:
        updated = await OidcProviderRepository.update(
            fields, client_secret=secret, clear_secret=body.clear_client_secret
        )
    except secret_store.SecretStoreUnavailable:
        raise _error(503, "auth_oidc_secret_store_unavailable") from None
    oidc.reset_caches()
    await audit.record_request(
        request,
        "settings.changed",
        "success",
        target="oidc_provider",
        details={"fields": changed},
    )
    return await _settings_view(updated, request, base)


class OidcTestResult(BaseModel):
    ok: bool = True
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str | None = None
    keys: int


@router.post("/test", response_model=OidcTestResult, dependencies=_ADMIN)
async def test_provider() -> OidcTestResult:
    """Fetch the saved provider's discovery document and keys, fresh."""
    config = await OidcProviderRepository.get()
    if not config.issuer:
        raise _error(400, "auth_oidc_incomplete")
    try:
        meta = await oidc.metadata(config, refresh=True)
        keys = await oidc.keyset(meta, config.allow_private_network, refresh=True)
    except oidc.OidcError as exc:
        raise _error(400, exc.code, dict(exc.params) or None) from None
    return OidcTestResult(
        issuer=meta.issuer,
        authorization_endpoint=meta.authorization_endpoint,
        token_endpoint=meta.token_endpoint,
        userinfo_endpoint=meta.userinfo_endpoint,
        keys=len(keys.as_dict()["keys"]),
    )


# --- Links (admin) ----------------------------------------------------------------


class OidcLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    subject: str | None = Field(default=None, max_length=SUBJECT_MAX_LENGTH)
    email: str | None = Field(default=None, max_length=EMAIL_MAX_LENGTH)


class OidcLinksView(BaseModel):
    issuer: str
    links: list[OidcLink]


@router.get("/links", response_model=OidcLinksView, dependencies=_ADMIN)
async def list_links() -> OidcLinksView:
    """Every link; ``issuer`` is the configured one (links of another issuer match nothing)."""
    config = await OidcProviderRepository.get()
    return OidcLinksView(issuer=config.issuer, links=await OidcLinkRepository.list_all())


@router.post("/links", status_code=201, response_model=OidcLink, dependencies=_ADMIN)
async def add_link(body: OidcLinkIn, request: Request) -> JSONResponse:
    """Let one external identity sign in an existing local account."""
    config = await OidcProviderRepository.get()
    if not config.issuer:
        raise _error(400, "auth_oidc_incomplete")
    subject = body.subject.strip() if body.subject else None
    email = oidc.normalize_email(body.email) if body.email else None
    kind: Literal["subject", "email"]
    if subject and not body.email and oidc.valid_subject(subject):
        kind = "subject"
    elif email and not body.subject:
        kind = "email"
    else:
        raise _error(400, "auth_oidc_link_invalid")
    target = audit.user_target(body.user_id)
    if not oidc.eligible(await UserRepository.get(body.user_id)):
        await audit.record_request(
            request,
            "oidc.linked",
            "refused",
            target=target,
            details={"reason": "link_user_invalid"},
        )
        raise _error(400, "auth_oidc_link_user_invalid")
    try:
        link = await OidcLinkRepository.add(
            body.user_id,
            config.issuer,
            subject=subject if kind == "subject" else None,
            email=email if kind == "email" else None,
        )
    except LinkExistsError:
        raise _error(409, "auth_oidc_link_exists") from None
    except LinkLimitError:
        raise _error(409, "auth_oidc_link_limit", {"max": MAX_LINKS}) from None
    await audit.record_request(
        request, "oidc.linked", "success", target=target, details={"kind": kind}
    )
    return JSONResponse(status_code=201, content=link.model_dump())


@router.delete("/links/{link_id}", status_code=204, dependencies=_ADMIN)
async def delete_link(link_id: int, request: Request) -> None:
    link = await OidcLinkRepository.get(link_id)
    if link is None or not await OidcLinkRepository.delete(link_id):
        raise _error(404, "auth_oidc_link_not_found")
    await audit.record_request(
        request, "oidc.unlinked", "success", target=audit.user_target(link.user_id)
    )
