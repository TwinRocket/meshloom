"""frontend/AGENTS.md only cites UI strings that exist, in English and French (MSHL-449).

The agent doc describes some dialogs by the strings they render (the Remote line and
enable dialog, the Links consent). When a copy pass removes a string, the doc that
cites it is stale: this test fails first. Keys are the backticked ``settings.*`` and
``errors.*`` paths; a path ending on a subtree (``settings.remote.badge``) counts too.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
LOCALES = ROOT / "frontend" / "src" / "i18n" / "locales"
AGENTS = ROOT / "frontend" / "AGENTS.md"
KEY = re.compile(r"`((?:settings|errors)\.[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)*)`")


def _merge(into: dict, other: dict) -> None:
    for key, value in other.items():
        if isinstance(value, dict) and isinstance(into.get(key), dict):
            _merge(into[key], value)
        else:
            into[key] = value


def _strings(language: str) -> dict:
    tree: dict = json.loads((LOCALES / f"{language}.json").read_text(encoding="utf-8"))
    for path in sorted((LOCALES / "slices").glob(f"*.{language}.json")):
        _merge(tree, json.loads(path.read_text(encoding="utf-8")))
    return tree


def _resolves(tree: dict, key: str) -> bool:
    node: object = tree
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def test_the_doc_cites_the_dialog_strings() -> None:
    cited = set(KEY.findall(AGENTS.read_text(encoding="utf-8")))
    for key in (
        "settings.remote.about.secure",
        "settings.remote.enable.help",
        "settings.share.consent.intro",
        "settings.share.consent.accept",
    ):
        assert key in cited, f"frontend/AGENTS.md no longer cites {key}"


@pytest.mark.parametrize("language", ["en", "fr"])
def test_every_cited_string_exists(language: str) -> None:
    tree = _strings(language)
    cited = sorted(set(KEY.findall(AGENTS.read_text(encoding="utf-8"))))
    missing = [key for key in cited if not _resolves(tree, key)]
    assert not missing, f"frontend/AGENTS.md cites strings missing in {language}: {missing}"


@pytest.mark.parametrize(
    "removed",
    [
        "settings.remote.about.privacyEdge",
        "settings.remote.about.neverLocal",
        "settings.share.consent.retention",
    ],
)
def test_a_removed_string_would_fail(removed: str) -> None:
    # The removed MSHL-449 strings must not resolve, so citing them would fail above.
    assert not _resolves(_strings("en"), removed)
