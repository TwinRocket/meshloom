"""Optional passphrase encryption of a full backup (``meshloom-backup-v2-encrypted``).

Envelope::

    {
      "format": "meshloom-backup-v2-encrypted",
      "exported_at": 1760000000,
      "public_key": "<64 hex>" | null,        # public; lets the UI say which radio
      "kdf": {"name": "scrypt", "n": 32768, "r": 8, "p": 1, "salt": "<b64, 16 bytes>"},
      "cipher": {"name": "aes-256-gcm", "nonce": "<b64, 12 bytes>"},
      "ciphertext": "<b64 of ciphertext || 16-byte tag>"
    }

- Key = ``scrypt(NFC(passphrase), salt, 32, N, r, p)``. This version writes
  N = 2^15, r = 8, p = 1 (32 MiB).
- The AAD is the canonical JSON (sorted keys, no spaces) of a fixed tuple of
  header fields, built the same way on both sides from those fields only, so an
  extra key in the file never changes it and a changed header fails the tag.
- Decrypt accepts only N a power of two with 2^14 <= N <= 2^16, r = 8, p = 1,
  a 16-byte salt, a 12-byte nonce and at most 16 MiB of ciphertext; anything
  else is refused **before** any KDF call (a file must not make the server
  allocate gigabytes).
- The async wrappers serialize KDF calls (one at a time) and run them in a
  worker thread.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import secrets
import unicodedata
from typing import Any, Literal

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import scrypt

ENCRYPTED_FORMAT = "meshloom-backup-v2-encrypted"
KDF_NAME = "scrypt"
CIPHER_NAME = "aes-256-gcm"

SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MIN_N = 2**14
SCRYPT_MAX_N = 2**16
SALT_BYTES = 16
NONCE_BYTES = 12
TAG_BYTES = 16
KEY_BYTES = 32
# Decrypt refuses more; encrypt refuses the same, so no file it writes is
# unreadable at restore time (after a reflash).
MAX_CIPHERTEXT_BYTES = 16 * 1024 * 1024
PASSPHRASE_MIN_CHARS = 8

BackupCryptoCode = Literal[
    "backup_bad_passphrase", "backup_bad_envelope", "backup_too_large_to_encrypt"
]


class BackupCryptoError(ValueError):
    """A coded failure. Never carries the passphrase or any payload."""

    def __init__(self, code: BackupCryptoCode) -> None:
        self.code: BackupCryptoCode = code
        super().__init__(code)


def is_encrypted_backup(data: object) -> bool:
    return isinstance(data, dict) and data.get("format") == ENCRYPTED_FORMAT


def _b64encode(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64decode(value: object, *, expected: int | None = None, maximum: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise BackupCryptoError("backup_bad_envelope")
    # Bound the text before decoding it (4 characters per 3 bytes).
    if maximum is not None and len(value) > (maximum // 3 + 1) * 4 + 4:
        raise BackupCryptoError("backup_bad_envelope")
    try:
        raw = base64.b64decode(value.encode("ascii"), validate=True)
    except (binascii.Error, ValueError, UnicodeEncodeError):
        raise BackupCryptoError("backup_bad_envelope") from None
    if expected is not None and len(raw) != expected:
        raise BackupCryptoError("backup_bad_envelope")
    if maximum is not None and len(raw) > maximum:
        raise BackupCryptoError("backup_bad_envelope")
    return raw


def _passphrase_bytes(passphrase: str) -> bytes:
    return unicodedata.normalize("NFC", passphrase).encode("utf-8")


def _derive_key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    key = scrypt(_passphrase_bytes(passphrase), salt, KEY_BYTES, N=n, r=r, p=p)  # type: ignore[arg-type]
    assert isinstance(key, bytes)
    return key


def _aad(
    *,
    exported_at: int,
    public_key: str | None,
    salt_b64: str,
    n: int,
    r: int,
    p: int,
    nonce_b64: str,
) -> bytes:
    header = [
        ENCRYPTED_FORMAT,
        exported_at,
        public_key,
        {"name": KDF_NAME, "n": n, "r": r, "p": p, "salt": salt_b64},
        {"name": CIPHER_NAME, "nonce": nonce_b64},
    ]
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def encrypt_backup(document: dict[str, Any], passphrase: str, *, public_key: str | None) -> dict:
    """Encrypt a backup document. Runs the KDF: call it from a worker thread."""
    if not isinstance(passphrase, str) or len(passphrase) < PASSPHRASE_MIN_CHARS:
        raise ValueError("backup_passphrase_too_short")
    plaintext = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(plaintext) > MAX_CIPHERTEXT_BYTES:
        raise BackupCryptoError("backup_too_large_to_encrypt")
    exported_at = document.get("exported_at")
    if not isinstance(exported_at, int) or isinstance(exported_at, bool):
        exported_at = 0
    salt = secrets.token_bytes(SALT_BYTES)
    nonce = secrets.token_bytes(NONCE_BYTES)
    salt_b64, nonce_b64 = _b64encode(salt), _b64encode(nonce)
    key = _derive_key(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(
        _aad(
            exported_at=exported_at,
            public_key=public_key,
            salt_b64=salt_b64,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            nonce_b64=nonce_b64,
        )
    )
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return {
        "format": ENCRYPTED_FORMAT,
        "exported_at": exported_at,
        "public_key": public_key,
        "kdf": {"name": KDF_NAME, "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P, "salt": salt_b64},
        "cipher": {"name": CIPHER_NAME, "nonce": nonce_b64},
        "ciphertext": _b64encode(ciphertext + tag),
    }


def _checked_params(envelope: object) -> tuple[int, str | None, int, int, int, str, str, bytes]:
    """Every bound checked, before any KDF call. Returns the header fields."""
    if not is_encrypted_backup(envelope):
        raise BackupCryptoError("backup_bad_envelope")
    assert isinstance(envelope, dict)
    exported_at = envelope.get("exported_at")
    if not isinstance(exported_at, int) or isinstance(exported_at, bool):
        raise BackupCryptoError("backup_bad_envelope")
    public_key = envelope.get("public_key")
    if public_key is not None and not isinstance(public_key, str):
        raise BackupCryptoError("backup_bad_envelope")
    kdf = envelope.get("kdf")
    cipher = envelope.get("cipher")
    if not isinstance(kdf, dict) or not isinstance(cipher, dict):
        raise BackupCryptoError("backup_bad_envelope")
    n, r, p = kdf.get("n"), kdf.get("r"), kdf.get("p")
    if kdf.get("name") != KDF_NAME or cipher.get("name") != CIPHER_NAME:
        raise BackupCryptoError("backup_bad_envelope")
    for value in (n, r, p):
        if not isinstance(value, int) or isinstance(value, bool):
            raise BackupCryptoError("backup_bad_envelope")
    assert isinstance(n, int) and isinstance(r, int) and isinstance(p, int)
    if not _is_power_of_two(n) or not SCRYPT_MIN_N <= n <= SCRYPT_MAX_N:
        raise BackupCryptoError("backup_bad_envelope")
    if r != SCRYPT_R or p != SCRYPT_P:
        raise BackupCryptoError("backup_bad_envelope")
    salt_b64, nonce_b64 = kdf.get("salt"), cipher.get("nonce")
    _b64decode(salt_b64, expected=SALT_BYTES)
    _b64decode(nonce_b64, expected=NONCE_BYTES)
    assert isinstance(salt_b64, str) and isinstance(nonce_b64, str)
    blob = _b64decode(envelope.get("ciphertext"), maximum=MAX_CIPHERTEXT_BYTES + TAG_BYTES)
    if len(blob) < TAG_BYTES:
        raise BackupCryptoError("backup_bad_envelope")
    return exported_at, public_key, n, r, p, salt_b64, nonce_b64, blob


def check_envelope(envelope: object) -> None:
    """Raise ``BackupCryptoError("backup_bad_envelope")`` unless every bound holds."""
    _checked_params(envelope)


def decrypt_backup(envelope: dict, passphrase: str) -> dict:
    """Decrypt an envelope. Runs the KDF: call it from a worker thread."""
    exported_at, public_key, n, r, p, salt_b64, nonce_b64, blob = _checked_params(envelope)
    if not isinstance(passphrase, str) or not passphrase:
        raise BackupCryptoError("backup_bad_passphrase")
    salt = base64.b64decode(salt_b64)
    nonce = base64.b64decode(nonce_b64)
    key = _derive_key(passphrase, salt, n, r, p)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    cipher.update(
        _aad(
            exported_at=exported_at,
            public_key=public_key,
            salt_b64=salt_b64,
            n=n,
            r=r,
            p=p,
            nonce_b64=nonce_b64,
        )
    )
    try:
        plaintext = cipher.decrypt_and_verify(blob[:-TAG_BYTES], blob[-TAG_BYTES:])
    except ValueError:
        # Wrong passphrase and tampering look the same, on purpose.
        raise BackupCryptoError("backup_bad_passphrase") from None
    try:
        document = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BackupCryptoError("backup_bad_envelope") from None
    if not isinstance(document, dict):
        raise BackupCryptoError("backup_bad_envelope")
    return document


# One KDF at a time (32-64 MiB each). Created lazily and rebound when the
# running loop changes (pytest gives each test its own loop).
_kdf_semaphore: asyncio.Semaphore | None = None
_kdf_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _semaphore() -> asyncio.Semaphore:
    global _kdf_semaphore, _kdf_semaphore_loop
    loop = asyncio.get_running_loop()
    if _kdf_semaphore is None or _kdf_semaphore_loop is not loop:
        _kdf_semaphore = asyncio.Semaphore(1)
        _kdf_semaphore_loop = loop
    return _kdf_semaphore


async def encrypt_backup_async(
    document: dict[str, Any], passphrase: str, *, public_key: str | None
) -> dict:
    async with _semaphore():
        return await asyncio.to_thread(encrypt_backup, document, passphrase, public_key=public_key)


async def decrypt_backup_async(envelope: dict, passphrase: str) -> dict:
    check_envelope(envelope)  # refused before waiting for the KDF slot
    async with _semaphore():
        return await asyncio.to_thread(decrypt_backup, envelope, passphrase)
