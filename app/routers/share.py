"""Local API of the link and file sharing service (``/api/share``).

The browser never talks to the service: these routes do, through
``app.services.share`` (opt-in, egress guard, coded errors). ``status`` makes no
request. Writes refuse a request the browser marks ``Sec-Fetch-Site: cross-site``
(defence in depth, SECURITY.md); CORS is untouched.

``POST /files`` takes the raw bytes of an image (not multipart), stages them
under ``<data dir>/share-tmp/``, checks them and uploads them
(``app.services.share_upload``); nothing leaves before the local checks pass.
In encrypted mode the image is sealed under the secret of the conversation named
by ``X-Share-Conversation-Type`` / ``X-Share-Conversation-Id``
(``app.services.share_secrets``) before it leaves: a channel is named by its
opaque ``share_conversation_id``, never by its key, which the browser never sends
back (the legacy ``X-Share-Conversation-Key`` header is refused).

``GET /{id}`` proxies a shared object through the disk cache (``read_object``)
with headers that never let it run as active content. ``message_id`` names the
message the id was seen in: the caller must be able to read that message (a
kiosk never reads a direct message) and its text must carry the id. An
encrypted object (``application/octet-stream``) is cached as ciphertext and
decrypted per request with the secret of that message's conversation, resolved
here from the message row, never supplied by the client. A decrypted image is
served ``Cache-Control: no-store`` (plaintext never lands in the browser's disk
cache) under a name whose extension is forced to its checked type. No secret is
ever logged, returned or sent.

``POST /{id}/report`` forwards a report (``reason``, optional ``details``) to the
service, signed with the instance key (``share.report_object``).
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, Query, Request, Response

from app import keystore
from app.auth.permissions import can_read_private, forbidden, require
from app.log_redaction import secret_scope
from app.models import (
    Message,
    ShareCacheClearResponse,
    ShareFileResponse,
    ShareLinkRequest,
    ShareLinkResponse,
    ShareMetaResponse,
    ShareReportRequest,
    ShareReportResponse,
    ShareStatus,
)
from app.repository import AppSettingsRepository, MessageRepository
from app.services import share, share_crypto, share_secrets, share_upload
from app.services.instance_identity import identity_ready
from app.services.share import ShareError, ShareObject

logger = logging.getLogger(__name__)

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

    Headers: ``X-Share-Mode`` (``public`` or ``encrypted``), optional
    ``X-Share-Name`` (percent-encoded UTF-8; validated, sealed in the envelope
    in encrypted mode, never sent otherwise). Encrypted mode also needs
    ``X-Share-Conversation-Type`` (``channel`` or ``contact``) and
    ``X-Share-Conversation-Id`` (a channel's ``share_conversation_id`` or a
    contact's public key), resolved here to the secret. A request still carrying
    ``X-Share-Conversation-Key`` is refused: no channel key travels from the
    browser. Every refusal before the upload reads at most what was declared and
    makes no request.
    """
    # Off first: a disabled instance answers share_disabled and reads nothing.
    if await share.share_egress_state() is None:
        raise ShareError("share_disabled")
    headers = request.headers
    mode = headers.get("x-share-mode", "").strip()
    if mode not in ("public", "encrypted") or "transfer-encoding" in headers:
        raise ShareError("share_invalid_request")
    content_type = share_upload.normal_content_type(headers.get("content-type"))
    if content_type not in share.IMAGE_CONTENT_TYPES:
        raise ShareError("share_type_refused")
    length = share_upload.declared_length(headers.get("content-length"), content_type)
    name = share_upload.header_name(headers.get("x-share-name"))
    conversation_type = headers.get("x-share-conversation-type", "").strip()
    conversation = headers.get("x-share-conversation-id", "").strip()
    if "x-share-conversation-key" in headers:
        # Retired (MSHL-378): a client must not send key material back.
        raise ShareError("share_invalid_request")
    if mode == "encrypted" and (
        conversation_type not in share_secrets.CONVERSATION_TYPES or not conversation
    ):
        raise ShareError("share_invalid_request")
    # Known local refusals before receiving megabytes for nothing.
    if share.share_banned():
        raise ShareError("share_banned")
    if share.share_breaker().cooling():
        raise ShareError("share_unavailable")
    secret = (
        await share_secrets.upload_secret(conversation_type, conversation)
        if mode == "encrypted"
        else None
    )
    # The whole encrypted upload runs with the secret registered for redaction.
    scope = secret_scope(secret) if secret is not None else contextlib.nullcontext()
    with scope, share_upload.upload_slot():
        async with share_upload.staged_upload(
            request.stream(), content_type=content_type, length=length
        ) as staged:
            await asyncio.to_thread(share_upload.check_staged, staged)
            if secret is None:
                stored = await share_upload.upload_file(staged)
            else:
                stored = await share_upload.upload_encrypted(staged, secret, name)
    return ShareFileResponse(
        id=stored.id,
        url=stored.url,
        expires_at=stored.expires_at,
        size=stored.size,
        mode="encrypted" if secret is not None else "public",
        text=share_upload.suggested_text(stored.url, encrypted=secret is not None),
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
# A decrypted image is plaintext of a private conversation: never stored by any cache.
_DECRYPTED_HEADERS = {**_OBJECT_HEADERS, "Cache-Control": "no-store"}

# Extensions a name may already end with for each served image type.
_NAME_EXTENSIONS: dict[str, frozenset[str]] = {
    "image/jpeg": frozenset({"jpg", "jpeg"}),
    "image/png": frozenset({"png"}),
    "image/gif": frozenset({"gif"}),
    "image/webp": frozenset({"webp"}),
}


def _served_name(name: str, content_type: str) -> str:
    """``name`` with an extension that matches ``content_type`` (an allowlisted image).

    The name is sender-chosen: ``x.html`` on a PNG/HTML polyglot must not keep a
    dangerous extension once saved and opened locally. A matching extension (any
    case) is kept; any other is replaced by the type's own, and a name without
    one gets it.
    """
    stem, dot, suffix = name.rpartition(".")
    if dot and suffix.lower() in _NAME_EXTENSIONS.get(content_type, frozenset()):
        return name
    base = stem if dot and stem else name
    extension = share.OBJECT_EXTENSIONS[content_type]
    # A longer replacement extension (.gif -> .webp) must not push the name past
    # the envelope's own bound: shorten the stem, never the extension.
    base = base[: share_crypto.NAME_MAX - len(extension) - 1].rstrip(" .") or "image"
    return f"{base}.{extension}"


def _checked_read_query(request: Request) -> None:
    keys = [key for key, _ in request.query_params.multi_items()]
    if any(key not in _READ_QUERY for key in keys) or len(keys) != len(set(keys)):
        raise ShareError("share_invalid_request")


async def _check_message(request: Request, share_id: str, message_id: int | None) -> Message | None:
    """The message ``message_id`` when the caller may read it and it carries ``share_id``.

    A missing message, or one that does not carry the id, is ``share_not_found``
    (it says nothing of other messages); a direct message without
    ``read_private`` is the usual 403. None without ``message_id``.
    """
    if message_id is None:
        return None
    message = await MessageRepository.get_by_id(message_id)
    if message is None:
        raise ShareError("share_not_found")
    if message.type != "CHAN" and not can_read_private(request):
        raise forbidden("read_private")
    if share_id not in (message.text or ""):
        raise ShareError("share_not_found")
    return message


async def _open_envelope(
    share_id: str, blob: bytes, message: Message | None
) -> tuple[ShareObject, str]:
    """The image sealed in an encrypted object, checked like a public one, and its name.

    The name's extension is forced to the checked type (``_served_name``). The
    secret comes from the conversation of ``message`` only: no message, no
    resolvable secret, or a failed authentication is 422 ``share_undecryptable``
    (no partial output). The decrypted type is sender-controlled: it must be an
    allowlisted image whose magic bytes and header agree (``checked_object``),
    else 415 ``share_type_refused`` / 413 ``share_too_large``.
    """
    secret = share_secrets.message_secret(message) if message is not None else None
    if secret is None:
        raise ShareError("share_undecryptable")
    try:
        with secret_scope(secret):
            content = await share_crypto.decrypt_envelope_async(secret, share_id, blob)
    except share_crypto.EnvelopeError as exc:
        # The code only: never the secret, the bytes or a partial header.
        logger.info("Encrypted share object not decryptable here: %s", exc.code)
        raise ShareError("share_undecryptable") from None
    if content.content_type not in share.IMAGE_CONTENT_TYPES:
        raise ShareError("share_type_refused")
    checked = await asyncio.to_thread(
        share.checked_object, ShareObject(content=content.data, content_type=content.content_type)
    )
    return checked, _served_name(content.name, checked.content_type)


# Refusals of a decryption: the object exists but cannot be shown here.
_UNDECRYPTABLE = frozenset({"share_undecryptable", "share_type_refused", "share_too_large"})


async def _encrypted_meta(share_id: str, message: Message | None) -> dict[str, object]:
    """``decryptable``, ``content_type``, ``name`` and ``size`` of an encrypted file.

    Decrypted here (the object is fetched through the cache) with the secret of
    ``message``'s conversation: false with null members when that fails, null
    when the object cannot be fetched now (unknown).
    """
    unknown: dict[str, object] = {"content_type": None, "name": None, "size": None}
    if message is None or share_secrets.message_secret(message) is None:
        return {**unknown, "decryptable": False}
    try:
        blob = await share.read_object(share_id)
        if blob.content_type != share.OCTET_STREAM:
            return {**unknown, "decryptable": False}
        image, name = await _open_envelope(share_id, blob.content, message)
    except ShareError as exc:
        return {**unknown, "decryptable": False if exc.code in _UNDECRYPTABLE else None}
    return {
        "decryptable": True,
        "content_type": image.content_type,
        "name": name,
        "size": len(image.content),
    }


@router.get(
    "/{share_id}/meta",
    response_model=ShareMetaResponse,
    response_model_exclude_unset=True,
    dependencies=[Depends(require("read"))],
)
async def get_share_meta(
    request: Request,
    response: Response,
    share_id: str,
    message_id: int | None = Query(default=None, ge=1, le=MESSAGE_ID_MAX),
) -> ShareMetaResponse:
    _checked_read_query(request)
    # The answer may name a decrypted file: never kept in a browser or proxy cache.
    response.headers["Cache-Control"] = "no-store"
    message = None
    if share.is_share_id(share_id) and await share.share_egress_state() is not None:
        message = await _check_message(request, share_id, message_id)
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
            # The name travels encrypted; a public file has none.
            name=None,
            decryptable=True if public else None,
        )
        if meta.mode == "encrypted":
            fields.update(await _encrypted_meta(share_id, message))
    return ShareMetaResponse.model_validate(fields)


@router.post(
    "/{share_id}/report",
    response_model=ShareReportResponse,
    status_code=202,
    dependencies=[Depends(require("send")), Depends(refuse_cross_site)],
)
async def report_share_object(share_id: str, body: ShareReportRequest) -> ShareReportResponse:
    """Report an object to the service (``POST /v1/reports``, JWT variant).

    The token is minted for ``id + "\\n" + reason`` with the instance key, so the
    service records the reporter by that key. ``details`` travels unbound.
    """
    # Off first: a disabled instance answers share_disabled whatever the id.
    if await share.share_egress_state() is None:
        raise ShareError("share_disabled")
    if not share.is_share_id(share_id):
        raise ShareError("share_invalid_id")
    await share.report_object(share_id, body.reason, body.details)
    return ShareReportResponse()


@router.get("/{share_id}", dependencies=[Depends(require("read"))])
async def get_share_object(
    request: Request,
    share_id: str,
    message_id: int | None = Query(default=None, ge=1, le=MESSAGE_ID_MAX),
) -> Response:
    """The bytes of a shared object, from the disk cache or fetched once.

    An encrypted object is decrypted with the secret of ``message_id``'s
    conversation and served as the image it seals (422 ``share_undecryptable``
    without a message or with the wrong one), with ``Cache-Control: no-store``
    and a name whose extension matches the served type.
    """
    if not share.is_share_id(share_id):
        raise ShareError("share_invalid_id")
    _checked_read_query(request)
    # Off means off, cached or not.
    if await share.share_egress_state() is None:
        raise ShareError("share_disabled")
    message = await _check_message(request, share_id, message_id)
    obj = await share.read_object(share_id)
    if obj.content_type == share.OCTET_STREAM:
        image, name = await _open_envelope(share_id, obj.content, message)
        headers = {
            **_DECRYPTED_HEADERS,
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(name, safe='')}",
        }
        return Response(content=image.content, media_type=image.content_type, headers=headers)
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
