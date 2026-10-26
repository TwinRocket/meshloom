"""Uploads to the share service (``POST /api/share/files``, public and encrypted mode).

The browser sends the raw bytes of an image to the backend, which checks them
locally and forwards them to ``POST /v1/files`` with the instance JWT
(contract ``meshloom-community/docs/contracts/links-files.md``). Nothing leaves
the instance before every local check passed.

- **Staging.** ``staged_upload`` streams the request body (``request.stream()``)
  to a file of ``<data dir>/share-tmp/`` (``tmp_directory``; mode 0700, the file
  0600, created with ``O_EXCL`` under a random name: ``X-Share-Name`` never
  reaches a path). Never read whole into memory: chunks go to disk as they
  arrive and the sha256 is computed on the way. The package's systemd unit has
  ``PrivateTmp`` and ``ProtectSystem=strict``: the data dir is writable.
- **Caps.** The declared ``Content-Length`` must be present and at most the cap
  of the declared type (``share.MAX_IMAGE_BYTES``, ``share.MAX_GIF_BYTES`` for a
  GIF): 413 ``share_too_large`` before reading. While streaming, one byte more
  than declared stops the read at once, as does a short body (400
  ``share_invalid_request``).
- **Receive deadline.** The whole body must arrive within
  ``RECEIVE_TIMEOUT_S`` (uvicorn has no body read timeout): a client that
  announces 10 MiB and trickles bytes would otherwise hold an upload slot
  forever. Past it: 408 ``share_upload_timeout``, file removed.
- **Disk errors.** A local ``OSError`` while staging or checking (``ENOSPC``,
  ``share-tmp`` replaced by a link or a file) is 500 ``share_storage_failed``
  (coded, logged with its errno name or fixed reason, never a path), file removed.
  The same goes for an error while the file is streamed to the service.
- **Checks** (``check_staged``), on the file, before any egress: the magic bytes
  must name the declared type (``share.sniff_image_type``, else 415
  ``share_type_refused``) and ``imagesize`` must read the dimensions from the
  header (else 415), at most ``share.MAX_IMAGE_PIXELS`` (else 413
  ``share_too_large``, as the service answers).
- **Concurrency.** At most ``MAX_CONCURRENT_UPLOADS`` uploads at once (disk on a
  Pi); one more is refused, never queued (429 ``share_rate_limited``,
  ``scope = "local"``).
- **Cleanup.** The staged file is removed in ``finally`` (success, refusal,
  cancellation); ``clean_tmp_directory`` empties ``share-tmp/`` at startup, so a
  crash or a kill leaves nothing behind for long.
- **Upload** (``upload_file``): ``share_request`` with the body streamed from the
  file (``StreamedBody``: exact ``Content-Length``, never chunked),
  ``X-Share-Mode``, ``X-Share-Sha256`` and the binding string
  ``mode + "\\n" + sha256 + "\\n" + proposed_id + "\\n" + content_length``
  (no ``X-Share-Id`` in public mode, so ``proposed_id`` is empty).

- **Encrypted upload** (``upload_encrypted``, MSHL-255): after the same staging and
  checks, the image is sealed in an envelope v1 (``share_crypto``) under the
  conversation secret (``share_secrets.upload_secret``) and a fresh base58 id
  drawn here (``new_share_id``: the id is in the key derivation and the AAD, so the
  client proposes it). The envelope goes to its own private file of
  ``share-tmp/`` and is sent with ``X-Share-Mode: encrypted``, ``Content-Type:
  application/octet-stream``, ``X-Share-Id``, ``X-Share-Sha256`` and no name
  header. ``409 id_taken`` draws a new id **and** re-encrypts, at most
  ``ENCRYPTED_ATTEMPTS`` times (then 502 ``share_unavailable``); a service older
  than M3 answers ``415 type_refused``, which is 422 ``share_mode_unavailable``.
  The secret is never logged, returned or sent (``secret_scope`` around its use).

Tests: ``tests/test_share_upload.py``, ``tests/test_share_encrypted.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import hashlib
import logging
import os
import secrets
import stat
import urllib.parse
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from app.log_redaction import secret_scope
from app.net.egress_guard import StreamedBody
from app.services import share, share_crypto
from app.services.share import ShareError

logger = logging.getLogger(__name__)

TMP_DIRNAME = "share-tmp"
# Uploads at once: each holds up to 10 MiB on disk while it is checked and sent.
MAX_CONCURRENT_UPLOADS = 2
# Chunk size when the staged file is streamed to the service.
READ_CHUNK = 64 * 1024
# Bytes the magic number check needs (WebP: 12).
SNIFF_BYTES = 16
# Seconds the whole request body may take to arrive (10 MiB at ~85 KiB/s).
RECEIVE_TIMEOUT_S = 120.0
STAGED_PREFIX = "upload-"
# Longest Content-Length accepted as text (far above any cap).
_LENGTH_DIGITS = 12

# Uploads of an encrypted file: 409 id_taken draws a new id (and re-encrypts).
ENCRYPTED_ATTEMPTS = 3
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
SHARE_ID_LENGTH = 7
# Reserved first path segments of the service that match the id grammar.
RESERVED_IDS = frozenset({"privacy"})

_active = 0


def tmp_directory() -> Path:
    """``<data dir>/share-tmp``: next to the database, inside the unit's ``ReadWritePaths``."""
    from app.config import settings

    return Path(settings.database_path).resolve().parent / TMP_DIRNAME


def _ensure_directory() -> Path:
    directory = tmp_directory()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"{TMP_DIRNAME} is not a directory")
    if stat.S_IMODE(info.st_mode) != 0o700:
        os.chmod(directory, 0o700)
    return directory


def clean_tmp_directory() -> int:
    """Remove the leftovers of ``share-tmp/`` (startup); the number of entries removed.

    Only regular files and symbolic links directly inside are removed (a link
    is unlinked, never followed); a missing directory is fine.
    """
    try:
        entries = list(os.scandir(tmp_directory()))
    except (FileNotFoundError, NotADirectoryError):
        return 0
    removed = 0
    for entry in entries:
        if entry.is_symlink() or entry.is_file(follow_symlinks=False):
            with contextlib.suppress(FileNotFoundError):
                os.unlink(entry.path)
                removed += 1
    if removed:
        logger.info("Removed %d leftover share upload file(s)", removed)
    return removed


def normal_content_type(raw: str | None) -> str:
    """The media type without parameters, lowercase (the contract compares it so)."""
    return (raw or "").split(";")[0].strip().lower()


def max_bytes_for(content_type: str) -> int:
    return share.MAX_GIF_BYTES if content_type == "image/gif" else share.MAX_IMAGE_BYTES


def declared_length(raw: str | None, content_type: str) -> int:
    """The ``Content-Length`` when present, decimal and within the type's cap.

    Missing, malformed or zero: 400 ``share_invalid_request`` (chunked uploads
    are refused: the size is part of the binding). Over the cap: 413
    ``share_too_large``, before anything is read.
    """
    value = (raw or "").strip()
    if not value or not value.isascii() or not value.isdigit() or len(value) > _LENGTH_DIGITS:
        raise ShareError("share_invalid_request")
    length = int(value)
    if length == 0:
        raise ShareError("share_invalid_request")
    if length > max_bytes_for(content_type):
        raise ShareError("share_too_large")
    return length


def check_name(raw: str | None) -> None:
    """``X-Share-Name``, when sent, is a plain file name; it is never used as a path.

    Public mode stores no name (the contract's public file has none), so it is
    only validated: 1 to 255 characters, no control character, no ``/`` or
    ``\\``, not ``.`` or ``..``.
    """
    if raw is None:
        return
    if (
        not raw
        or len(raw) > 255
        or raw in (".", "..")
        or any(ch in "/\\" or ord(ch) < 32 or ord(ch) == 127 for ch in raw)
    ):
        raise ShareError("share_invalid_request")


def header_name(raw: str | None) -> str:
    """The file name of ``X-Share-Name`` for the envelope header, ``""`` when absent.

    The header is percent-encoded UTF-8 (``encodeURIComponent``), since HTTP
    headers carry Latin-1 at best; a plain ASCII name decodes to itself. The
    decoded name passes ``check_name`` (400 ``share_invalid_request``) and the
    contract's sanitisation; the result never reaches a path.
    """
    if raw is None:
        return ""
    try:
        name = urllib.parse.unquote(raw, encoding="utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ShareError("share_invalid_request") from None
    check_name(name)
    return share_crypto.sanitize_name(name)


def new_share_id() -> str:
    """A uniformly random id of the contract's grammar, never a reserved path."""
    while True:
        candidate = "".join(secrets.choice(BASE58_ALPHABET) for _ in range(SHARE_ID_LENGTH))
        if candidate not in RESERVED_IDS:
            return candidate


@dataclass(frozen=True)
class StagedFile:
    path: Path
    size: int
    sha256: str
    content_type: str


@contextlib.contextmanager
def upload_slot() -> Iterator[None]:
    """One of ``MAX_CONCURRENT_UPLOADS`` slots, else 429 ``share_rate_limited`` (local)."""
    global _active
    if _active >= MAX_CONCURRENT_UPLOADS:
        raise ShareError("share_rate_limited", retry_after=5, scope="local")
    _active += 1
    try:
        yield
    finally:
        _active -= 1


def reset_upload_state_for_tests() -> None:
    global _active
    _active = 0


def _new_file() -> tuple[Path, int]:
    directory = _ensure_directory()
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    path = directory / f"{STAGED_PREFIX}{secrets.token_hex(16)}"
    return path, os.open(path, flags, 0o600)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        # Never mask the answer; the startup cleanup gets it later.
        logger.warning("Could not remove a staged share upload: %s", _os_reason(exc))


def _os_reason(exc: OSError) -> str:
    """A loggable reason for an ``OSError``: its errno name, else our own message.

    Never the path (``str(exc)`` would carry it). Errors raised by this module
    itself (``share-tmp is not a directory``) have no errno: their first
    argument is a fixed text; anything else is reduced to its class name.
    """
    if exc.errno is not None:
        return f"errno {errno.errorcode.get(exc.errno, exc.errno)}"
    if exc.args and isinstance(exc.args[0], str) and exc.filename is None:
        return exc.args[0]
    return type(exc).__name__


def _storage_failed(exc: OSError) -> ShareError:
    logger.warning("Share upload staging failed: %s", _os_reason(exc))
    return ShareError("share_storage_failed")


@contextlib.asynccontextmanager
async def staged_upload(
    chunks: AsyncIterable[bytes], *, content_type: str, length: int
) -> AsyncIterator[StagedFile]:
    """The body written to a private file of ``share-tmp/``, removed on exit.

    Reads exactly ``length`` bytes: one more stops the read at once, and a
    short body is refused too (400 ``share_invalid_request``). The body must
    arrive within ``RECEIVE_TIMEOUT_S`` (408 ``share_upload_timeout``); a disk
    error is 500 ``share_storage_failed``.
    """
    try:
        path, fd = await asyncio.to_thread(_new_file)
    except OSError as exc:
        raise _storage_failed(exc) from None
    try:
        digest = hashlib.sha256()
        size = 0
        try:
            async with asyncio.timeout(RECEIVE_TIMEOUT_S):
                with os.fdopen(fd, "wb") as out:
                    async for chunk in chunks:
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > length:
                            raise ShareError("share_invalid_request")
                        digest.update(chunk)
                        await asyncio.to_thread(out.write, chunk)
        except TimeoutError:
            raise ShareError("share_upload_timeout") from None
        except OSError as exc:
            raise _storage_failed(exc) from None
        if size != length:
            raise ShareError("share_invalid_request")
        yield StagedFile(path=path, size=size, sha256=digest.hexdigest(), content_type=content_type)
    finally:
        await asyncio.to_thread(_remove, path)


def check_staged(staged: StagedFile) -> None:
    """The contract's local checks on the staged file, before any egress.

    The magic bytes must name the declared type and the header must give the
    dimensions (415 ``share_type_refused``); a GIF stays within its cap and the
    image within ``MAX_IMAGE_PIXELS`` (413 ``share_too_large``).
    """
    try:
        with open(staged.path, "rb") as f:
            head = f.read(SNIFF_BYTES)
    except OSError as exc:
        raise _storage_failed(exc) from None
    if share.sniff_image_type(head) != staged.content_type:
        raise ShareError("share_type_refused")
    if staged.size > max_bytes_for(staged.content_type):
        raise ShareError("share_too_large")
    dimensions = share.image_dimensions(staged.path)
    if dimensions is None:
        raise ShareError("share_type_refused")
    if dimensions[0] * dimensions[1] > share.MAX_IMAGE_PIXELS:
        raise ShareError("share_too_large")


def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    async def chunks() -> AsyncIterator[bytes]:
        # A disk error while streaming (the file removed mid-send) is a coded
        # local failure, not an outage of the service: ``share_request`` passes
        # a ``ShareError`` through without judging the breaker.
        try:
            f = await asyncio.to_thread(open, path, "rb")
            try:
                while chunk := await asyncio.to_thread(f.read, READ_CHUNK):
                    yield chunk
            finally:
                await asyncio.to_thread(f.close)
        except OSError as exc:
            raise _storage_failed(exc) from None

    return chunks()


class ShareFile(BaseModel):
    """``POST /v1/files`` answer."""

    model_config = ConfigDict(extra="ignore")

    id: str
    url: str
    expires_at: int
    size: int


def binding_string(mode: str, sha256: str, proposed_id: str, length: int) -> str:
    """The contract's ``POST /v1/files`` binding string."""
    return f"{mode}\n{sha256}\n{proposed_id}\n{length}"


def _checked_answer(
    response: share.CheckedResponse, size: int, proposed_id: str | None = None
) -> ShareFile:
    """The ``POST /v1/files`` answer when it keeps the contract, else ``share_unavailable``."""
    answer: ShareFile = share._parse(ShareFile, response)
    host = httpx.URL(answer.url).host
    if (
        not share.is_share_id(answer.id)
        or not share.is_share_host(host)
        or answer.url != f"https://{host}/{answer.id}"
        or answer.size != size
        or (proposed_id is not None and answer.id != proposed_id)
    ):
        logger.warning("Share service answered a file outside the grammar")
        raise ShareError("share_unavailable")
    return answer


async def upload_file(staged: StagedFile) -> ShareFile:
    """``POST /v1/files`` in public mode, the body streamed from the staged file."""
    mode = "public"
    response = await share.share_request(
        "POST",
        "/v1/files",
        binding=binding_string(mode, staged.sha256, "", staged.size),
        body=StreamedBody(length=staged.size, open=lambda: _file_chunks(staged.path)),
        content_type=staged.content_type,
        extra_headers={"X-Share-Mode": mode, "X-Share-Sha256": staged.sha256},
    )
    return _checked_answer(response, staged.size)


def _write_envelope(blob: bytes) -> tuple[Path, str]:
    """The envelope in a private file of ``share-tmp/`` and its sha256 hex."""
    path, fd = _new_file()
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(blob)
    except BaseException:
        _remove(path)
        raise
    return path, hashlib.sha256(blob).hexdigest()


async def _seal(secret: bytes, share_id: str, name: str, staged: StagedFile, data: bytes) -> bytes:
    try:
        with secret_scope(secret):
            return await share_crypto.encrypt_envelope_async(
                secret, share_id, name, staged.content_type, data
            )
    except share_crypto.EnvelopeError as exc:
        # The code only: never the secret, the name or the data.
        logger.warning("Encrypted share refused locally: %s", exc.code)
        if exc.code == "refused_secret":
            raise ShareError("share_mode_not_allowed") from None
        raise ShareError("share_invalid_request") from None


async def upload_encrypted(staged: StagedFile, secret: bytes, name: str) -> ShareFile:
    """``POST /v1/files`` in encrypted mode: the staged image sealed under ``secret``.

    Each attempt draws a new id and seals again (fresh nonce), so a retry after
    ``409 id_taken`` never reuses an id. The envelope file is removed after
    each attempt.
    """
    mode = "encrypted"
    try:
        data = await asyncio.to_thread(staged.path.read_bytes)
    except OSError as exc:
        raise _storage_failed(exc) from None
    for _attempt in range(ENCRYPTED_ATTEMPTS):
        share_id = new_share_id()
        blob = await _seal(secret, share_id, name, staged, data)
        try:
            path, digest = await asyncio.to_thread(_write_envelope, blob)
        except OSError as exc:
            raise _storage_failed(exc) from None
        size = len(blob)
        del blob
        try:
            response = await share.share_request(
                "POST",
                "/v1/files",
                binding=binding_string(mode, digest, share_id, size),
                body=StreamedBody(length=size, open=lambda p=path: _file_chunks(p)),
                content_type=share.OCTET_STREAM,
                extra_headers={
                    "X-Share-Mode": mode,
                    "X-Share-Id": share_id,
                    "X-Share-Sha256": digest,
                },
            )
        except ShareError as exc:
            if exc.code == "share_id_taken":
                continue
            if exc.code == "share_type_refused":
                # A service older than M3 refuses the mode itself (contract).
                raise ShareError("share_mode_unavailable") from None
            raise
        finally:
            await asyncio.to_thread(_remove, path)
        return _checked_answer(response, size, share_id)
    logger.warning("Share service refused %d proposed ids in a row", ENCRYPTED_ATTEMPTS)
    raise ShareError("share_unavailable")


def suggested_text(url: str, *, encrypted: bool = False) -> str:
    """The composer's proposed message: ``📷 <url>``, ``🔒 <url>`` for an encrypted file."""
    emoji = "\N{LOCK}" if encrypted else "\N{CAMERA}"
    return f"{emoji} {url}"
