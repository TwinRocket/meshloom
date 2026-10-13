"""What this radio says about its own position, and what Community gets of it.

``advert_location_broadcast`` reads the radio's advert location policy
(``self_info['adv_loc_policy']``): only an integer other than 0 means the
adverts carry the position. Anything else (absent, ``None``, a string, a bool)
is read as "off", the safe side.

``publishable_position`` is the only place the position sent to Community is
rounded: exact when the radio broadcasts it anyway, rounded to about 1 km
otherwise.
"""

from __future__ import annotations

import math
from typing import Any, Literal, NamedTuple

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
