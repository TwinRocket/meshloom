"""``/api/auth/kiosk-*``: the kiosk launcher signs its screen in with its device token.

Included by ``app/routers/auth.py``. Rules in ``app/services/kiosk.py`` and
``docs/dev/auth.md``, "Kiosk device token":

- ``POST /auth/kiosk-bootstrap`` (``read``): the launcher sends the device
  token in ``Authorization: Bearer`` and gets a one-time path. Refused (403
  ``auth_kiosk_bootstrap_refused``, one reason for every case) unless the host
  is a kiosk, the credential is a bearer token of the system ``kiosk`` account
  and the request is a direct loopback one. The answer carries no secret
  beyond the one-time code.
- ``GET /auth/kiosk-session?code=…`` (public, ``PUBLIC_ROUTES``): Chromium
  opens it; a valid code (single use, 60 s) on a direct loopback request of a
  kiosk host gets a session cookie of the ``kiosk`` account. Either way the
  answer is a 303 to ``/`` (the browser then shows the app, or the sign-in or
  setup page), so a refused code shows nothing but that page; only a success
  sets a cookie.

Rate limits (``app/services/kiosk.py``): past ``BOOTSTRAP_RATE`` codes a minute
the bootstrap answers 429 ``auth_rate_limited`` (``params.retry_after``,
``Retry-After``); past ``EXCHANGE_FAILURE_RATE`` failed exchanges a minute the
exchange refuses every code (reason ``rate_limited``).

Each outcome writes an audit row (``token.used`` for a refused bootstrap,
``login`` for the exchange, ``details.method = "kiosk_token"``). Nothing here
logs the token or a code.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth import audit, sessions
from app.auth.middleware import client_ip
from app.auth.permissions import public, require
from app.auth.principal import Principal, get_principal
from app.repository.auth import UserRepository
from app.services import kiosk

router = APIRouter()

BOOTSTRAP_REFUSED = "auth_kiosk_bootstrap_refused"
SESSION_PATH = "/api/auth/kiosk-session"


class KioskBootstrapResponse(BaseModel):
    # ``/api/auth/kiosk-session?code=…``, relative to the instance.
    path: str
    expires_in: int


def _refused() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": BOOTSTRAP_REFUSED, "message": "Kiosk sign-in refused"},
    )


async def _bootstrap_refusal(request: Request, principal: Principal | None) -> str | None:
    if not kiosk.kiosk_host():
        return "not_kiosk_host"
    if principal is None or principal.credential != "token":
        return "not_a_token"
    if not kiosk.is_kiosk_account(principal.user):
        return "not_kiosk_account"
    if not await kiosk.direct_loopback_request(request.scope, request.headers):
        return "not_loopback"
    return None


@router.post(
    "/kiosk-bootstrap",
    response_model=KioskBootstrapResponse,
    responses={403: {"description": BOOTSTRAP_REFUSED}},
    dependencies=[Depends(require("read"))],
)
async def kiosk_bootstrap(request: Request) -> KioskBootstrapResponse:
    """Trade the kiosk's device token for a one-time session path (loopback only)."""
    principal = get_principal(request)
    reason = await _bootstrap_refusal(request, principal)
    if reason is None and not kiosk.bootstrap_allowed():
        await audit.record_request(
            request,
            "token.used",
            "delayed",
            target=f"token:{principal.token_id}" if principal and principal.token_id else None,
            details={"reason": "rate_limited", "route": "kiosk_bootstrap"},
        )
        retry = int(kiosk.RATE_WINDOW_S)
        raise HTTPException(
            status_code=429,
            detail={
                "code": "auth_rate_limited",
                "message": "Too many attempts: retry later",
                "params": {"retry_after": retry},
            },
            headers={"Retry-After": str(retry)},
        )
    if reason is not None:
        await audit.record_request(
            request,
            "token.used",
            "refused",
            target=f"token:{principal.token_id}" if principal and principal.token_id else None,
            details={"reason": reason, "status": 403, "route": "kiosk_bootstrap"},
        )
        raise _refused()
    assert principal is not None
    code = kiosk.issue_bootstrap_code(principal.user)
    return KioskBootstrapResponse(
        path=f"{SESSION_PATH}?code={code}", expires_in=int(kiosk.BOOTSTRAP_CODE_TTL_S)
    )


@router.get(
    "/kiosk-session",
    status_code=303,
    response_class=RedirectResponse,
    dependencies=[Depends(public())],
)
async def kiosk_session(
    request: Request, code: str = Query(default="", max_length=128)
) -> RedirectResponse:
    """Exchange a one-time code for a session cookie of the ``kiosk`` account,
    then go to ``/``."""
    response = RedirectResponse("/", status_code=303)
    response.headers["referrer-policy"] = "no-referrer"
    reason: str | None = None
    user = None
    if not kiosk.kiosk_host():
        reason = "not_kiosk_host"
    elif not await kiosk.direct_loopback_request(request.scope, request.headers):
        reason = "not_loopback"
    elif not kiosk.exchange_allowed():
        reason = "rate_limited"
    else:
        # Spent here whatever follows: a code is good for one try.
        user_id = kiosk.redeem_bootstrap_code(code)
        if user_id is None:
            reason = "invalid_code"
        else:
            user = await UserRepository.get(user_id)
            if user is None or user.disabled or not kiosk.is_kiosk_account(user):
                reason = "account_unavailable"
    if reason is not None or user is None:
        if reason != "rate_limited":
            kiosk.note_exchange_failure()
        await audit.record_request(
            request,
            "login",
            "failure",
            details={"method": "kiosk_token", "reason": reason or "account_unavailable"},
        )
        return response
    issued = await sessions.issue_session(
        user,
        ip=client_ip(request.scope),
        user_agent=request.headers.get("user-agent"),
        via="lan",
    )
    secure = bool(request.scope.get("auth_secure", False))
    response.headers.append(
        "set-cookie",
        sessions.session_cookie_header(issued.token, secure=secure, max_age=issued.max_age),
    )
    principal = Principal(user=user, via="lan", credential="session", session_id=issued.session.id)
    await audit.record_request(
        request,
        "login",
        "success",
        principal=principal,
        target=audit.user_target(user.id),
        details={"method": "kiosk_token"},
    )
    return response
