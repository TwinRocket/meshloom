"""Every transport renders the existing events exactly as before the renderer move.

``tests/fixtures/notifications_golden.json`` was generated from the code that
preceded ``app/notifications/render.py`` (``scripts/dev/gen_notifications_golden.py``).
A failure here is a behaviour change: fix the code, not the fixture.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "notifications_golden.json"


def _generator():
    spec = importlib.util.spec_from_file_location(
        "gen_notifications_golden", ROOT / "scripts" / "dev" / "gen_notifications_golden.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_golden_output_is_unchanged():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    actual = json.loads(json.dumps(_generator().build_golden()))
    assert actual.keys() == expected.keys()
    for name in expected:
        assert actual[name] == expected[name], name


def test_golden_covers_both_languages_and_every_policy():
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for name, langs in expected.items():
        assert {"fr", "en"} <= set(langs), name
        assert set(langs["fr"]["text"]) == {
            f"{p}|{m}" for p in ("always", "public_only", "never") for m in ("plain", "md")
        }


@pytest.mark.parametrize("name", ["telemetry_battery", "message_dm"])
def test_golden_is_not_vacuous(name):
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert expected[name]["fr"]["push"]["title"]
    assert expected[name]["fr"]["email_html"].startswith("<!DOCTYPE html>")
