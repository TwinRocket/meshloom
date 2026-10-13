"""What this radio says about its own position.

``advert_location_broadcast`` reads the radio's advert location policy
(``self_info['adv_loc_policy']``): only an integer other than 0 means the
adverts carry the position. Anything else (absent, ``None``, a string, a bool)
is read as "off", the safe side.
"""

from __future__ import annotations

from typing import Any


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
