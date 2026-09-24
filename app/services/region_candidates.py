"""Propose region names, and check them against traffic already heard.

A region name is a convention agreed between repeater operators, not something
the protocol carries: the code on a packet is a MAC keyed by the region name and
computed over the payload, so a packet can never be asked which region it belongs
to. Asking the repeaters is one answer, and it depends on the radio reaching them.

This is the other one. A proposed name can be tested against packets already on
disk, which is deterministic, costs no airtime, and answers in a second. It
cannot invent a name nobody has mentioned, so the candidates come from what this
instance already knows: the scopes it is configured with, and the names of the
repeaters it has heard.
"""

from __future__ import annotations

import re

from app.decoder import parse_packet
from app.region_resolver import RegionSample, count_region_matches
from app.region_scope import is_unscoped

# Mirrors firmware ``RegionMap::is_name_char``: no spaces, and nothing below 'A'
# except digits and these three. A name the firmware would refuse is not worth
# testing, and would be refused again on its way into the settings.
_NAME_CHARS = re.compile(r"^[-$#0-9A-Za-z\x80-\xff]+$")

MAX_NAME_LENGTH = 32

_MIN_WORD_LENGTH = 4

# Words that describe the box rather than the place it stands on.
_GENERIC_WORDS = {
    "base",
    "gateway",
    "mesh",
    "meshcore",
    "node",
    "relai",
    "relais",
    "relay",
    "repeater",
    "repeteur",
    "room",
    "server",
    "station",
    "test",
}


def is_testable_name(name: str) -> bool:
    """Whether a name is worth spending a MAC on."""
    candidate = (name or "").strip()
    if not candidate or is_unscoped(candidate):
        return False
    if len(candidate) > MAX_NAME_LENGTH:
        return False
    return bool(_NAME_CHARS.match(candidate))


def _from_repeater_name(name: str) -> list[str]:
    """The region names a repeater called ``name`` might plausibly flood.

    Operators routinely name a repeater after the place it stands on, and the
    region after the same place. This is a guess, which is why every candidate is
    then checked against real traffic rather than offered as an answer.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return []
    words = [word for word in re.split(r"[\s_/()]+", cleaned) if word]
    out = [cleaned.replace(" ", "-")]
    # Only words that could name a place. An article or the word "relay" costs a
    # MAC per stored packet and can only ever match by accident, which would be
    # worse than not offering it.
    out.extend(
        word
        for word in words
        if len(word) >= _MIN_WORD_LENGTH and word.casefold() not in _GENERIC_WORDS
    )
    return [candidate for candidate in out if is_testable_name(candidate)]


def build_candidates(
    *,
    known_regions: list[str],
    flood_scope: str | None,
    channel_scopes: list[str],
    repeater_names: list[str],
    extra: list[str] | None = None,
) -> list[str]:
    """Names worth testing, most likely first, without duplicates.

    Ordered so that what the operator typed is tested before anything guessed on
    their behalf, and what they already configured before names scraped off
    repeaters.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        candidate = (name or "").strip()
        if not is_testable_name(candidate):
            return
        key = candidate.casefold()
        if key in seen:
            return
        seen.add(key)
        ordered.append(candidate)

    for name in extra or []:
        add(name)
    for name in known_regions:
        add(name)
    add(flood_scope or "")
    for name in channel_scopes:
        add(name)
    for name in repeater_names:
        for candidate in _from_repeater_name(name):
            add(candidate)
    return ordered


def samples_from_packets(packets: list[tuple[bytes, int]]) -> list[RegionSample]:
    """Keep only the packets a region name could possibly explain.

    Most traffic is unscoped and carries no transport code at all; testing a name
    against it would be arithmetic with no answer in it.
    """
    samples: list[RegionSample] = []
    for data, timestamp in packets:
        info = parse_packet(data)
        if info is None or info.transport_codes is None:
            continue
        samples.append(
            RegionSample(
                payload_type=int(info.payload_type),
                payload=info.payload,
                transport_code=info.transport_codes[0],
                timestamp=timestamp,
            )
        )
    return samples


def match_candidates(candidates: list[str], samples: list[RegionSample]) -> list[tuple[str, int]]:
    """Candidates and how much traffic each explains, best first.

    Zero is reported rather than hidden: "this name matches nothing I have heard"
    is a useful answer, and quite different from "this name is wrong".
    """
    counts = count_region_matches(candidates, samples)
    return sorted(counts.items(), key=lambda pair: (-pair[1], pair[0].casefold()))
