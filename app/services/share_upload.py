"""Uploads to the share service (``POST /api/share/files``, public mode).

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

Encrypted mode (M3, T28) comes on the same route; until then it is 422
``share_mode_unavailable`` with no request. Tests: ``tests/test_share_upload.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import secrets
import stat
from collections.abc import AsyncIterable, AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict

from app.net.egress_guard import StreamedBody
from app.services import share
from app.services.share import ShareError

logger = logging.getLogger(__name__)

TMP_DIRNAME = "share-tmp"
# Uploads at once: each holds up to 10 MiB on disk while it is checked and sent.
MAX_CONCURRENT_UPLOADS = 2
# Chunk size when the staged file is streamed to the service.
READ_CHUNK = 64 * 1024
# Bytes the magic number check needs (WebP: 12).
SNIFF_BYTES = 16
STAGED_PREFIX = "upload-"
# Longest Content-Length accepted as text (far above any cap).
_LENGTH_DIGITS = 12

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
    with contextlib.suppress(FileNotFoundError):
        path.unlink()


@contextlib.asynccontextmanager
async def staged_upload(
    chunks: AsyncIterable[bytes], *, content_type: str, length: int
) -> AsyncIterator[StagedFile]:
    """The body written to a private file of ``share-tmp/``, removed on exit.

    Reads exactly ``length`` bytes: one more stops the read at once, and a
    short body is refused too (400 ``share_invalid_request``).
    """
    path, fd = await asyncio.to_thread(_new_file)
    try:
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, "wb") as out:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > length:
                    raise ShareError("share_invalid_request")
                digest.update(chunk)
                await asyncio.to_thread(out.write, chunk)
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
    with open(staged.path, "rb") as f:
        head = f.read(SNIFF_BYTES)
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
        f = await asyncio.to_thread(open, path, "rb")
        try:
            while chunk := await asyncio.to_thread(f.read, READ_CHUNK):
                yield chunk
        finally:
            await asyncio.to_thread(f.close)

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
    answer: ShareFile = share._parse(ShareFile, response)
    host = httpx.URL(answer.url).host
    if (
        not share.is_share_id(answer.id)
        or not share.is_share_host(host)
        or answer.url != f"https://{host}/{answer.id}"
        or answer.size != staged.size
    ):
        logger.warning("Share service answered a file outside the grammar")
        raise ShareError("share_unavailable")
    return answer


def suggested_text(url: str) -> str:
    """The composer's proposed message: ``📷 <url>`` (30 bytes on ``meshl.ink``)."""
    return f"\N{CAMERA} {url}"
