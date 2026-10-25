"""Local API of the link and file sharing service (``/api/share``).

The browser never talks to the service: these routes do, through
``app.services.share`` (opt-in, egress guard, coded errors). ``status`` makes no
request. Writes refuse a request the browser marks ``Sec-Fetch-Site: cross-site``
(defence in depth, SECURITY.md); CORS is untouched.

``POST /files`` takes the raw bytes of an image (not multipart), stages them
under ``<data dir>/share-tmp/``, checks them and uploads them
(``app.services.share_upload``); nothing leaves before the local checks pass.

``GET /{id}`` proxies a shared object through the disk cache (``read_object``)
with headers that never let it run as active content. ``message_id`` names the
message the id was seen in: the caller must be able to read that message (a
kiosk never reads a direct message) and its text must carry the id.
"""

from __future__ import annotations

import asyncio
import ipaddress
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response

from app import keystore
from app.auth.permissions import can_read_private, forbidden, require
from app.models import (
    ShareCacheClearResponse,
    ShareFileResponse,
    ShareLinkRequest,
    ShareLinkResponse,
    ShareMetaResponse,
    ShareStatus,
)
from app.repository import AppSettingsRepository, MessageRepository
from app.services import share, share_upload
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


# Hosts the contract's target URL rules always refuse (``invalid_url``): refused here
# first, so credentials and LAN addresses never leave the instance.
_PRIVATE_SUFFIXES = (".localhost", ".local", ".lan", ".internal", ".home.arpa")


def _checked_url(raw: str) -> str:
    """The URL when the contract would accept it, else ``share_invalid_url``.

    http(s) only, a sane length, no whitespace, no user info, no IP literal, and a
    public-looking name (at least two labels, no ``localhost``/``.local``/``.lan``/
    ``.internal`` host).
    """
    url = raw.strip()
    if not url or len(url) > MAX_URL_LENGTH or any(ch.isspace() or ord(ch) < 32 for ch in url):
        raise ShareError("share_invalid_url")
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        parts.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        raise ShareError("share_invalid_url") from None
    if parts.scheme not in ("http", "https") or not hostname:
        raise ShareError("share_invalid_url")
    if parts.username is not None or parts.password is not None:
        raise ShareError("share_invalid_url")
    host = hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ShareError("share_invalid_url")
    if "." not in host or host == "localhost" or host.endswith(_PRIVATE_SUFFIXES):
        raise ShareError("share_invalid_url")
    return url


@router.get("/status", response_model=ShareStatus, dependencies=[Depends(require("read"))])
async def get_share_status() -> ShareStatus:
    settings = await AppSettingsRepository.get()
    terms_current = share.share_terms_current(settings)
    return ShareStatus(
        enabled=share.share_is_effective(settings),
        terms_version=share.SHARE_TERMS_VERSION,
        terms_accepted_version=settings.share_terms_version,
        terms_accepted_at=settings.share_terms_accepted_at or None,
        terms_stale=settings.share_enabled and not terms_current,
        host=share.SHARE_HOST,
        hosts=list(share.SHARE_HOSTS),
        identity_ready=await identity_ready("share"),
        dm_encryption_available=keystore.has_private_key(),
        banned=share.share_banned(),
        unreachable=share.share_breaker().cooling(),
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


@router.post(
    "/files",
    response_model=ShareFileResponse,
    status_code=201,
    dependencies=[Depends(require("send")), Depends(refuse_cross_site)],
)
async def upload_share_file(request: Request) -> ShareFileResponse:
    """Upload an image: the raw body, its ``Content-Type`` and ``Content-Length``.

    Headers: ``X-Share-Mode`` (``public``; ``encrypted`` is 422
    ``share_mode_unavailable`` until M3), optional ``X-Share-Name`` (validated,
    never stored nor sent in public mode). Every refusal before the upload
    reads at most what was declared and makes no request.
    """
    # Off first: a disabled instance answers share_disabled and reads nothing.
    if await share.share_egress_state() is None:
        raise ShareError("share_disabled")
    headers = request.headers
    mode = headers.get("x-share-mode", "").strip()
    if mode == "encrypted":
        raise ShareError("share_mode_unavailable")
    if mode != "public" or "transfer-encoding" in headers:
        raise ShareError("share_invalid_request")
    content_type = share_upload.normal_content_type(headers.get("content-type"))
    if content_type not in share.IMAGE_CONTENT_TYPES:
        raise ShareError("share_type_refused")
    length = share_upload.declared_length(headers.get("content-length"), content_type)
    share_upload.check_name(headers.get("x-share-name"))
    # Known local refusals before receiving megabytes for nothing.
    if share.share_banned():
        raise ShareError("share_banned")
    if share.share_breaker().cooling():
        raise ShareError("share_unavailable")
    with share_upload.upload_slot():
        async with share_upload.staged_upload(
            request.stream(), content_type=content_type, length=length
        ) as staged:
            await asyncio.to_thread(share_upload.check_staged, staged)
            stored = await share_upload.upload_file(staged)
    return ShareFileResponse(
        id=stored.id,
        url=stored.url,
        expires_at=stored.expires_at,
        size=stored.size,
        mode="public",
        text=share_upload.suggested_text(stored.url),
    )


# Query parameters a read accepts: nothing else, never twice.
_READ_QUERY = frozenset({"message_id"})
MESSAGE_ID_MAX = 2**63 - 1

# Headers of every served object: never sniffed, never run, never shared by a cache.
_OBJECT_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
    "Cache-Control": "private, max-age=3600",
}


def _checked_read_query(request: Request) -> None:
    keys = [key for key, _ in request.query_params.multi_items()]
    if any(key not in _READ_QUERY for key in keys) or len(keys) != len(set(keys)):
        raise ShareError("share_invalid_request")


async def _check_message(request: Request, share_id: str, message_id: int | None) -> None:
    """The caller may read ``message_id`` and its text carries ``share_id``.

    A missing message, or one that does not carry the id, is ``share_not_found``
    (it says nothing of other messages); a direct message without
    ``read_private`` is the usual 403.
    """
    if message_id is None:
        return
    message = await MessageRepository.get_by_id(message_id)
    if message is None:
        raise ShareError("share_not_found")
    if message.type != "CHAN" and not can_read_private(request):
        raise forbidden("read_private")
    if share_id not in (message.text or ""):
        raise ShareError("share_not_found")


@router.get(
    "/{share_id}/meta",
    response_model=ShareMetaResponse,
    response_model_exclude_unset=True,
    dependencies=[Depends(require("read"))],
)
async def get_share_meta(
    request: Request,
    share_id: str,
    message_id: int | None = Query(default=None, ge=1, le=MESSAGE_ID_MAX),
) -> ShareMetaResponse:
    _checked_read_query(request)
    if share.is_share_id(share_id) and await share.share_egress_state() is not None:
        await _check_message(request, share_id, message_id)
    # Off (share_disabled) and a bad id (share_not_found) are answered here.
    meta = await share.get_meta(share_id)
    fields: dict[str, object] = {
        "kind": meta.kind,
        "target_host": meta.target_host,
        "expires_at": meta.expires_at,
    }
    if meta.kind == "file":
        # Only the file members are added: a link answer keeps its v1 shape.
        public = meta.mode == "public"
        fields.update(
            created_at=meta.created_at,
            mode=meta.mode,
            content_type=(
                meta.content_type if meta.content_type in share.IMAGE_CONTENT_TYPES else None
            ),
            size=meta.size if meta.size is not None and meta.size >= 0 else None,
            # The name travels encrypted (M3); a public file has none.
            name=None,
            decryptable=True if public else None,
        )
    return ShareMetaResponse.model_validate(fields)


@router.get("/{share_id}", dependencies=[Depends(require("read"))])
async def get_share_object(
    request: Request,
    share_id: str,
    message_id: int | None = Query(default=None, ge=1, le=MESSAGE_ID_MAX),
) -> Response:
    """The bytes of a shared object, from the disk cache or fetched once."""
    if not share.is_share_id(share_id):
        raise ShareError("share_invalid_id")
    _checked_read_query(request)
    # Off means off, cached or not.
    if await share.share_egress_state() is None:
        raise ShareError("share_disabled")
    await _check_message(request, share_id, message_id)
    obj = await share.read_object(share_id)
    is_image = obj.content_type in share.IMAGE_CONTENT_TYPES
    content_type = obj.content_type if is_image else share.OCTET_STREAM
    extension = share.OBJECT_EXTENSIONS[content_type]
    disposition = "inline" if is_image else "attachment"
    headers = {
        **_OBJECT_HEADERS,
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{share_id}.{extension}",
    }
    return Response(content=obj.content, media_type=content_type, headers=headers)


@router.post(
    "/cache/clear",
    response_model=ShareCacheClearResponse,
    dependencies=[Depends(require("admin")), Depends(refuse_cross_site)],
)
async def clear_share_cache() -> ShareCacheClearResponse:
    """Empty the local object cache (no request; works with sharing off)."""
    return ShareCacheClearResponse(freed_bytes=await share.clear_object_cache())
