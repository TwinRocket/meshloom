"""The public backup page keeps the anchors the flasher links to.

The flasher (meshloom.app) cannot know a user's Meshloom address, so it links to
docs/user/{en,fr}/backup.md. Its anchors are heading slugs: renaming one of these
headings breaks the flasher's link. Other headings may be added.
"""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).resolve().parents[1] / "docs" / "user"

ANCHOR_HEADINGS = {
    "en": [
        "## Before reflashing",
        "## Where the backup is",
        "## Restore after reflashing",
        "## Encryption",
        "## What the backup contains",
    ],
    "fr": [
        "## Avant de reflasher",
        "## Trouver la sauvegarde",
        "## Restaurer la radio",
        "## Chiffrement",
        "## Contenu de la sauvegarde",
    ],
}


@pytest.mark.parametrize("language", ["en", "fr"])
def test_frontmatter(language):
    text = (DOCS / language / "backup.md").read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
    assert match, "frontmatter missing"
    keys = {line.split(":", 1)[0] for line in match.group(1).splitlines() if ":" in line}
    assert {"title", "description", "level", "order"} <= keys


@pytest.mark.parametrize("language", ["en", "fr"])
def test_anchor_headings_exist_once(language):
    lines = (DOCS / language / "backup.md").read_text(encoding="utf-8").splitlines()
    for heading in ANCHOR_HEADINGS[language]:
        assert lines.count(heading) == 1, heading


def test_french_anchor_headings_have_no_diacritics():
    # Plain ASCII slugs: the same anchor whatever the slugger does with accents.
    for heading in ANCHOR_HEADINGS["fr"]:
        assert heading.isascii(), heading
