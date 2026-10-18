"""Local API of the link and file sharing service (``/api/share``).

The browser never talks to the service: these routes do, through
``app.services.share`` (opt-in, egress guard, coded errors). ``status`` makes no
request. Writes refuse a request the browser marks ``Sec-Fetch-Site: cross-site``
(defence in depth, SECURITY.md); CORS is untouched.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request

from app import keystore
from app.auth.permissions import require
from app.models import ShareLinkRequest, ShareLinkResponse, ShareMetaResponse, ShareStatus
from app.repository import AppSettingsRepository
from app.services import share
from app.services.instance_identity import identity_ready
from app.services.share import ShareError

router = APIRouter(prefix="/share", tags=["share"])

# The service's own bound is the authority; this only refuses the absurd early.
MAX_URL_LENGTH = 2048


def refuse_cross_site(request: Request) -> None:
    """Dependency of every write route: 403 ``share_cross_site`` from another site.

    A request without the header (curl, bots) and ``same-origin``, ``same-site``
    and ``none`` pass.
    """
    if request.headers.get("sec-fetch-site", "").strip().lower() == "cross-site":
        raise ShareError("share_cross_site")


def _checked_url(raw: str) -> str:
    """The URL when it is a plain http(s) one of sane length, else ``share_invalid_url``."""
    url = raw.strip()
    if not url or len(url) > MAX_URL_LENGTH or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise ShareError("share_invalid_url")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
    except ValueError:
        raise ShareError("share_invalid_url") from None
    if parts.scheme not in ("http", "https") or not hostname:
        raise ShareError("share_invalid_url")
    return url


@router.get("/status", response_model=ShareStatus, dependencies=[Depends(require("read"))])
async def get_share_status() -> ShareStatus:
    settings = await AppSettingsRepository.get()
    return ShareStatus(
        enabled=settings.share_enabled,
        host=share.SHARE_HOST,
        hosts=list(share.SHARE_HOSTS),
        identity_ready=await identity_ready("share"),
        dm_encryption_available=keystore.has_private_key(),
        banned=share.share_banned(),
        shorten_mode=settings.share_shorten_mode,
        preview_policy=settings.share_preview_policy,
    )


@router.post(
    "/links",
    response_model=ShareLinkResponse,
    status_code=201,
    dependencies=[Depends(require("send")), Depends(refuse_cross_site)],
)
async def create_share_link(body: ShareLinkRequest) -> ShareLinkResponse:
    # Off first: a disabled instance answers share_disabled whatever the body.
    if await share.share_egress_state() is None:
        raise ShareError("share_disabled")
    link = await share.create_link(_checked_url(body.url))
    return ShareLinkResponse(id=link.id, url=link.url, expires_at=link.expires_at)


@router.get(
    "/{share_id}/meta", response_model=ShareMetaResponse, dependencies=[Depends(require("read"))]
)
async def get_share_meta(share_id: str) -> ShareMetaResponse:
    meta = await share.get_meta(share_id)
    return ShareMetaResponse(
        kind=meta.kind, target_host=meta.target_host, expires_at=meta.expires_at
    )
