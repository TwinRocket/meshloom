"""Front matter of docs/user stays valid YAML for the site build.

meshloom.app (Astro) parses it at build time: an unquoted ``title`` or ``description`` that holds
``": "`` is a YAML mapping error and fails the whole site build once the docs are pulled from a tag.
"""

import re
from pathlib import Path

import pytest

USER = Path(__file__).resolve().parent.parent / "docs" / "user"
PAGES = sorted(USER.rglob("*.md"))
FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: str(p.relative_to(USER)))
def test_front_matter_scalars_are_valid_yaml(page: Path) -> None:
    match = FRONT.match(page.read_text(encoding="utf-8"))
    assert match, "missing front matter"
    keys = set()
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        keys.add(key)
        value = value.strip()
        if key in ("title", "description") and not value.startswith(('"', "'")):
            assert ": " not in value and " #" not in value, f"quote this value: {line[:80]}"
    assert {"title", "description", "level", "order"} <= keys
