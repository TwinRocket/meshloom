"""Every user doc has frontmatter that a real YAML parser accepts.

The website builds its docs from the latest release tag and Astro's content sync
fails on invalid YAML, which breaks every build of meshloom.app. The usual trap is
an unquoted `description:` whose value contains a colon followed by a space. A published
tag cannot be rewritten, so catch it here before the release.
"""

from pathlib import Path

import pytest
import yaml

DOCS = Path(__file__).resolve().parents[1] / "docs" / "user"
FILES = sorted(DOCS.rglob("*.md"))


def _frontmatter(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return None
    return parts[0].removeprefix("---")


def test_docs_are_found():
    assert FILES, f"no markdown under {DOCS}"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.relative_to(DOCS).as_posix())
def test_frontmatter_is_valid_yaml(path: Path):
    block = _frontmatter(path.read_text(encoding="utf-8"))
    if block is None:
        pytest.skip("no frontmatter")
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as exc:
        pytest.fail(f"{path.relative_to(DOCS.parent.parent)}: invalid YAML frontmatter: {exc}")
    assert isinstance(data, dict), f"{path}: frontmatter is not a mapping"
    assert isinstance(data.get("title"), str) and data["title"], f"{path}: missing title"
    description = data.get("description")
    assert description is None or isinstance(description, str), (
        f"{path}: description is not a string"
    )
