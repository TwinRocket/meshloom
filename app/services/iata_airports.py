"""The offline airport list: IATA code -> position and type, no request ever.

Loaded from ``app/data/iata_airports.json``, a copy of the snapshot
meshloom-community uses (OurAirports + OpenTravelData, plus its metropolitan
area codes), rebuilt by ``scripts/data/refresh_iata_airports.py``.

- ``airport_position(code)`` places a code (the Live regions, the location
  picker): any entry, a heliport or a metro area (PAR, at the mean of its
  airports) included.
- ``nearest_airport_iata(lat, lon)`` is the offline fallback of Community's
  ``nearest_airport`` (``GET /v1/iata/suggest``) and mirrors its rule
  (identity.md, § *IATA suggestion*): no ``heliport``, no ``seaplane_base``,
  no metropolitan area code, no placeholder (``XXX``, ``LOC``, ``AAA``); the
  nearest by haversine, equal distances to the smaller code. Change both
  together.
"""

from __future__ import annotations

import json
import logging
import math
from functools import cache
from pathlib import Path
from typing import NamedTuple

logger = logging.getLogger(__name__)

AIRPORTS_FILE = Path(__file__).resolve().parent.parent / "data" / "iata_airports.json"
# Community's PLACEHOLDER_IATAS (api/iata.py). AAA is also a real airport of the snapshot.
PLACEHOLDER_IATAS = frozenset({"XXX", "LOC", "AAA"})
METRO_AREA = "metro_area"
# Community's NOT_SUGGESTED_KINDS (api/iata_suggest.py).
NOT_SUGGESTED_KINDS = frozenset({"heliport", "seaplane_base", METRO_AREA})
_EARTH_RADIUS_KM = 6371.0


class Airport(NamedTuple):
    iata: str
    lat: float
    lon: float
    kind: str


@cache
def airports() -> dict[str, Airport]:
    """Every entry of the bundled list, by code. Empty (and logged) if it cannot be read."""
    try:
        payload = json.loads(AIRPORTS_FILE.read_text(encoding="utf-8"))
        kinds = payload["kinds"]
        return {
            code: Airport(code, float(lat), float(lon), kinds[kind])
            for code, (lat, lon, kind) in payload["airports"].items()
        }
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        logger.warning("Could not read the bundled airport list %s", AIRPORTS_FILE, exc_info=True)
        return {}


@cache
def _suggestible() -> tuple[Airport, ...]:
    return tuple(
        airport
        for airport in airports().values()
        if airport.kind not in NOT_SUGGESTED_KINDS and airport.iata not in PLACEHOLDER_IATAS
    )


def airport_position(code: str) -> tuple[float, float] | None:
    """``(lat, lon)`` of a code of the list, or None."""
    airport = airports().get((code or "").strip().upper())
    return (airport.lat, airport.lon) if airport is not None else None


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return _EARTH_RADIUS_KM * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def nearest_airport_iata(lat: float, lon: float) -> str | None:
    """The nearest suggestible airport, Community's rule; equal distances to the smaller code."""
    best: tuple[float, str] | None = None
    for airport in _suggestible():
        candidate = (haversine_km(lat, lon, airport.lat, airport.lon), airport.iata)
        if best is None or candidate < best:
            best = candidate
    return best[1] if best is not None else None
