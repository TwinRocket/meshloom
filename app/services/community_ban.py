"""Community ban: Community refused this radio key with 403 ``banned``.

An operator of Meshloom Community can ban a radio key. Community then answers
**403** with the error body ``{"detail": str, "code": "banned", "server_time":
int}`` (``meshloom-community/docs/contracts/errors.md``) on the key's
authenticated write routes (``BANNED_REFUSED_ROUTES``) and on the Live
handshake, and its MQTT auth refuses the key, so the Stats publisher cannot
connect either.

Retrying cannot lift a ban, so the first such answer puts this process in a
banned state (in memory, not persisted) for ``BANNED_HOLD_S``:

- ``stats_request`` refuses the routes of ``BANNED_REFUSED_ROUTES`` locally,
  with the same 403 ``community_banned`` the browser gets for Community's
  own refusal, without any request;
- the Live relay stops (state ``banned``) and does not reconnect;
- the Stats publisher does not reconnect to the broker
  (``MeshloomStatsPublisher._pre_connect``) and shows no error toast.

The ban also reaches a process that makes none of those calls: when the
broker refuses the Stats publisher's credentials (the MQTT auth of a banned
key), the publisher sends one probe (``probe_community_ban`` in
``meshloom_community``: ``POST /v1/hashtags/resolve`` with no hash byte, which
Community refuses for a banned key and otherwise answers with an empty list,
without any side effect), at most once per ``BAN_PROBE_MIN_INTERVAL_S``, before
it decides on the error toast.

The state ends on a restart, on a user action that changes the Community
settings (a join or an IATA change, ``PATCH /community``, an opt-out) or
restarts Live (``POST /community/live/restart``), and on its own after
``BANNED_HOLD_S``: then the next call checks again, once, whether the ban was
lifted. Nothing else has to make that call: the Stats publisher connects
again, and a refusal from the broker sends the probe above, which starts the
banned state again while the ban holds. ``GET /community`` reports it as
``banned: true``.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Community error code (403) of a banned radio key.
BANNED_CODE = "banned"
# Error code this server answers the browser with, translated as errors.community_banned.
COMMUNITY_BANNED_ERROR = "community_banned"
COMMUNITY_BANNED_MESSAGE = "This radio has been banned from Meshloom Community"
# How long a ban answer holds before one call checks again (12 h).
BANNED_HOLD_S = 12 * 3600.0
# Authenticated routes Community refuses with 403 banned (errors.md). Only
# these are refused locally; every other route keeps working.
BANNED_REFUSED_ROUTES: frozenset[tuple[str, str]] = frozenset(
    {
        ("PUT", "/v1/me/iata"),
        ("POST", "/v1/me/iata/override"),
        ("PUT", "/v1/me/hashtags"),
        ("POST", "/v1/hashtags/resolve"),
        ("POST", "/v1/hashtags/samples"),
        ("POST", "/v1/directory/packets/observations"),
    }
)

# Least time between two probes sent after a broker auth refusal: a refusal
# for another reason (a bad clock, a broker problem) must not turn every
# reconnect into an HTTP request.
BAN_PROBE_MIN_INTERVAL_S = 3600.0

_banned_until: float | None = None
_last_probe_at: float | None = None


def _now(now: float | None) -> float:
    return time.monotonic() if now is None else now


def is_banned_answer(status: int | None, payload: Any) -> bool:
    """A 403 whose body ``code`` is ``banned``. Only ``code`` is stable."""
    return status == 403 and isinstance(payload, dict) and payload.get("code") == BANNED_CODE


def community_banned(*, now: float | None = None) -> bool:
    """True while a ban answer holds (since the last one, for ``BANNED_HOLD_S``)."""
    global _banned_until
    if _banned_until is None:
        return False
    if _now(now) < _banned_until:
        return True
    _banned_until = None
    return False


def banned_route(method: str, path: str) -> bool:
    return (method.upper(), path) in BANNED_REFUSED_ROUTES


def note_banned(*, now: float | None = None) -> bool:
    """Record a ban answer. True when it starts the banned state (log it once)."""
    global _banned_until
    started = not community_banned(now=now)
    _banned_until = _now(now) + BANNED_HOLD_S
    if started:
        logger.warning(
            "Meshloom Community banned this radio key: writes, Live and the Stats "
            "publisher stop until a restart, a Community settings change, or %.0f h",
            BANNED_HOLD_S / 3600,
        )
    return started


def clear_ban() -> bool:
    """End the banned state (a user action). True when one was active.

    It also lets the next broker auth refusal probe again at once.
    """
    global _banned_until, _last_probe_at
    was = community_banned()
    _banned_until = None
    _last_probe_at = None
    return was


def take_ban_probe(*, now: float | None = None) -> bool:
    """True (and counted) when a ban probe may go now, else False."""
    global _last_probe_at
    current = _now(now)
    if _last_probe_at is not None and current - _last_probe_at < BAN_PROBE_MIN_INTERVAL_S:
        return False
    _last_probe_at = current
    return True


def banned_http_exception() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": COMMUNITY_BANNED_ERROR, "message": COMMUNITY_BANNED_MESSAGE},
    )


def reset_community_ban_for_tests() -> None:
    global _banned_until, _last_probe_at
    _banned_until = None
    _last_probe_at = None
