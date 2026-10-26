"""Secrets at rest: one key file and AES-256-GCM for the third-party service secrets.

Scope (MSHL-133): the write-only fields of the notification channels
(``notification_channels.secrets``) and of the fanout configs (the keys of
``secret_fields.FANOUT_SECRET_KEYS`` inside ``fanout_configs.config``). The radio
private key is not in the database at all; MeshCore channel keys, the VAPID
private key, the 4.18.x ``app_settings.notification_destinations`` and the
other settings stay in plain text.

- **Key file.** ``secrets.key`` next to the database (``<dir of
  MESHCORE_DATABASE_PATH>/secrets.key``): 32 random bytes, created with mode
  0600 by ``ensure_key()`` at startup, before the migrations. An existing file
  is never overwritten; one of the wrong size is moved aside
  (``secrets.key.invalid-<time>``) and a new key is made. No rotation.
  ``probe_available()`` tells, without creating or changing anything, whether a
  key could be loaded or made (previews such as the backup inspection).
- **Token.** ``v1:`` + base64(12-byte nonce + ciphertext + 16-byte tag), a fresh
  random nonce per encryption. The AAD names the table, the row id and the
  field (``aad()``): a ciphertext copied to another row or field fails to
  decrypt.
- **Tolerant read.** A value that is not a ``v1:`` token is returned as it is (a
  row written before the migration, or by an older build); the next write
  encrypts it.
- **Unreadable.** A token that fails to decrypt (the database was copied without
  its key, or with another one) raises ``SecretUnreadable``. Callers treat the
  secret as absent: the channel or module is disabled with ``secret_unreadable``
  and the UI asks for the secret again. Startup never fails on it.

A key or a secret is never logged: the errors carry the field name at most.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES

logger = logging.getLogger(__name__)

KEY_FILENAME = "secrets.key"
KEY_BYTES = 32
NONCE_BYTES = 12
TAG_BYTES = 16
PREFIX = "v1:"
SECRET_UNREADABLE = "secret_unreadable"


class SecretUnreadable(Exception):
    """A stored token cannot be decrypted with the current key."""


class SecretStoreUnavailable(Exception):
    """No key could be loaded or created, so nothing can be encrypted."""


_lock = threading.Lock()
_key: bytes | None = None
_key_loaded_from: Path | None = None


def key_path() -> Path:
    """``secrets.key`` in the folder of the database (it travels with it)."""
    from app.config import settings

    return Path(settings.database_path).parent / KEY_FILENAME


def _create(path: Path) -> bytes:
    key = secrets.token_bytes(KEY_BYTES)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, key)
        os.fsync(fd)
    finally:
        os.close(fd)
    logger.info("Created the secrets key file %s", path)
    return key


def _load(path: Path) -> bytes | None:
    """The key in ``path``, a new key when there is none; None when unusable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _create(path)
    except FileExistsError:
        pass
    try:
        mode = path.stat().st_mode & 0o777
        if mode & 0o077:
            try:
                os.chmod(path, 0o600)
                logger.warning("The secrets key file was readable by others: mode set to 0600")
            except OSError:
                logger.warning("The secrets key file is readable by others (mode %o)", mode)
        key = path.read_bytes()
    except OSError as exc:
        logger.error("The secrets key file cannot be read (%s)", type(exc).__name__)
        return None
    if len(key) == KEY_BYTES:
        return key
    aside = path.with_name(f"{KEY_FILENAME}.invalid-{int(time.time())}")
    try:
        os.replace(path, aside)
    except OSError as exc:
        logger.error("The secrets key file is invalid and cannot be moved (%s)", type(exc).__name__)
        return None
    logger.warning("The secrets key file was invalid: moved to %s, a new key is made", aside)
    try:
        return _create(path)
    except OSError as exc:
        logger.error("A new secrets key file cannot be created (%s)", type(exc).__name__)
        return None


def ensure_key(path: Path | None = None) -> bool:
    """Load the key, creating the file (mode 0600) when there is none. Never raises.

    True when a key is available. Called at startup before the migrations
    (``Database.connect``); a later call for the same path is a no-op.
    """
    global _key, _key_loaded_from
    target = path or key_path()
    with _lock:
        if _key is not None and _key_loaded_from == target:
            return True
        try:
            key = _load(target)
        except OSError as exc:
            logger.error("The secrets key file cannot be created (%s)", type(exc).__name__)
            key = None
        _key = key
        _key_loaded_from = target if key is not None else None
        return key is not None


def reset() -> None:
    """Forget the loaded key (tests; the next use reloads or recreates the file)."""
    global _key, _key_loaded_from
    with _lock:
        _key = None
        _key_loaded_from = None


def _current_key() -> bytes | None:
    if _key is None:
        ensure_key()
    return _key


def available() -> bool:
    """Whether a secret can be encrypted now (loads or creates the key on first use).

    Lets a multi-step write such as the backup restore refuse up front instead of
    failing with ``SecretStoreUnavailable`` halfway through."""
    return _current_key() is not None


def _can_create_in(folder: Path) -> bool:
    """Whether ``_load`` could make ``folder`` (when missing) and a file in it."""
    probe = folder
    while not probe.exists():
        if probe.parent == probe:
            return False
        probe = probe.parent
    return probe.is_dir() and os.access(probe, os.W_OK | os.X_OK)


def _readable(path: Path, st: os.stat_result) -> bool:
    """Whether ``_load`` could read the existing key file ``path``.

    ``_load`` first sets a file readable by others to 0600, which works for its owner
    (or root) and makes it readable to them; otherwise the current mode decides."""
    euid = os.geteuid()
    if st.st_mode & 0o077 and euid in (0, st.st_uid):
        return True
    return os.access(path, os.R_OK)


def probe_available() -> bool:
    """Read-only twin of ``available()`` for previews such as the backup inspection.

    Answers whether ``available()`` would succeed, without creating ``secrets.key``,
    its folder or a moved-aside copy, without changing the file mode and without
    loading the key into memory (MSHL-405). Never logs. It mirrors ``_load``: a
    loaded key, a readable regular file of the right size, a readable file of the
    wrong size in a writable folder (moved aside, a new key made), or no file in a
    folder that can be made and written. The answer is a best effort (permission
    bits through ``os.access``; a read-only file system that refuses the 0600
    ``chmod`` is not seen): the restore itself still calls ``available()`` and
    refuses when it fails."""
    if _key is not None:
        return True
    path = key_path()
    try:
        path.lstat()
    except FileNotFoundError:
        return _can_create_in(path.parent)
    except OSError:
        return False
    try:
        st = path.stat()  # a dangling symlink fails here, as it does in ``_load``
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode) or not _readable(path, st):
        return False
    if st.st_size == KEY_BYTES:
        return True
    # A file of the wrong size is moved aside and replaced by a new key.
    return _can_create_in(path.parent)


def aad(table: str, row_id: str, field: str) -> str:
    """The associated data of one stored secret: its table, row id and field."""
    return f"meshloom:{table}:{row_id}:{field}"


def is_encrypted(value: object) -> bool:
    """Whether ``value`` has the shape of a ``v1:`` token (not whether it decrypts)."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return False
    try:
        raw = base64.b64decode(value[len(PREFIX) :], validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(raw) >= NONCE_BYTES + TAG_BYTES


def encrypt(plain: str, aad_text: str) -> str:
    """``v1:`` token of ``plain`` bound to ``aad_text``, with a fresh random nonce."""
    key = _current_key()
    if key is None:
        raise SecretStoreUnavailable("no secrets key")
    nonce = secrets.token_bytes(NONCE_BYTES)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=TAG_BYTES)
    cipher.update(aad_text.encode("utf-8"))
    ciphertext, tag = cipher.encrypt_and_digest(plain.encode("utf-8"))
    return PREFIX + base64.b64encode(nonce + ciphertext + tag).decode("ascii")


def decrypt(token: str, aad_text: str) -> str:
    """The plain text of a ``v1:`` token; any other value is returned as it is.

    Known edge case, accepted: a legacy plain-text secret that happens to start with
    ``v1:`` is taken for a token and reads as unreadable (``SecretUnreadable``), so the
    channel or module asks for it again. Plain values cannot be told apart by shape."""
    if not isinstance(token, str) or not token.startswith(PREFIX):
        return token
    if not is_encrypted(token):
        raise SecretUnreadable("malformed token")
    key = _current_key()
    if key is None:
        raise SecretUnreadable("no secrets key")
    raw = base64.b64decode(token[len(PREFIX) :], validate=True)
    nonce, body, tag = raw[:NONCE_BYTES], raw[NONCE_BYTES:-TAG_BYTES], raw[-TAG_BYTES:]
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=TAG_BYTES)
    cipher.update(aad_text.encode("utf-8"))
    try:
        plain = cipher.decrypt_and_verify(body, tag)
        return plain.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise SecretUnreadable("authentication failed") from None


def seal(value: Any, aad_text: str) -> str:
    """Encrypt any JSON value (a string, or an object such as the webhook headers)."""
    return encrypt(json.dumps(value, separators=(",", ":"), sort_keys=True), aad_text)


def unseal(value: Any, aad_text: str) -> Any:
    """The value ``seal`` was given; a value that is not a token is returned as it is."""
    if not isinstance(value, str) or not value.startswith(PREFIX):
        return value
    plain = decrypt(value, aad_text)
    try:
        return json.loads(plain)
    except json.JSONDecodeError:
        raise SecretUnreadable("not a sealed value") from None


def opens(value: Any, aad_text: str) -> bool:
    """Whether ``value`` is a token this key decrypts under ``aad_text``."""
    if not is_encrypted(value):
        return False
    try:
        unseal(value, aad_text)
    except SecretUnreadable:
        return False
    return True
