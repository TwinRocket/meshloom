"""Settings paths to the Connection page, in the user docs and the UI strings.

MSHL-427 moved how people sign in (session lifetime, client addresses, login-free on
the local network, address bans) from Settings → Users & Access to the tabs of
Settings → Connection (``frontend/src/components/settings/SettingsConnectionSection.tsx``).
A doc or a string that still sends the reader to the old place is wrong, in either
language. Labels come from the locale slices, so a renamed tab is followed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
USER_DOCS = ROOT / "docs" / "user"
SLICES = ROOT / "frontend" / "src" / "i18n" / "locales" / "slices"
SETTINGS_WORD = {"en": "Settings", "fr": "Réglages"}
# Panels that now live on Settings → Connection, by their i18n key.
MOVED_PANELS = ("ipBan", "trustedNetworks", "clientAddresses", "sessionLifetime")


def _merge(into: dict, other: dict) -> None:
    for key, value in other.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            _merge(into[key], value)
        else:
            into[key] = value


def _locale(lang: str) -> dict:
    merged: dict = {}
    for path in sorted(SLICES.glob(f"*.{lang}.json")):
        _merge(merged, json.loads(path.read_text(encoding="utf-8")))
    return merged


def _strings(node, trail=()):
    if isinstance(node, dict):
        for key, value in node.items():
            yield from _strings(value, (*trail, key))
    elif isinstance(node, str):
        yield ".".join(trail), node


def _norm(text: str) -> str:
    return text.replace("’", "'")


def _texts(lang: str) -> list[tuple[str, str]]:
    texts = [
        (str(page.relative_to(ROOT)), page.read_text(encoding="utf-8"))
        for page in sorted((USER_DOCS / lang).rglob("*.md"))
    ]
    texts += [(f"i18n:{key}", value) for key, value in _strings(_locale(lang))]
    return [(where, _norm(text)) for where, text in texts]


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_no_path_sends_a_moved_panel_to_users_and_access(lang: str) -> None:
    locale = _locale(lang)
    access = _norm(locale["settingsNav"]["access"])
    titles = [_norm(locale["settings"]["access"][panel]["title"]) for panel in MOVED_PANELS]
    stale = [f"{SETTINGS_WORD[lang]} → {access} → {title}" for title in titles]
    found = [(where, path) for where, text in _texts(lang) for path in stale if path in text]
    assert not found, found


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_connection_paths_name_a_real_tab(lang: str) -> None:
    locale = _locale(lang)
    connection = _norm(locale["settingsNav"]["connection"])
    tabs = {_norm(label) for label in locale["settings"]["connection"]["tabs"].values()}
    pattern = re.compile(
        re.escape(f"{SETTINGS_WORD[lang]} → {connection} → ") + r"([^*\n.:;,()→]+)"
    )
    seen = []
    wrong = []
    for where, text in _texts(lang):
        for match in pattern.finditer(text):
            label = match.group(1).strip()
            seen.append(label)
            if label not in tabs:
                wrong.append((where, label))
    assert seen, "no Settings → Connection path found: the pattern no longer matches"
    assert not wrong, wrong


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_the_still_signed_in_note_points_to_the_local_network_tab(lang: str) -> None:
    locale = _locale(lang)
    note = _norm(locale["auth"]["userMenu"]["stillSignedInNote"])
    connection = _norm(locale["settingsNav"]["connection"])
    lan = _norm(locale["settings"]["connection"]["tabs"]["lan"])
    assert f"{SETTINGS_WORD[lang]} → {connection} → {lan}" in note
