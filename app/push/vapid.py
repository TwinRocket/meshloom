"""VAPID key management for Web Push.

Generates a P-256 key pair on first use and caches it in app_settings
via ``AppSettingsRepository``.  The public key is served to browsers
for ``PushManager.subscribe()``.

The JWT ``sub`` (``get_vapid_claims``) is, in order: the subject typed in Settings →
Notifications (``app_settings.vapid_subject``) when py-vapid accepts it; else, while
Meshloom Remote is on and registered, the Remote address ``https://<slug>.meshloom.link``
(MSHL-446, derived from ``remote_state``, never written to ``app_settings``); else
``DEFAULT_VAPID_SUBJECT``. The Remote part is cached here and refreshed by a Remote change
listener (enable, disable, register, rotate, unregister, restore). Changing the subject
does not touch subscriptions: they depend on the VAPID keys only.
"""

import base64
import logging
from typing import Literal
from urllib.parse import urlparse

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid

from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

# Used when Settings → Notifications holds no usable contact. Apple's push service
# rejects reserved TLDs such as ``.local``, so iPhones need a real one set there.
DEFAULT_VAPID_SUBJECT = "mailto:noreply@meshcore.local"

_cached_private_key: str = ""
_cached_public_key: str = ""
_cached_subject: str = ""
_cached_remote_subject: str = ""

VapidSubjectSource = Literal["stored", "remote", "default"]


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


def resolve_vapid_subject_with_source(
    stored: str = "", remote: str = ""
) -> tuple[str, VapidSubjectSource]:
    """``(subject, source)``: the stored subject when py-vapid accepts it, else the
    Remote address when usable, else the built-in default.

    ``MESHCORE_VAPID_SUBJECT`` is no longer read here: a leftover value was copied
    into the database once (``services/legacy_env_settings.py``).
    """
    normalized = normalize_vapid_subject(stored)
    if vapid_subject_is_usable(normalized):
        return normalized, "stored"
    remote_normalized = normalize_vapid_subject(remote)
    if vapid_subject_is_usable(remote_normalized):
        return remote_normalized, "remote"
    return DEFAULT_VAPID_SUBJECT, "default"


def resolve_vapid_subject(stored: str = "", remote: str = "") -> str:
    """The subject ``resolve_vapid_subject_with_source`` picks."""
    return resolve_vapid_subject_with_source(stored, remote)[0]


def set_cached_vapid_subject(subject: str) -> None:
    """Update the in-memory stored VAPID subject. Empty means no stored subject."""
    global _cached_subject
    _cached_subject = normalize_vapid_subject(subject)


def remote_vapid_subject(enabled: bool, hostname: str) -> str:
    """``https://<hostname>`` while Remote is on and registered, else ''.

    The custom domain of MSHL-428 is only tested, never stored, so the registry
    hostname (``<slug>.meshloom.link``) is the one address the instance knows.
    """
    host = (hostname or "").strip().lower()
    if not enabled or not host:
        return ""
    subject = f"https://{host}"
    return subject if vapid_subject_is_usable(subject) else ""


def set_cached_remote_vapid_subject(subject: str) -> None:
    """Update the in-memory Remote-derived subject. Empty means Remote is not usable."""
    global _cached_remote_subject
    _cached_remote_subject = normalize_vapid_subject(subject)


def get_cached_remote_vapid_subject() -> str:
    return _cached_remote_subject


async def refresh_remote_vapid_subject() -> str:
    """Recompute the Remote-derived subject from ``remote_state``. Never raises."""
    from app.repository.remote import RemoteRepository

    try:
        state = await RemoteRepository.get_state()
    except Exception:
        logger.warning("VAPID: the Remote state could not be read; no Remote subject")
        set_cached_remote_vapid_subject("")
        return ""
    subject = remote_vapid_subject(state.enabled, state.hostname)
    set_cached_remote_vapid_subject(subject)
    return subject


def watch_remote_for_vapid_subject() -> None:
    """Recompute the Remote-derived subject on every Remote change (idempotent)."""
    from app.services import remote

    remote.add_change_listener(refresh_remote_vapid_subject)


def effective_vapid_subject() -> tuple[str, VapidSubjectSource]:
    """The subject Web Push signs with now, and where it comes from."""
    return resolve_vapid_subject_with_source(_cached_subject, _cached_remote_subject)


async def ensure_vapid_keys() -> tuple[str, str]:
    """Read or generate VAPID keys. Call once at startup after DB connect."""
    global _cached_private_key, _cached_public_key

    stored_subject = await AppSettingsRepository.get_vapid_subject()
    set_cached_vapid_subject(stored_subject)
    watch_remote_for_vapid_subject()
    await refresh_remote_vapid_subject()

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

    Precedence: usable DB-cached subject (Settings → Notifications), then the
    Meshloom Remote address while Remote is on, then ``DEFAULT_VAPID_SUBJECT``.
    https origins are normalized to scheme + host so a trailing slash cannot trip
    py-vapid's ``sub`` regex. Apple's push service (APNs) rejects subjects on
    reserved TLDs such as ``.local`` with ``403 BadJwtToken``, so iOS/Safari
    operators without Remote must set a real ``mailto:`` or ``https:`` contact.
    """
    return {"sub": effective_vapid_subject()[0]}
