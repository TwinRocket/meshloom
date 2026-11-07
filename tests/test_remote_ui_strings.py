"""Settings → Remote access translates every Remote error code and link state (MSHL-82).

The UI shows ``errors.<code>`` for a coded error and ``settings.remote.badge.<state>`` /
``settings.remote.badgeHelp.<state>`` for a ``link_status``. A code or state added on
the backend without its French and English strings would render as a raw key: this test
fails first. It also pins the frontend's copy of ``LINK_STATES`` (``remoteTypes.ts``).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.remote import REMOTE_ERRORS
from app.services.remote_relay import _REFUSALS, LINK_STATES

FRONTEND = Path(__file__).parent.parent / "frontend" / "src"
SLICES = FRONTEND / "i18n" / "locales" / "slices"
REMOTE_TYPES_TS = FRONTEND / "components" / "settings" / "remote" / "remoteTypes.ts"


def _slice(language: str) -> dict:
    return json.loads((SLICES / f"remote.{language}.json").read_text(encoding="utf-8"))


def test_the_frontend_link_states_match_the_relay_client() -> None:
    source = REMOTE_TYPES_TS.read_text(encoding="utf-8")
    match = re.search(r"export const REMOTE_LINK_STATES = \[(.*?)\] as const;", source, re.S)
    assert match, "REMOTE_LINK_STATES not found in remoteTypes.ts"
    assert tuple(re.findall(r"'([^']+)'", match.group(1))) == LINK_STATES


@pytest.mark.parametrize("language", ["en", "fr"])
def test_every_remote_error_code_is_translated(language: str) -> None:
    errors = _slice(language)["errors"]
    for code in [*REMOTE_ERRORS, *_REFUSALS]:
        assert errors.get(code, "").strip(), f"errors.{code} missing in {language}"


@pytest.mark.parametrize("language", ["en", "fr"])
def test_every_link_state_has_a_label_and_a_help(language: str) -> None:
    remote = _slice(language)["settings"]["remote"]
    for state in [*LINK_STATES, "off", "unregistered", "unknown"]:
        assert remote["badge"].get(state, "").strip(), f"badge.{state} missing in {language}"
        assert remote["badgeHelp"].get(state, "").strip(), f"badgeHelp.{state} missing"
