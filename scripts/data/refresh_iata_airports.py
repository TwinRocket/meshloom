#!/usr/bin/env python3
"""Rebuild ``app/data/iata_airports.json`` from meshloom-community's airport snapshot.

Meshloom keeps an offline copy of the airport list Community uses, for two things
that must work without any request:

- the fallback of the IATA suggestion (``nearest_airport_iata`` in
  ``app/services/iata_airports.py``), when Community's ``GET /v1/iata/suggest``
  cannot be reached. It mirrors Community's exclusion rule (identity.md,
  § *IATA suggestion*), so it needs each entry's type;
- placing a code on a map (the Live regions, the location picker), which once
  asked an outside airport search.

Source, read from a git ref of a meshloom-community checkout (default
``origin/main``), never from a working tree, so the same ref gives the same file:

- ``api/airports/ourairports_snapshot.csv``: ``iata_code,latitude_deg,longitude_deg,type,source``.
  A duplicate code keeps the larger airport (``large_airport`` first), as
  Community's ``load_airports`` does;
- ``api/airports/metro_codes.csv``: metropolitan area codes (PAR, LON...), each placed
  at the plain mean of its member airports present in the snapshot, type
  ``metro_area``, as Community's ``airports()`` does. A code that is a real airport
  of the snapshot is skipped (the airport wins).

Output: one entry per code, ``"CDG":[lat,lon,k]``, where ``k`` indexes ``kinds``.
Sorted by code, one entry per line, so a refresh shows as a readable diff.

Usage::

    uv run python scripts/data/refresh_iata_airports.py \
        --community ../meshloom-community [--ref origin/main]

Data: OurAirports (public domain) and OpenTravelData (CC BY 4.0), as merged by
meshloom-community's ``api/airports/refresh_snapshot.py``. The attribution ships
next to the file (``app/data/NOTICE-iata-airports.md``) and in ``LICENSES.md``
(``scripts/build/collect_licenses.sh``, "Bundled Data").
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "app" / "data" / "iata_airports.json"
SNAPSHOT_PATH = "api/airports/ourairports_snapshot.csv"
METRO_PATH = "api/airports/metro_codes.csv"
IATA_RE = re.compile(r"^[A-Z]{3}$")
# Community's PLACEHOLDER_IATAS (api/iata.py): never a metro area code.
PLACEHOLDER_IATAS = frozenset({"XXX", "LOC", "AAA"})
TYPE_PREFERENCE = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}
METRO_AREA = "metro_area"


def _git(community: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(community), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def load_snapshot(text: str) -> dict[str, tuple[float, float, str]]:
    by_code: dict[str, tuple[float, float, str]] = {}
    for row in csv.DictReader(io.StringIO(text)):
        code = (row.get("iata_code") or "").strip().upper()
        if len(code) != 3:
            continue
        try:
            lat = float(row["latitude_deg"])
            lon = float(row["longitude_deg"])
        except (KeyError, TypeError, ValueError):
            continue
        kind = (row.get("type") or "").strip() or "unknown"
        existing = by_code.get(code)
        if existing is None or TYPE_PREFERENCE.get(kind, 99) < TYPE_PREFERENCE.get(existing[2], 99):
            by_code[code] = (lat, lon, kind)
    return by_code


def load_metro(
    text: str, snapshot: dict[str, tuple[float, float, str]]
) -> dict[str, tuple[float, float, str]]:
    lines = [line for line in text.splitlines(keepends=True) if not line.lstrip().startswith("#")]
    out: dict[str, tuple[float, float, str]] = {}
    for row in csv.DictReader(lines):
        code = (row.get("code") or "").strip().upper()
        if not IATA_RE.fullmatch(code) or code in PLACEHOLDER_IATAS or code in snapshot:
            continue
        members = [
            snapshot[member]
            for member in dict.fromkeys((row.get("members") or "").upper().split())
            if member in snapshot
        ]
        if members:
            lat = sum(m[0] for m in members) / len(members)
            lon = sum(m[1] for m in members) / len(members)
            out[code] = (lat, lon, METRO_AREA)
    return out


def build(snapshot_text: str, metro_text: str, commit: str) -> str:
    snapshot = load_snapshot(snapshot_text)
    entries = {**snapshot, **load_metro(metro_text, snapshot)}
    kinds = sorted({kind for _lat, _lon, kind in entries.values()})
    index = {kind: i for i, kind in enumerate(kinds)}
    lines = [
        "{",
        f'"source":{json.dumps(f"meshloom-community {SNAPSHOT_PATH} + {METRO_PATH}")},',
        f'"community_commit":{json.dumps(commit)},',
        '"license":"OurAirports (public domain) and OpenTravelData (CC BY 4.0, '
        'https://github.com/opentraveldata/opentraveldata), modified: merged and recomputed; '
        'see app/data/NOTICE-iata-airports.md",',
        f'"kinds":{json.dumps(kinds)},',
        '"airports":{',
    ]
    codes = sorted(entries)
    for n, code in enumerate(codes):
        lat, lon, kind = entries[code]
        comma = "," if n < len(codes) - 1 else ""
        lines.append(f'"{code}":[{round(lat, 6)},{round(lon, 6)},{index[kind]}]{comma}')
    lines += ["}", "}"]
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--community", type=Path, required=True, help="meshloom-community checkout")
    parser.add_argument(
        "--ref", default="origin/main", help="git ref to read (default origin/main)"
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    commit = _git(args.community, "rev-parse", f"{args.ref}^{{commit}}").strip()
    snapshot_text = _git(args.community, "show", f"{commit}:{SNAPSHOT_PATH}")
    metro_text = _git(args.community, "show", f"{commit}:{METRO_PATH}")
    args.output.write_text(build(snapshot_text, metro_text, commit), encoding="utf-8")
    print(f"Wrote {args.output} from meshloom-community {commit}")


if __name__ == "__main__":
    main()
