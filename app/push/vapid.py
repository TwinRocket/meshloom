"""VAPID key management for Web Push.

Generates a P-256 key pair on first use and caches it in app_settings
via ``AppSettingsRepository``.  The public key is served to browsers
for ``PushManager.subscribe()``.
"""

import base64
import logging
from urllib.parse import urlparse

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid

from app.config import DEFAULT_VAPID_SUBJECT, settings
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

_cached_private_key: str = ""
_cached_public_key: str = ""
_cached_subject: str = ""


def normalize_vapid_subject(raw: str) -> str:
    """Strip noise py-vapid rejects (whitespace, https path, trailing slash)."""
    stored = (raw or "").strip()
    if not stored:
        return ""
    if stored.lower().startswith("mailto:"):
        return stored
    if stored.lower().startswith("https:"):
        parsed = urlparse(stored)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return stored
        return f"https://{parsed.netloc}"
    return stored


def vapid_subject_is_usable(subject: str) -> bool:
    """True when py-vapid will accept this string as JWT ``sub``."""
    if not subject:
        return False
    lowered = subject.lower()
    if lowered.startswith("mailto:"):
        rest = subject[7:]
        if "@" not in rest:
            return False
        local, _, host = rest.partition("@")
        return bool(local.strip()) and bool(host.strip())
    if lowered.startswith("https://"):
        parsed = urlparse(subject)
        return (
            parsed.scheme.lower() == "https"
            and bool(parsed.hostname)
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment
        )
    return False


def resolve_vapid_subject(stored: str = "", env_subject: str | None = None) -> str:
    """Pick the first usable subject: stored, then env, then built-in default."""
    fallback = settings.vapid_subject if env_subject is None else env_subject
    for candidate in (stored, fallback, DEFAULT_VAPID_SUBJECT):
        normalized = normalize_vapid_subject(candidate)
        if vapid_subject_is_usable(normalized):
            return normalized
    return DEFAULT_VAPID_SUBJECT


def set_cached_vapid_subject(subject: str) -> None:
    """Update the in-memory VAPID subject. Empty means fall back to env."""
    global _cached_subject
    _cached_subject = normalize_vapid_subject(subject)


async def ensure_vapid_keys() -> tuple[str, str]:
    """Read or generate VAPID keys. Call once at startup after DB connect."""
    global _cached_private_key, _cached_public_key

    stored_subject = await AppSettingsRepository.get_vapid_subject()
    set_cached_vapid_subject(stored_subject)

    private, public = await AppSettingsRepository.get_vapid_keys()
    if private and public:
        _cached_private_key = private
        _cached_public_key = public
        logger.info("VAPID keys loaded from database")
        return _cached_private_key, _cached_public_key

    # Generate new key pair
    vapid = Vapid()
    vapid.generate_keys()

    # Private key as base64url-encoded raw 32-byte EC scalar — the format
    # that pywebpush passes to ``Vapid.from_string()``.
    raw_priv = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")  # type: ignore[union-attr]
    _cached_private_key = base64.urlsafe_b64encode(raw_priv).rstrip(b"=").decode("ascii")

    # Public key as uncompressed P-256 point, base64url-encoded (no padding)
    # for the browser Push API's applicationServerKey
    raw_pub = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)  # type: ignore[union-attr]
    _cached_public_key = base64.urlsafe_b64encode(raw_pub).rstrip(b"=").decode("ascii")

    await AppSettingsRepository.set_vapid_keys(_cached_private_key, _cached_public_key)
    logger.info("Generated and stored new VAPID key pair")

    return _cached_private_key, _cached_public_key


def get_vapid_public_key() -> str:
    """Return the cached VAPID public key (base64url). Must call ensure_vapid_keys() first."""
    return _cached_public_key


def get_vapid_private_key() -> str:
    """Return the cached VAPID private key (base64url). Must call ensure_vapid_keys() first."""
    return _cached_private_key


def get_vapid_claims() -> dict[str, str]:
    """VAPID JWT claims for Web Push.

    Precedence: usable DB-cached subject, then ``MESHCORE_VAPID_SUBJECT``,
    then ``DEFAULT_VAPID_SUBJECT``. https origins are normalized to scheme +
    host so a trailing slash cannot trip py-vapid's ``sub`` regex.
    Apple's push service (APNs) rejects subjects on reserved TLDs such as
    ``.local`` with ``403 BadJwtToken``, so iOS/Safari operators must set this
    to a real ``mailto:`` or ``https:`` contact.
    """
    return {"sub": resolve_vapid_subject(_cached_subject)}
