"""Home Assistant ingress: the Supervisor vouches for the user (docs/dev/auth.md, "Ingress").

Under ingress Home Assistant has already signed the user in. The Supervisor
proxies every ingress request from its own address on the ``hassio`` network
and adds ``X-Remote-User-Id``, ``X-Remote-User-Name`` and
``X-Remote-User-Display-Name`` when it resolved a user (it strips copies sent
by the browser). Those headers are trusted only when all of these hold:

- the install is the add-on (``MESHLOOM_INSTALL_KIND=addon``, declared, never
  inferred: ``app/services/install_kind.py``);
- the TCP peer is ``SUPERVISOR_IP`` (``peer_address``, before any trusted
  proxy rewrite of ``scope["client"]``: the port the add-on may publish on the
  host arrives from the Docker gateway, a different address, and a LAN client
  cannot spoof the TCP peer);
- the request did not come through the relay (``scope["state"]["via"]``).

The HA user is mapped to a local account ``provider = "ingress"``,
``external_id = <HA user id>``, created on first sight: the first ingress
identity of the instance as ``admin`` even when a local admin exists
(cadrage R3: the sidebar must reach an administrator; safe because the
manifest keeps ``panel_admin`` true, so only HA administrators see the
panel), the next ones with
``auth_settings.ingress_default_role`` (``member`` by default). The account
has no password; its display name follows HA's. No session cookie is ever set
or read under ingress (the HA origin is shared by every add-on): the identity
is read again from the headers on every request. These are the only
``X-Remote-User-*`` readers; forwarded headers are
``app/middleware/proxy_headers.py``'s.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from app.auth.models import USERNAME_MAX_LENGTH, User
from app.middleware.proxy_headers import peer_address
from app.repository.auth import (
    AuthAuditRepository,
    AuthSettingsRepository,
    InvalidUsernameError,
    UsernameTakenError,
    UserRepository,
)

logger = logging.getLogger(__name__)

# The Supervisor's address on the ``hassio`` network, where every ingress
# request comes from (Home Assistant developer docs, "Ingress"). A constant on
# purpose: not something a user tunes.
SUPERVISOR_IP = "172.30.32.2"

USER_ID_HEADER = "x-remote-user-id"
USER_NAME_HEADER = "x-remote-user-name"
USER_DISPLAY_NAME_HEADER = "x-remote-user-display-name"

# HA user ids are 32 hex characters; anything far from that is not the Supervisor's.
MAX_EXTERNAL_ID_LENGTH = 128
MAX_DISPLAY_NAME_LENGTH = 128

# One first-sight creation at a time, so two HA users opening the sidebar at
# once cannot both become the first, administrator, identity.
_create_lock = asyncio.Lock()


def ingress_enabled() -> bool:
    from app.services.install_kind import detect_install_kind

    return detect_install_kind()[0] == "addon"


def is_ingress_request(scope: Mapping[str, Any]) -> bool:
    """Whether the request is the Supervisor's ingress proxy (add-on, peer, not relayed)."""
    if peer_address(scope) != SUPERVISOR_IP:
        return False
    state = scope.get("state")
    if isinstance(state, Mapping) and state.get("via") == "relay":
        return False
    return ingress_enabled()


def _header_text(headers: Mapping[str, str], name: str, limit: int) -> str:
    """A header value as text. The Supervisor (aiohttp) writes UTF-8 and the
    ASGI layer reads latin-1, so a non-ASCII HA name is re-decoded."""
    raw = headers.get(name) or ""
    try:
        text = raw.encode("latin-1").decode("utf-8")
    except UnicodeError:
        text = raw
    text = "".join(char for char in text if char.isprintable()).strip()
    return text[:limit]


def ingress_identity(headers: Mapping[str, str]) -> tuple[str, str, str] | None:
    """``(external_id, username, display_name)`` from the Supervisor headers, or None."""
    external_id = (headers.get(USER_ID_HEADER) or "").strip()
    if (
        not external_id
        or len(external_id) > MAX_EXTERNAL_ID_LENGTH
        or not external_id.isascii()
        or not external_id.isprintable()
    ):
        return None
    username = _header_text(headers, USER_NAME_HEADER, USERNAME_MAX_LENGTH)
    display_name = _header_text(headers, USER_DISPLAY_NAME_HEADER, MAX_DISPLAY_NAME_LENGTH)
    return external_id, username, display_name


def _username_candidates(username: str, display_name: str, external_id: str) -> list[str]:
    """Usernames to try for a new identity: HA's own name first, then
    disambiguated forms (a local account may already hold that name)."""
    base = (username or display_name or "ha").replace(" ", "-")
    suffix = external_id[:8]
    candidates = [base, f"{base[: USERNAME_MAX_LENGTH - 3]}-ha"]
    candidates.append(f"{base[: USERNAME_MAX_LENGTH - len(suffix) - 4]}-ha-{suffix}")
    candidates.append(f"ha-{external_id}"[:USERNAME_MAX_LENGTH])
    return candidates


async def _create(
    external_id: str, username: str, display_name: str, ip: str | None
) -> User | None:
    async with _create_lock:
        existing = await UserRepository.get_by_external_id("ingress", external_id)
        if existing is not None:
            return existing
        first = not any(user.provider == "ingress" for user in await UserRepository.list_all())
        role = "admin" if first else (await AuthSettingsRepository.get()).ingress_default_role
        no_account = not await UserRepository.any_exist()
        if no_account:
            # Let the 4.x Basic import run first if it has not yet (it only
            # acts while no account exists); ingress does not depend on it.
            from app.auth.setup_code import SetupUnavailable, ensure_first_run_ready

            try:
                await ensure_first_run_ready()
            except SetupUnavailable:
                logger.warning("First-run preparation unavailable; creating the ingress identity")
        user: User | None = None
        for candidate in _username_candidates(username, display_name, external_id):
            try:
                user = await UserRepository.create(
                    candidate,
                    role,
                    display_name=display_name or username,
                    provider="ingress",
                    external_id=external_id,
                )
                break
            except (UsernameTakenError, InvalidUsernameError):
                continue
        if user is None:
            logger.warning("Could not find a free username for a Home Assistant identity")
            return None
        if no_account:
            # The first account exists now: the installation code has no use left.
            from app.auth.setup_code import clear_setup_code

            await clear_setup_code()
    logger.info("Home Assistant identity %r added as %s", user.username, user.role)
    try:
        await AuthAuditRepository.add(
            "ingress_user_created",
            "success",
            actor_user_id=user.id,
            target=f"user:{user.id}",
            ip=ip,
            via="ingress",
            details={"role": user.role},
        )
    except Exception:
        logger.warning("Could not write the auth audit row ingress_user_created")
    return user


async def resolve_ingress_user(headers: Mapping[str, str], ip: str | None) -> User | None:
    """The local account of the HA user named by the Supervisor headers.

    None without user headers (an anonymous ingress session, a direct link) and
    for a disabled account. Call only when ``is_ingress_request`` holds.
    """
    identity = ingress_identity(headers)
    if identity is None:
        return None
    external_id, username, display_name = identity
    user = await UserRepository.get_by_external_id("ingress", external_id)
    if user is None:
        user = await _create(external_id, username, display_name, ip)
        if user is None:
            return None
    elif display_name and display_name != user.display_name:
        user = await UserRepository.update(user.id, display_name=display_name) or user
    if user.disabled:
        return None
    return user
