"""What this radio says about its own position, and what Community gets of it.

``advert_location_broadcast`` reads the radio's advert location policy
(``self_info['adv_loc_policy']``): only an integer other than 0 means the
adverts carry the position. Anything else (absent, ``None``, a string, a bool)
is read as "off", the safe side.

``publishable_position`` is the only place the position bound with Community
is rounded: exact when the radio broadcasts it anyway, rounded to about 1 km
otherwise. ``put_iata_bind`` is the only place it leaves for a bind, in the
body of Community's ``PUT /v1/me/iata`` (``lat``, ``lon``,
``position_precision``). The one other request that carries it is the IATA
suggestion (``meshloom_community.suggest_iata``, on the user's click), always
rounded to about 1 km by ``suggestion_position``: Community stores no code for
it, but the request, through Cloudflare, shows in its access logs with the
caller's IP address and that rounded position.

Automatic re-bind: when the radio reports a new ``self_info`` (``SELF_INFO``
after a connection, a ``set_coords`` or a policy change, all followed by an
``appstart``) and what ``publishable_position`` gives differs from what the
last successful bind sent, the bind is sent again with the stored IATA
(``rebind_current_iata``). Same IATA, so Community does not count it as an
IATA change. Debounced, one worker at a time, a few bounded retries, and only
while Community is on with an IATA: off, nothing leaves. The last bound
position is kept in memory, so the first ``self_info`` after a start re-binds
once (that also replaces a position an older version sent exactly).
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any, Literal, NamedTuple

from fastapi import HTTPException

from app.models import CommunityIataBindResult
from app.services.directory import is_valid_map_location

logger = logging.getLogger(__name__)


def advert_location_broadcast(info: Any) -> bool:
    """True only when the radio's adverts carry its position.

    MeshCore ``adv_loc_policy`` is 0 (no location) or a non-zero mode. A value
    that is not a plain integer (missing, ``None``, ``"x"``, ``True``) is not
    trusted and counts as off.
    """
    if not isinstance(info, dict):
        return False
    policy = info.get("adv_loc_policy")
    return isinstance(policy, int) and not isinstance(policy, bool) and policy != 0


PositionPrecision = Literal["exact", "rounded"]

# Two decimals is about 1.1 km of latitude: enough to place an ear in its town,
# not enough to find the house.
ROUNDED_DECIMALS = 2
EXACT_DECIMALS = 6


class PublishablePosition(NamedTuple):
    lat: float
    lon: float
    precision: PositionPrecision


def publishable_position(info: Any) -> PublishablePosition | None:
    """The position this radio may publish to Community, or None.

    The one place where it is rounded. A radio whose adverts carry its
    position (``advert_location_broadcast``) publishes it as heard on the air,
    to 6 decimals, ``exact``. Any other radio publishes it rounded to 2
    decimals (about 1 km), ``rounded``: Community never gets a precise position
    the radio does not broadcast itself. None when there is no usable position
    (absent, not finite, out of bounds, or MeshCore's (0, 0) "no GPS").
    """
    if not isinstance(info, dict):
        return None
    lat = _finite(info.get("adv_lat"))
    lon = _finite(info.get("adv_lon"))
    if lat is None or lon is None or not is_valid_map_location(lat, lon):
        return None
    precision: PositionPrecision = "exact" if advert_location_broadcast(info) else "rounded"
    decimals = EXACT_DECIMALS if precision == "exact" else ROUNDED_DECIMALS
    lat_out = round(lat, decimals)
    lon_out = round(lon, decimals)
    # A position within ~500 m of (0, 0) rounds onto the "no GPS" value.
    if not is_valid_map_location(lat_out, lon_out):
        return None
    return PublishablePosition(lat_out, lon_out, precision)


def suggestion_position(info: Any) -> tuple[float, float] | None:
    """The radio's position for the IATA suggestion, always rounded to 2 decimals.

    Whatever the advert location policy: Community's ``GET /v1/iata/suggest``
    only ever gets about 1 km (identity.md). None in the same cases as
    ``publishable_position``: nothing is asked without a position.
    """
    if not isinstance(info, dict):
        return None
    lat = _finite(info.get("adv_lat"))
    lon = _finite(info.get("adv_lon"))
    if lat is None or lon is None or not is_valid_map_location(lat, lon):
        return None
    lat_out = round(lat, ROUNDED_DECIMALS)
    lon_out = round(lon, ROUNDED_DECIMALS)
    if not is_valid_map_location(lat_out, lon_out):
        return None
    return lat_out, lon_out


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def radio_self_info() -> dict[str, Any] | None:
    """The connected radio's cached ``self_info``, or None."""
    from app.services.radio_runtime import radio_runtime

    try:
        meshcore = getattr(radio_runtime, "meshcore", None)
        info = getattr(meshcore, "self_info", None) if meshcore is not None else None
    except AttributeError:
        return None
    return info if isinstance(info, dict) else None


def iata_bind_body(iata: str, position: PublishablePosition | None) -> dict[str, Any]:
    """Body of Community's ``PUT /v1/me/iata``: the IATA, then the position if any.

    The position goes out with its precision, never alone, and nothing about
    the position goes out when there is none to publish.
    """
    body: dict[str, Any] = {"iata": iata.upper()}
    if position is not None:
        body["lat"] = position.lat
        body["lon"] = position.lon
        body["position_precision"] = position.precision
    return body


async def put_iata_bind(iata: str) -> CommunityIataBindResult:
    """Send ``PUT /v1/me/iata`` with this radio's publishable position.

    Goes through ``stats_json`` and so through the Community egress guard
    (403 while off, except inside a join).
    """
    from app.services.meshloom_community import stats_json

    info = radio_self_info()
    position = publishable_position(info)
    payload = await stats_json(
        "PUT",
        "/v1/me/iata",
        auth=True,
        json_body=iata_bind_body(iata, position),
        iata=iata,
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Community returned an unexpected body")
    result = CommunityIataBindResult.model_validate(payload)
    _note_bound(info)
    if position is not None:
        from app.services.meshloom_community import note_position_bound

        # Community has the position now: packets and the rest may resume.
        note_position_bound()
    return result


# --- Automatic re-bind -------------------------------------------------------

REBIND_DEBOUNCE_S = 5.0
# After a failed re-bind (Community down, rate limit, no radio key yet). A new
# radio change cuts a retry wait short.
REBIND_RETRY_DELAYS_S = (60.0, 300.0, 1800.0)
# Once those are spent, while Community still waits for a position (every
# Community feature is held back until a bind carrying it succeeds), keep
# trying at this pace instead of giving up until the next radio change.
REBIND_WAITING_RETRY_S = 1800.0

_UNKNOWN: Any = object()
# (radio public key, position) sent by the last successful bind, in memory.
_last_bound: Any = _UNKNOWN
_rebind_task: asyncio.Task[None] | None = None
_rebind_retry_wait = False

RebindOutcome = Literal["sent", "skipped", "failed"]


def _bound_key(info: Any) -> tuple[str, PublishablePosition | None]:
    raw = info.get("public_key") if isinstance(info, dict) else None
    pubkey = raw.strip().lower() if isinstance(raw, str) else ""
    return pubkey, publishable_position(info)


def _note_bound(info: Any) -> None:
    global _last_bound
    _last_bound = _bound_key(info)


def position_already_bound(info: Any) -> bool:
    """True when this radio's publishable position is what the last bind sent."""
    return publishable_position(info) is not None and _bound_key(info) == _last_bound


def note_radio_self_info(info: Any) -> None:
    """A fresh ``self_info``: schedule a re-bind if the published position moved.

    Cheap and synchronous (an event handler calls it). Nothing leaves from
    here: the worker checks the Community state, under the Community lock,
    before any request.
    """
    global _rebind_task
    from app.services.meshloom_community import note_radio_position

    # The waiting-for-position state follows every self_info, bound or not.
    note_radio_position(info)
    if not isinstance(info, dict) or _bound_key(info) == _last_bound:
        return
    if _rebind_task is not None and not _rebind_task.done():
        if not _rebind_retry_wait:
            # Debouncing or binding: the worker reads the latest self_info.
            return
        _rebind_task.cancel()
    from app.background_tasks import spawn

    _rebind_task = spawn(_rebind_worker(), name="community-position-rebind")


async def _rebind_worker() -> None:
    global _rebind_retry_wait
    failures = 0
    delay = REBIND_DEBOUNCE_S
    try:
        while True:
            await asyncio.sleep(delay)
            _rebind_retry_wait = False
            outcome = await _rebind_once()
            if outcome == "skipped":
                return
            if outcome == "sent":
                # The radio may have changed again while the bind was out.
                failures = 0
                delay = REBIND_DEBOUNCE_S
                continue
            if failures < len(REBIND_RETRY_DELAYS_S):
                delay = REBIND_RETRY_DELAYS_S[failures]
                failures += 1
            elif _waiting_for_position():
                delay = REBIND_WAITING_RETRY_S
            else:
                logger.warning("Community position re-bind gave up until the next radio change")
                return
            _rebind_retry_wait = True
            logger.info("Community position re-bind failed, retrying in %.0f s", delay)
    finally:
        _rebind_retry_wait = False


async def _rebind_once() -> RebindOutcome:
    """One re-bind attempt, if one is still needed."""
    from app.services.meshloom_community import rebind_current_iata
    from app.services.radio_ingest_gate import ingest_allowed

    info = radio_self_info()
    # No radio, or an identity not accepted yet: the end of the post-connect
    # setup notes self_info again.
    if info is None or not ingest_allowed() or _bound_key(info) == _last_bound:
        return "skipped"
    try:
        # Compared again under the Community lock: a join or an IATA change
        # that held it may have just sent this very position.
        result = await rebind_current_iata(put_iata_bind, still_needed=_rebind_still_needed)
    except HTTPException as exc:
        if exc.status_code == 403:
            return "skipped"  # opted out meanwhile
        logger.warning("Community position re-bind failed: %s", exc.detail)
        return "failed"
    except Exception:
        # Anything else (an unexpected Community body failing validation, a
        # bug): logged and retried like a failure, the worker never dies.
        logger.exception("Community position re-bind failed")
        return "failed"
    if result is None:
        return "skipped"  # Community off, no IATA, or already bound: nothing was sent
    logger.info("Community bind refreshed after a radio position or policy change")
    return "sent"


def _waiting_for_position() -> bool:
    from app.services.meshloom_community import waiting_for_position

    return waiting_for_position()


def _rebind_still_needed() -> bool:
    info = radio_self_info()
    return info is not None and _bound_key(info) != _last_bound


def forget_last_bind() -> None:
    """After an opt-out: Community refuses this key until a bind lifts it.

    The next ``self_info`` (``PATCH {enabled: true}`` notes one) then re-binds
    even when the position did not move.
    """
    global _last_bound
    _last_bound = _UNKNOWN


def reset_position_rebind_for_tests() -> None:
    global _last_bound, _rebind_task, _rebind_retry_wait
    if _rebind_task is not None and not _rebind_task.done():
        _rebind_task.cancel()
    _rebind_task = None
    _rebind_retry_wait = False
    _last_bound = _UNKNOWN
