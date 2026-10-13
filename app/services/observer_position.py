"""What this radio says about its own position, and what Community gets of it.

``advert_location_broadcast`` reads the radio's advert location policy
(``self_info['adv_loc_policy']``): only an integer other than 0 means the
adverts carry the position. Anything else (absent, ``None``, a string, a bool)
is read as "off", the safe side.

``publishable_position`` is the only place the position sent to Community is
rounded: exact when the radio broadcasts it anyway, rounded to about 1 km
otherwise. ``put_iata_bind`` is the only place it leaves, in the body of
Community's ``PUT /v1/me/iata`` (``lat``, ``lon``, ``position_precision``).
"""

from __future__ import annotations

import math
from typing import Any, Literal, NamedTuple

from fastapi import HTTPException

from app.models import CommunityIataBindResult
from app.services.directory import is_valid_map_location


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

    position = publishable_position(radio_self_info())
    payload = await stats_json(
        "PUT",
        "/v1/me/iata",
        auth=True,
        json_body=iata_bind_body(iata, position),
        iata=iata,
    )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="Community returned an unexpected body")
    return CommunityIataBindResult.model_validate(payload)
