"""Encrypted envelope v1 for share files (MSHL-254).

Implements meshloom-community docs/contracts/links-files.md, section "Encrypted
envelope v1": HKDF-SHA256 key per (conversation secret, object id), AES-256-GCM
with the AAD ``"MLF1" || id``, layout ``"MLF1" || 0x01 || nonce || ciphertext ||
tag`` over ``header_len (2, BE) || header JSON || data``. The contract is the
source of truth; the vendored vectors (tests/fixtures/links_files_vectors.json,
pinned in tests/test_share_vectors.py) prove this module against it.

Encryption runs here, in the backend (pycryptodome), never in the browser. Sealing
10 MiB takes a noticeable fraction of a second on a Pi 4, so async callers use
``encrypt_envelope_async`` / ``decrypt_envelope_async``, which run in the default
executor, never on the event loop thread.

Decryption is single shot and authenticates before any plaintext is used: every
failure raises ``EnvelopeError`` (``EnvelopeAuthError`` for ``auth_failed``) and
returns no name, type or bytes. ``code`` carries the contract's failure code, for
logs and tests; users see one "cannot be decrypted" state. A refused secret (wrong
length, all-zero DM secret) is ``refused_secret``, raised before any crypto.

After a successful decryption the caller handles the content like a public image:
``content_type`` must be in the image allowlist and match the sniffed magic bytes
(``services/share.py``); this module does not check either.
"""

from __future__ import annotations

import asyncio
import functools
import hmac
import json
import re
import secrets
import struct
import unicodedata
from dataclasses import dataclass
from typing import Any

from Crypto.Cipher import AES
from Crypto.Hash import SHA256
from Crypto.Protocol.KDF import HKDF

MAGIC = b"MLF1"
VERSION = 0x01
HKDF_SALT = b"meshloom-share-v1"
KEY_LEN = 32
NONCE_LEN = 12
TAG_LEN = 16
HEADER_LEN_SIZE = 2
MAX_HEADER_LEN = 4096
# Same value as services/share.py MAX_IMAGE_BYTES (tests/test_share_crypto.py pins both).
MAX_DATA_BYTES = 10 * 1024 * 1024
MIN_ENVELOPE_BYTES = len(MAGIC) + 1 + NONCE_LEN + HEADER_LEN_SIZE + TAG_LEN  # 35
# Same value as services/share.py MAX_ENVELOPE_BYTES.
MAX_ENVELOPE_BYTES = MIN_ENVELOPE_BYTES + MAX_HEADER_LEN + MAX_DATA_BYTES  # 10 489 891
SECRET_LENGTHS = frozenset({16, 32})
NAME_MAX = 100
NAME_EXT_MAX = 10

_OBJECT_ID_RE = re.compile(r"[1-9A-HJ-NP-Za-km-z]{7}")
_HEADER_KEYS = frozenset({"n", "t", "s"})
_DROPPED_CATEGORIES = frozenset({"Cc", "Cf", "Co", "Cs", "Cn", "Zl", "Zp"})
_REPLACED_CHARS = frozenset('/\\<>:"|?*')
# ASCII case-insensitive letters (re.ASCII: no Unicode folds such as U+0130), exact digit.
_RESERVED_RE = re.compile(
    r"(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(?:COM|LPT)[0-9¹²³])", re.IGNORECASE | re.ASCII
)
_FALLBACK_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
_ALL_ZERO_DM = bytes(32)


class EnvelopeError(Exception):
    """An envelope cannot be produced or opened. ``code`` is the contract's code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class EnvelopeAuthError(EnvelopeError):
    """AES-GCM verification failed (wrong secret, wrong id, any changed byte)."""

    def __init__(self) -> None:
        super().__init__("auth_failed")


@dataclass(frozen=True, slots=True)
class EnvelopeContent:
    name: str  # sanitised (contract "Name sanitisation"), never empty
    content_type: str  # sender-controlled: check the allowlist and the magic bytes
    size: int
    data: bytes


def _check_secret(secret: bytes) -> None:
    """Refuse a wrong length or the all-zero DM secret, before any crypto."""
    if not isinstance(secret, bytes | bytearray) or len(secret) not in SECRET_LENGTHS:
        raise EnvelopeError("refused_secret")
    if len(secret) == 32 and hmac.compare_digest(bytes(secret), _ALL_ZERO_DM):
        raise EnvelopeError("refused_secret")


def _check_object_id(object_id: str) -> bytes:
    if not isinstance(object_id, str) or not _OBJECT_ID_RE.fullmatch(object_id):
        raise EnvelopeError("invalid_id")
    return object_id.encode("ascii")


def derive_file_key(secret: bytes, object_id: str) -> bytes:
    """``HKDF-SHA256(ikm=secret, salt="meshloom-share-v1", info=id, L=32)``."""
    _check_secret(secret)
    info = _check_object_id(object_id)
    key = HKDF(bytes(secret), KEY_LEN, HKDF_SALT, SHA256, context=info)
    assert isinstance(key, bytes)
    return key


def _header_bytes(name: str, content_type: str, size: int) -> bytes:
    # The sender's canonical form: members n, t, s, no whitespace, raw UTF-8.
    header = {"n": name, "t": content_type, "s": size}
    return json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _seal(
    secret: bytes, object_id: str, name: str, content_type: str, data: bytes, nonce: bytes
) -> bytes:
    key = derive_file_key(secret, object_id)
    if not isinstance(name, str) or not isinstance(content_type, str):
        raise EnvelopeError("header_invalid")
    if not isinstance(data, bytes | bytearray):
        # A str (or anything else) would be sealed as its repr or crash later.
        raise EnvelopeError("invalid_data")
    if len(data) > MAX_DATA_BYTES:
        raise EnvelopeError("too_large")
    header = _header_bytes(name, content_type, len(data))
    if len(header) > MAX_HEADER_LEN:
        raise EnvelopeError("header_too_long")
    if len(nonce) != NONCE_LEN:
        raise EnvelopeError("invalid_nonce")
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=TAG_LEN)
    cipher.update(MAGIC + object_id.encode("ascii"))
    ciphertext, tag = cipher.encrypt_and_digest(
        struct.pack(">H", len(header)) + header + bytes(data)
    )
    return MAGIC + bytes([VERSION]) + nonce + ciphertext + tag


def encrypt_envelope(
    secret: bytes, object_id: str, name: str, content_type: str, data: bytes
) -> bytes:
    """Seal ``data`` for ``object_id`` under a fresh random nonce (never reused).

    The sender shortens a long name itself: a header over 4096 bytes is refused.
    """
    return _seal(secret, object_id, name, content_type, data, secrets.token_bytes(NONCE_LEN))


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise EnvelopeError("header_invalid")
    return dict(pairs)


def _refuse_constant(_: str) -> Any:
    raise EnvelopeError("header_invalid")  # NaN, Infinity: not JSON


def _parse_header(raw: bytes) -> tuple[str, str, int]:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_no_duplicates, parse_constant=_refuse_constant
        )
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise EnvelopeError("header_invalid") from exc
    if not isinstance(value, dict) or set(value) != _HEADER_KEYS:
        raise EnvelopeError("header_invalid")
    name, content_type, size = value["n"], value["t"], value["s"]
    if not isinstance(name, str) or not isinstance(content_type, str):
        raise EnvelopeError("header_invalid")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_DATA_BYTES:
        raise EnvelopeError("header_invalid")
    return name, content_type, size


def decrypt_envelope(secret: bytes, object_id: str, blob: bytes) -> EnvelopeContent:
    """Open an envelope; checks in the contract's order, no output on any failure."""
    _check_secret(secret)
    aad_id = _check_object_id(object_id)
    blob = bytes(blob)
    if not MIN_ENVELOPE_BYTES <= len(blob) <= MAX_ENVELOPE_BYTES or blob[:4] != MAGIC:
        raise EnvelopeError("malformed")
    if blob[4] != VERSION:
        raise EnvelopeError("unsupported_version")
    nonce = blob[5 : 5 + NONCE_LEN]
    body, tag = blob[5 + NONCE_LEN : -TAG_LEN], blob[-TAG_LEN:]
    cipher = AES.new(derive_file_key(secret, object_id), AES.MODE_GCM, nonce=nonce, mac_len=TAG_LEN)
    cipher.update(MAGIC + aad_id)
    try:
        # One call: pycryptodome verifies the tag before returning any plaintext.
        plaintext = cipher.decrypt_and_verify(body, tag)
    except ValueError:
        raise EnvelopeAuthError() from None
    (header_len,) = struct.unpack(">H", plaintext[:HEADER_LEN_SIZE])
    if header_len > MAX_HEADER_LEN:
        raise EnvelopeError("header_too_long")
    if header_len < 1 or HEADER_LEN_SIZE + header_len > len(plaintext):
        raise EnvelopeError("header_invalid")
    name, content_type, size = _parse_header(
        plaintext[HEADER_LEN_SIZE : HEADER_LEN_SIZE + header_len]
    )
    data = plaintext[HEADER_LEN_SIZE + header_len :]
    if size != len(data):
        raise EnvelopeError("size_mismatch")
    clean = sanitize_name(name) or object_id + _FALLBACK_EXT.get(content_type, "")
    return EnvelopeContent(name=clean, content_type=content_type, size=size, data=data)


async def encrypt_envelope_async(
    secret: bytes, object_id: str, name: str, content_type: str, data: bytes
) -> bytes:
    """``encrypt_envelope`` in the default executor, off the event loop thread."""
    loop = asyncio.get_running_loop()
    seal = functools.partial(encrypt_envelope, secret, object_id, name, content_type, data)
    return await loop.run_in_executor(None, seal)


async def decrypt_envelope_async(secret: bytes, object_id: str, blob: bytes) -> EnvelopeContent:
    """``decrypt_envelope`` in the default executor, off the event loop thread."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, functools.partial(decrypt_envelope, secret, object_id, blob)
    )


def _extension(name: str) -> str:
    dot = name.rfind(".")
    if dot <= 0:
        return ""
    ext = name[dot:]
    if not 1 <= len(ext) - 1 <= NAME_EXT_MAX or " " in ext:
        return ""
    return ext


def _truncate(name: str) -> str:
    if len(name) <= NAME_MAX:
        return name
    ext = _extension(name)
    if ext:
        return name[: -len(ext)][: NAME_MAX - len(ext)].rstrip(" .") + ext
    return name[:NAME_MAX].rstrip(" .")


def _reserved(name: str) -> bool:
    return _RESERVED_RE.fullmatch(name.split(".", 1)[0].rstrip(" ")) is not None


def sanitize_name(name: str) -> str:
    """Receiver-side name sanitisation, steps 1 to 6 of the contract (code points).

    May return ``""``: the caller substitutes its own name (step 7).
    """
    out = []
    for char in name:
        category = unicodedata.category(char)
        if category in _DROPPED_CATEGORIES:
            continue
        if char in _REPLACED_CHARS:
            out.append("_")
        elif category == "Zs":
            out.append(" ")
        else:
            out.append(char)
    clean = _truncate(unicodedata.normalize("NFC", "".join(out)).strip(" ."))
    if _reserved(clean):
        clean = _truncate("_" + clean)
    return clean
