"""The e-mail code and sign-in links (MSHL-426): ``/api/auth/email*``,
``POST /api/auth/login/2fa/email`` and ``/api/auth/magic-link*``.

- ``GET/PATCH /auth/email/settings`` (admin): the notification e-mail channel whose
  SMTP settings send the sign-in mails (Settings → Connection → E-mail sign-in).
- ``/auth/email`` (own account): ``GET`` the state; ``POST /auth/email/address``
  sends a code to a new address (current password, and a TOTP or recovery code when
  TOTP is on); ``POST /auth/email/verify`` stores it once the code checks; ``PATCH``
  turns the e-mail code and the sign-in links on or off (current password; turning
  one on also takes the TOTP code when TOTP is on); ``DELETE`` forgets the address.
- ``POST /auth/login/2fa/email`` (public): mails a code for a pending login of the
  password step; ``POST /auth/login/2fa`` with ``method: "email"`` checks it
  (``app/routers/auth_totp.py``).
- ``POST /auth/magic-link`` (public): mails a sign-in link. The answer is the same
  whatever the name, and everything that reads the account (lookups, per-account
  limit, token, audit row, mail) runs in a background task (``_send_link``), so the
  request path does the same work whether an account matched or not. ``POST /auth/magic-link/verify``
  (public) trades the link's token for a session, or for the TOTP step when TOTP is
  on (never the e-mail code: the link already proved the mailbox). The mailed link
  hangs only from a trusted origin (``_mail_base``): a declared public address, or
  this request's own origin when its ``Host`` names this machine. A client-supplied
  ``Host`` or ``base`` never chooses where the link points.

Every route but the settings answers 503 ``auth_email_unavailable`` without a usable
SMTP profile. Rules: ``app/auth/email_factor.py``, ``docs/dev/auth.md``, "E-mail
code and sign-in links". Nothing here logs a code, a token, a link or a password.
"""

from __future__ import annotations

import hmac
import ipaddress
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.auth import audit, email_factor, ratelimit, sessions, totp
from app.auth.email_factor import EmailSendFailed, EmailUnavailable
from app.auth.middleware import client_ip
from app.auth.models import User
from app.auth.passwords import MAX_PASSWORD_LENGTH
from app.auth.permissions import own_account, public, require
from app.background_tasks import spawn
from app.middleware import host_guard
from app.repository.auth import UserRepository
from app.repository.email_factor import (
    ADDRESS_MAX_LENGTH,
    CHANNEL_ID_MAX_LENGTH,
    EmailFactorRepository,
    EmailSignInRepository,
)
from app.routers.auth import (
    AuthSessionResponse,
    OkResponse,
    _check_current_password,
    _email_code_on,
    _error,
    _instance_name,
    _refuse_under_ingress,
    _require,
    _second_factor_challenge,
    _session_payload,
    _start_session,
    _via,
)
from app.routers.auth_oidc import _declared_bases, app_base
from app.routers.auth_totp import _MAX_CODE_LENGTH, _applicable, _check_code, _rate_limited

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

# The login limiter key of the sign-in link redemption: per-address rules only.
LINK_LIMIT_KEY = "\x00magic_link"

MailLanguage = Literal["fr", "en"]


# --- Helpers ----------------------------------------------------------------------


def _host_is_this_machine(request: Request) -> bool:
    """Whether the request's ``Host`` names this machine, whoever sent it: a loopback
    address, the address this connection reached (``scope["server"]``), ``localhost``,
    the machine's own name or ``<its name>.local``, or the Remote hostname. Unlike
    ``host_guard.host_allowed``, any other IP literal or local-looking name is refused:
    a client could name its own box there."""
    host = host_guard.request_host(request.headers.get("host") or "")
    if not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if ip.is_loopback:
            return True
        server = request.scope.get("server")
        try:
            reached = ipaddress.ip_address(str(server[0])) if server else None
        except (ValueError, TypeError, IndexError):
            reached = None
        if isinstance(reached, ipaddress.IPv6Address) and reached.ipv4_mapped:
            reached = reached.ipv4_mapped
        return reached is not None and reached == ip
    if host == "localhost" or host in host_guard.remote_hostnames():
        return True
    names = host_guard.machine_names()
    return host in names or (host.endswith(".local") and host[: -len(".local")] in names)


async def _mail_base(request: Request, base: str | None) -> str | None:
    """The app address a mailed sign-in link hangs from, or None when there is no
    trusted one. Unlike OpenID Connect (where the provider checks the registered
    redirect URIs), nothing downstream checks a mailed link: a forged ``Host`` would
    send the victim's token to the attacker's page. So: ``app_base`` when it falls
    under a declared public address, or when the request's own host is this machine;
    else the first declared public address; else None."""
    candidate = await app_base(request, base)
    declared = await _declared_bases()
    if any(candidate.startswith(entry) for entry in declared):
        return candidate
    if _host_is_this_machine(request):
        return candidate
    return declared[0] if declared else None


async def magic_link_origin_trusted(request: Request) -> bool:
    """Whether a sign-in link may be mailed from this request (``_mail_base`` has an
    address for it). ``GET /auth/session`` offers ``methods.magic_link`` only then, so
    the login page hides the button instead of meeting 400
    ``auth_magic_link_origin_untrusted``."""
    return await _mail_base(request, None) is not None


def _unavailable() -> Exception:
    return _error(503, EmailUnavailable.code)


def _send_failed(exc: EmailSendFailed) -> Exception:
    return _error(502, EmailSendFailed.code, {"reason": exc.reason})


def _too_soon(retry_after: int) -> Exception:
    return _error(
        429,
        "auth_rate_limited",
        {"retry_after": retry_after},
        headers={"Retry-After": str(retry_after)},
    )


async def _require_available() -> None:
    if not await email_factor.available():
        raise _unavailable()


def _client(request: Request) -> str:
    return totp.client_binding(client_ip(request.scope), request.headers.get("user-agent"))


async def _proof(request: Request, user: User, password: SecretStr, code: SecretStr | None):
    """The current password and, when TOTP is on, a TOTP or recovery code: adding an
    e-mail way in must not get around TOTP (the passkey rule, ``docs/dev/auth.md``)."""
    totp_on = user.totp_enabled_at is not None
    await _check_current_password(
        request, user, password, "email_factor.changed", record_success=not totp_on
    )
    if totp_on:
        if code is None:
            await audit.record_request(
                request,
                "email_factor.changed",
                "failure",
                target=audit.user_target(user.id),
                details={"reason": "second_factor_required"},
            )
            raise _error(403, "auth_2fa_required")
        await _check_code(request, user, code, "email_factor.changed", allow_recovery=True)


# --- Settings (admin) -------------------------------------------------------------


class EmailChannelOption(BaseModel):
    id: str
    name: str
    enabled: bool
    usable: bool


class EmailSignInSettings(BaseModel):
    channel_id: str | None = None
    # The chosen channel can send now (enabled, a native e-mail channel, readable secret).
    available: bool = False
    channels: list[EmailChannelOption] = Field(default_factory=list)


class EmailSignInSettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str | None = Field(default=None, max_length=CHANNEL_ID_MAX_LENGTH)


async def _settings_view() -> EmailSignInSettings:
    from app.repository.notification_channels import NotificationChannelRepository

    channels = [
        EmailChannelOption(
            id=channel.id,
            name=channel.name,
            enabled=channel.enabled,
            usable=channel.enabled and bool(channel.config.get("host")),
        )
        for channel in await NotificationChannelRepository.list_all()
        if email_factor.usable_channel(channel)
    ]
    return EmailSignInSettings(
        channel_id=await EmailSignInRepository.channel_id(),
        available=await email_factor.available(),
        channels=channels,
    )


@router.get(
    "/email/settings", response_model=EmailSignInSettings, dependencies=[Depends(require("admin"))]
)
async def get_email_settings() -> EmailSignInSettings:
    """Which notification e-mail channel sends the sign-in mails, and the candidates."""
    return await _settings_view()


@router.patch(
    "/email/settings", response_model=EmailSignInSettings, dependencies=[Depends(require("admin"))]
)
async def patch_email_settings(
    body: EmailSignInSettingsPatch, request: Request
) -> EmailSignInSettings:
    """Pick the channel (null: none, both features off). 400
    ``auth_email_channel_invalid`` for anything but a native e-mail channel."""
    from app.repository.notification_channels import NotificationChannelRepository

    channel_id = body.channel_id or None
    if channel_id is not None:
        channel = await NotificationChannelRepository.get(channel_id)
        if channel is None or not email_factor.usable_channel(channel):
            raise _error(400, "auth_email_channel_invalid")
    await EmailSignInRepository.set_channel_id(channel_id)
    await audit.record_request(
        request,
        "settings.changed",
        "success",
        target="email_sign_in",
        details={"channel_id": channel_id},
    )
    return await _settings_view()


# --- The account's own address and opt-ins ------------------------------------------


class EmailFactorStatus(BaseModel):
    # A local account with a password, outside ``kiosk``.
    applicable: bool
    # An SMTP profile is set: codes and links can be sent.
    available: bool
    address: str | None = None
    otp_enabled: bool = False
    magic_link_enabled: bool = False
    # The address a code was sent to, waiting for ``POST /auth/email/verify``.
    pending_address: str | None = None


class EmailAddressRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)
    address: str = Field(min_length=3, max_length=ADDRESS_MAX_LENGTH)
    # A TOTP or recovery code, required when TOTP is on.
    code: SecretStr | None = Field(default=None, min_length=1, max_length=_MAX_CODE_LENGTH)
    lang: MailLanguage = "en"


class EmailVerifyRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    code: SecretStr = Field(min_length=1, max_length=_MAX_CODE_LENGTH)


class EmailFactorPatch(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")

    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)
    code: SecretStr | None = Field(default=None, min_length=1, max_length=_MAX_CODE_LENGTH)
    otp_enabled: bool | None = None
    magic_link_enabled: bool | None = None


class PasswordOnly(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    password: SecretStr = Field(max_length=MAX_PASSWORD_LENGTH)


class CodeSent(BaseModel):
    sent: bool = True
    expires_in: int
    # Seconds before another mail may be asked for.
    resend_in: int
    email_hint: str


def _is_applicable(user: User) -> bool:
    return user.provider == "local" and user.has_password and user.role != "kiosk"


async def _status(user: User) -> EmailFactorStatus:
    factor = await EmailFactorRepository.get(user.id)
    return EmailFactorStatus(
        applicable=_is_applicable(user),
        available=await email_factor.available(),
        address=factor.address if factor else None,
        otp_enabled=bool(factor and factor.otp_enabled),
        magic_link_enabled=bool(factor and factor.magic_link_enabled),
        pending_address=email_factor.address_challenges.pending(user.id),
    )


@router.get("/email", response_model=EmailFactorStatus, dependencies=[Depends(own_account())])
async def get_email_factor(request: Request) -> EmailFactorStatus:
    """The caller's sign-in address and opt-ins."""
    return await _status(_require(request).user)


@router.post("/email/address", response_model=CodeSent, dependencies=[Depends(own_account())])
async def start_email_address(body: EmailAddressRequest, request: Request) -> CodeSent:
    """Send a code to a new address; ``POST /auth/email/verify`` stores it."""
    user = _require(request).user
    _applicable(user)
    address = email_factor.normalize_address(body.address)
    if address is None:
        raise _error(400, "auth_email_address_invalid")
    await _require_available()
    await _proof(request, user, body.password, body.code)
    retry_after = email_factor.account_sends.retry_after(("user", user.id))
    if retry_after or not email_factor.account_sends.allow(("user", user.id)):
        raise _too_soon(retry_after or int(email_factor.SEND_INTERVAL_S))
    code = email_factor.new_code()
    subject, html = email_factor.render("verify", body.lang, instance=_instance_name(), code=code)
    try:
        await email_factor.deliver(address, subject, html)
    except EmailUnavailable:
        raise _unavailable() from None
    except EmailSendFailed as exc:
        raise _send_failed(exc) from None
    email_factor.address_challenges.issue(user.id, address, code)
    return CodeSent(
        expires_in=int(email_factor.CODE_TTL_S),
        resend_in=int(email_factor.SEND_INTERVAL_S),
        email_hint=email_factor.mask_address(address),
    )


@router.post(
    "/email/verify", response_model=EmailFactorStatus, dependencies=[Depends(own_account())]
)
async def verify_email_address(body: EmailVerifyRequest, request: Request) -> EmailFactorStatus:
    """The code sent to the new address stores it (five tries, 10 minutes)."""
    user = _require(request).user
    _applicable(user)
    target = audit.user_target(user.id)
    if email_factor.address_challenges.pending(user.id) is None:
        raise _error(409, "auth_email_not_pending")
    address = email_factor.address_challenges.check(user.id, body.code.get_secret_value())
    if address is None:
        await audit.record_request(
            request,
            "email_factor.verified",
            "failure",
            target=target,
            details={"reason": "second_factor_invalid"},
        )
        raise _error(400, "auth_2fa_invalid")
    await EmailFactorRepository.set_address(user.id, address)
    await audit.record_request(request, "email_factor.verified", "success", target=target)
    current = await UserRepository.get(user.id)
    return await _status(current or user)


@router.patch("/email", response_model=EmailFactorStatus, dependencies=[Depends(own_account())])
async def patch_email_factor(body: EmailFactorPatch, request: Request) -> EmailFactorStatus:
    """Turn the e-mail code and the sign-in links on or off (current password; turning
    one on needs an SMTP profile and, with TOTP on, a TOTP or recovery code). Turning
    the e-mail code on signs the account out everywhere else."""
    principal = _require(request)
    user = principal.user
    _applicable(user)
    factor = await EmailFactorRepository.get(user.id)
    if factor is None:
        raise _error(409, "auth_email_not_verified")
    turning_on = (body.otp_enabled and not factor.otp_enabled) or (
        body.magic_link_enabled and not factor.magic_link_enabled
    )
    if turning_on:
        await _require_available()
        await _proof(request, user, body.password, body.code)
    else:
        await _check_current_password(request, user, body.password, "email_factor.changed")
    updated = await EmailFactorRepository.set_flags(
        user.id, otp_enabled=body.otp_enabled, magic_link_enabled=body.magic_link_enabled
    )
    if updated is None:
        raise _error(409, "auth_email_not_verified")
    details: dict[str, object] = {
        "second_factor": updated.otp_enabled,
        "sign_in_link": updated.magic_link_enabled,
    }
    if body.otp_enabled and not factor.otp_enabled:
        details["sessions_revoked"] = await sessions.sign_out_user(
            user.id, except_session_id=principal.session_id
        )
    await audit.record_request(
        request,
        "email_factor.changed",
        "success",
        target=audit.user_target(user.id),
        details=details,
    )
    current = await UserRepository.get(user.id)
    return await _status(current or user)


@router.delete("/email", response_model=OkResponse, dependencies=[Depends(own_account())])
async def delete_email_factor(body: PasswordOnly, request: Request) -> OkResponse:
    """Forget the address (both opt-ins go with it): the current password."""
    user = _require(request).user
    _applicable(user)
    await _check_current_password(request, user, body.password, "email_factor.removed")
    email_factor.address_challenges.discard(user.id)
    if not await EmailFactorRepository.delete(user.id):
        raise _error(409, "auth_email_not_verified")
    await audit.record_request(
        request, "email_factor.removed", "success", target=audit.user_target(user.id)
    )
    return OkResponse()


# --- The e-mail code of a pending login ---------------------------------------------


class PendingEmailRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    pending_token: SecretStr = Field(min_length=1, max_length=128)
    lang: MailLanguage = "en"


@router.post("/login/2fa/email", response_model=CodeSent, dependencies=[Depends(public())])
async def send_login_code(body: PendingEmailRequest, request: Request) -> CodeSent:
    """Mail a code for a pending login of the password step. Public. 401
    ``auth_2fa_pending_invalid`` for an unknown, expired or foreign token or an
    account whose e-mail code is off; 429 ``auth_rate_limited`` (one mail a minute,
    five an hour per account) or ``auth_email_send_limit`` (three per pending login);
    503 ``auth_email_unavailable``; 502 ``auth_email_send_failed``."""
    _refuse_under_ingress(request)
    entry = totp.pending_logins.get(body.pending_token.get_secret_value())
    user = await UserRepository.get(entry.user_id) if entry is not None else None
    if (
        entry is None
        or user is None
        or user.disabled
        or user.updated_at != entry.generation
        or not entry.email
        or not hmac.compare_digest(_client(request), entry.client)
        or not await _email_code_on(user)
    ):
        raise _error(401, "auth_2fa_pending_invalid")
    factor = await EmailFactorRepository.get(user.id)
    if factor is None:
        raise _error(401, "auth_2fa_pending_invalid")
    await _require_available()
    if entry.email_sends >= email_factor.PENDING_MAX_SENDS:
        raise _error(429, "auth_email_send_limit")
    retry_after = email_factor.account_sends.retry_after(("user", user.id))
    if retry_after or not email_factor.account_sends.allow(("user", user.id)):
        raise _too_soon(retry_after or int(email_factor.SEND_INTERVAL_S))
    code = email_factor.new_code()
    subject, html = email_factor.render("code", body.lang, instance=_instance_name(), code=code)
    entry.email_sends += 1
    try:
        await email_factor.deliver(factor.address, subject, html)
    except EmailUnavailable:
        raise _unavailable() from None
    except EmailSendFailed as exc:
        raise _send_failed(exc) from None
    # A new code replaces the previous one; the pending login lives as long as it.
    entry.email_code = email_factor.CodeChallenge.issue(code)
    left = totp.pending_logins.extend(entry, email_factor.CODE_TTL_S)
    await audit.record_request(
        request,
        "login",
        "pending",
        target=audit.user_target(user.id),
        details={"factor": "email", "sent": entry.email_sends},
    )
    return CodeSent(
        expires_in=left,
        resend_in=int(email_factor.SEND_INTERVAL_S),
        email_hint=email_factor.mask_address(factor.address),
    )


# --- Sign-in links ------------------------------------------------------------------


class MagicLinkRequest(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    username: str = Field(min_length=1, max_length=256)
    remember: bool = False
    # The app's address (``new URL('./', location)``), checked like OpenID Connect's.
    base: str | None = Field(default=None, max_length=512)
    lang: MailLanguage = "en"


class MagicLinkSent(BaseModel):
    """The same answer whatever the name: no account is revealed."""

    sent: bool = True
    expires_in: int = int(email_factor.LINK_TTL_S)


class MagicLinkVerify(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    token: SecretStr = Field(min_length=1, max_length=128)


async def _send_link(
    username: str,
    remember: bool,
    lang: MailLanguage,
    app_address: str,
    actor: audit.AuditActor,
) -> None:
    """Everything of ``POST /auth/magic-link`` that depends on the account, off the
    request path: the lookups, the per-account limit, the token, the audit row and
    the mail. The answer then does the same work whatever the name (the audit insert
    and its commit, or a lookup that stops early, would otherwise time it)."""
    user = await UserRepository.get_by_username(username)
    factor = await EmailFactorRepository.get(user.id) if user is not None else None
    if (
        user is None
        or user.disabled
        or not _is_applicable(user)
        or factor is None
        or not factor.magic_link_enabled
    ):
        return
    # Per account, silently: a refusal here would tell the name exists.
    if not email_factor.account_sends.allow(("user", user.id)):
        return
    token = email_factor.magic_links.issue(user.id, user.updated_at, remember)
    link = f"{app_address}#{email_factor.LINK_FRAGMENT}={token}"
    await audit.record(
        "login",
        "pending",
        actor=actor,
        target=audit.user_target(user.id),
        details={"method": "magic_link"},
    )
    subject, html = email_factor.render("link", lang, instance=_instance_name(), link=link)
    try:
        await email_factor.deliver(factor.address, subject, html)
    except (EmailUnavailable, EmailSendFailed) as exc:
        logger.warning("Sign-in link for user %s not sent: %s", user.id, type(exc).__name__)


@router.post("/magic-link", response_model=MagicLinkSent, dependencies=[Depends(public())])
async def request_magic_link(body: MagicLinkRequest, request: Request) -> MagicLinkSent:
    """Mail a sign-in link to the account's verified address, when it allows links.
    Public. 503 ``auth_email_unavailable`` without an SMTP profile (a fact of the
    instance, not of an account); 429 ``auth_rate_limited`` past 20 requests an
    hour from one client address; 400 ``auth_magic_link_origin_untrusted`` when the
    request's address is neither declared nor this machine's (``_mail_base``: a fact
    of the request, not of an account). Otherwise always ``{sent: true}``. Nothing
    here reads the account: ``_send_link`` does, in the background."""
    _refuse_under_ingress(request)
    await _require_available()
    limiter = ratelimit.login_limiter
    address_key = limiter.address_key(request.scope) or client_ip(request.scope) or "shared"
    retry_after = email_factor.address_requests.retry_after(("address", address_key))
    if retry_after or not email_factor.address_requests.allow(("address", address_key)):
        raise _too_soon(retry_after or 60)
    app_address = await _mail_base(request, body.base)
    if app_address is None:
        raise _error(400, "auth_magic_link_origin_untrusted")
    spawn(
        _send_link(
            body.username, body.remember, body.lang, app_address, audit.actor_from_request(request)
        ),
        name="magic-link-mail",
    )
    return MagicLinkSent()


@router.post(
    "/magic-link/verify",
    response_model=AuthSessionResponse,
    response_model_exclude_none=True,
    dependencies=[Depends(public())],
)
async def verify_magic_link(
    body: MagicLinkVerify, request: Request, response: Response
) -> AuthSessionResponse:
    """Trade a sign-in link's token for a session (single use, 15 minutes). With TOTP
    on, the answer is the pending login of the TOTP step instead. Public. 401
    ``auth_magic_link_invalid``; 429 ``auth_rate_limited`` (the login delays of the
    client's address)."""
    _refuse_under_ingress(request)
    limiter = ratelimit.login_limiter
    address = limiter.address_key(request.scope)
    try:
        limiter.check(address, LINK_LIMIT_KEY, name_rule=False)
    except ratelimit.LoginDelayed as delayed:
        raise _rate_limited(delayed) from None
    entry = email_factor.magic_links.take(body.token.get_secret_value())
    user = await UserRepository.get(entry.user_id) if entry is not None else None
    factor = await EmailFactorRepository.get(user.id) if user is not None else None
    if (
        entry is None
        or user is None
        or user.disabled
        or user.updated_at != entry.generation
        or not _is_applicable(user)
        or factor is None
        or not factor.magic_link_enabled
    ):
        await limiter.record_failure(address, LINK_LIMIT_KEY, name_rule=False)
        if user is not None:
            await audit.record_request(
                request,
                "login",
                "failure",
                target=audit.user_target(user.id),
                details={"method": "magic_link", "reason": "link_invalid"},
            )
        raise _error(401, "auth_magic_link_invalid")
    limiter.record_success(address, LINK_LIMIT_KEY)
    if user.totp_enabled_at is not None:
        return await _second_factor_challenge(
            request, user, entry.remember, email=False, device_cookie=False
        )
    principal = await _start_session(request, response, user, remember=entry.remember)
    await audit.record_request(
        request,
        "login",
        "success",
        principal=principal,
        target=audit.user_target(user.id),
        details={"method": "magic_link"},
    )
    return await _session_payload(principal, _via(request), request)
