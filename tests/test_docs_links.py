"""Internal links of docs/user: right language prefix, existing page.

EN pages link ``/en/docs/...``; FR pages link ``/docs/...`` (the site serves FR
at the root). A FR link written ``/fr/docs/...`` broke every FR link of a page once.
"""

import re
from pathlib import Path

import pytest

USER = Path(__file__).resolve().parent.parent / "docs" / "user"
LINK = re.compile(r"\]\((/(?:en/|fr/)?docs/[^)#\s]*)")


def _pages(lang: str) -> list[Path]:
    return sorted((USER / lang).rglob("*.md"))


def _target(path: str, lang: str) -> Path:
    rel = re.sub(r"^/(en/|fr/)?docs/?", "", path).strip("/")
    base = USER / lang / rel
    return base / "index.md" if base.is_dir() else base.with_suffix(".md")


@pytest.mark.parametrize("page", _pages("fr"), ids=lambda p: str(p.relative_to(USER)))
def test_fr_links_have_no_language_prefix(page: Path) -> None:
    bad = [m for m in LINK.findall(page.read_text(encoding="utf-8")) if m.startswith("/fr/")]
    assert not bad, bad


@pytest.mark.parametrize("page", _pages("en"), ids=lambda p: str(p.relative_to(USER)))
def test_en_links_use_en_prefix(page: Path) -> None:
    bad = [m for m in LINK.findall(page.read_text(encoding="utf-8")) if not m.startswith("/en/")]
    assert not bad, bad


@pytest.mark.parametrize("lang", ["en", "fr"])
def test_internal_links_resolve(lang: str) -> None:
    missing = []
    for page in _pages(lang):
        for link in LINK.findall(page.read_text(encoding="utf-8")):
            if not _target(link, lang).exists():
                missing.append(f"{page.relative_to(USER)}: {link}")
    assert not missing, missing
