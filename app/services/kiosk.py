"""The Raspberry Pi kiosk image: host marker, ``kiosk`` account, setup banner code.

``/var/lib/meshloom/kiosk`` is a host-provisioned flag, the same class as
``MESHCORE_MANAGED_PORTS`` (``app/AGENTS.md``, "Host flags"): written by
``scripts/build/build_rpi_kiosk_image.sh`` for new images and by
``pkg/nfpm/postinstall.sh`` on an image flashed before 5.0.0. It is not a
user setting and is not in the user docs. With it:

- the first start creates the ``kiosk`` account (role ``kiosk``, no password,
  provider ``system``) when it is absent. **No trusted-network rule is seeded**:
  the launcher signs in with a device token (T60, not here);
- the installation code is issued even on a database created by this start (the
  screen is in a room, the LAN is not), and the screen shows it while no
  administrator exists (``kiosk_setup_code``).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.auth.providers.trusted_network import rule_blocker
from app.middleware import proxy_headers

logger = logging.getLogger(__name__)

KIOSK_MARKER = Path("/var/lib/meshloom/kiosk")
KIOSK_USERNAME = "kiosk"

# The plaintext of the code issued at this start, for the kiosk screen only.
_displayed_code: str | None = None


def kiosk_host() -> bool:
    return KIOSK_MARKER.exists()


async def ensure_kiosk_user() -> None:
    """Create the ``kiosk`` account when the host is a kiosk and it is absent."""
    from app.repository.auth import UserRepository

    if not kiosk_host():
        return
    if await UserRepository.get_credentials(KIOSK_USERNAME) is not None:
        return
    await UserRepository.create(KIOSK_USERNAME, "kiosk", display_name="Kiosk", provider="system")
    logger.info("Kiosk host: created the %r account", KIOSK_USERNAME)


def remember_displayed_code(code: str | None) -> None:
    global _displayed_code
    _displayed_code = code


async def displayed_setup_code(scope: Mapping[str, Any], headers: Mapping[str, str]) -> str | None:
    """The installation code for the kiosk screen, or None (fail closed).

    Only on a kiosk host, only while the code issued at this start is still the
    stored one (``meshloom-admin setup-code`` replaces it from another process),
    and only to the request the kiosk browser makes: a direct loopback peer that
    is no trusted proxy, carries no proxy header, did not come through the relay
    or ingress, and passes the strict ``Host`` guard. Those are the checks of the
    trusted-network provider (``rule_blocker``, whose loopback refusal is lifted
    here because loopback is exactly the kiosk browser).
    """
    if _displayed_code is None or not kiosk_host():
        return None
    try:
        from app.auth.middleware import request_via
        from app.auth.tokens import hash_token
        from app.repository.auth import AuthSettingsRepository

        peer = proxy_headers.peer_address(scope)
        if peer not in ("127.0.0.1", "::1"):
            return None
        auth_settings = await AuthSettingsRepository.get()
        if rule_blocker(
            scope,
            headers,
            request_via(scope),
            trusted_proxies=proxy_headers.current(),
            public_urls=auth_settings.public_urls,
            install_kind="addon",  # only to let loopback through the check
        ):
            return None
        stored = await AuthSettingsRepository.get_setup_code_hash()
    except Exception:
        logger.exception("Kiosk setup code check failed; not shown")
        return None
    if stored is None or stored != hash_token(_displayed_code):
        return None
    return _displayed_code
