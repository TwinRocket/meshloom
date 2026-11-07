"""The shared URL table (also read by the frontend composer test) matches _checked_url."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.routers.share import _checked_url
from app.services.share import ShareError

_CASES = json.loads((Path(__file__).parent / "fixtures" / "share_url_cases.json").read_text())[
    "cases"
]


@pytest.mark.parametrize("case", _CASES, ids=[c["url"] for c in _CASES])
def test_checked_url_matches_shared_cases(case: dict) -> None:
    if case["ok"]:
        assert _checked_url(case["url"]) == case["url"]
    else:
        with pytest.raises(ShareError):
            _checked_url(case["url"])
