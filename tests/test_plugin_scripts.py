"""UTS #39 "Highly Restrictive" word check and its embedded Unicode tables."""

import json
from pathlib import Path

import pytest

from app.plugins.scripts import is_restricted_word, mixed_script_word, script_set

DATA = Path(__file__).resolve().parent.parent / "app" / "data" / "unicode_scripts.json"


@pytest.mark.parametrize(
    "text",
    [
        "\u30e9\u30fc\u30e1\u30f3",  # ラーメン
        "\u30b5\u30fc\u30d0\u30fc",  # サーバー
        "\u4f50\u3005\u6728",  # 佐々木
        "\u6771\u4eac\u30bf\u30ef\u30fc",  # 東京タワー
        "\ud55c\uad6d\uc5b4",  # 한국어
        "\u4e2d\u6587",  # 中文
        "Tokyo\u6771\u4eac",
        "Weather\u30e9\u30fc\u30e1\u30f3",
        "\u3042\u3044\u3046 \u30a2\u30a4\u30a6 \u6f22\u5b57 Latin",
        "Latin\ud55c\uad6d\uc5b4 \u6f22\u5b57",
        "\u3105\u3106\u3107 \u4e2d\u6587 Latin",  # Hanb: Bopomofo + Han + Latin
        "Pogoda \u041f\u043e\u0433\u043e\u0434\u0430",  # separate words, one script each
        "M\u00e9t\u00e9o Stra\u00dfe \u0130stanbul",
        "e\u0301clair",
        "\uff2d\uff45\uff53\uff48\uff4c\uff4f\uff4f\uff4d",  # fullwidth, folded by NFKC
        "\u067e\u06cc\u0634\u200c\u0628\u06cc\u0646\u06cc",
        "\u0915\u094d\u200d\u0937",
    ],
)
def test_allowed(text):
    assert not mixed_script_word(text)


@pytest.mark.parametrize(
    "text",
    [
        "Me\u0455hloom",  # Cyrillic dze
        "M\u03b5shloom",  # Greek epsilon
        "Meshl\u0585om",  # Armenian oh
        "Meshlo\u13a0m",  # Cherokee
        "\u03bcController",
        "e\u0301\u0455hloom",  # a combining mark must not split the word
        "\u6771\u4eac\u30e9\u30fc\u30e1\u30f3\ud55c",  # Han + kana + Hangul
        "\u3042\u3105",  # Hiragana + Bopomofo
        "\u041f\u043e\u0433\u043e\u0434a",  # Cyrillic with a Latin a
    ],
)
def test_refused(text):
    assert mixed_script_word(text)


def test_the_check_is_per_word_not_per_string():
    # Deliberate (docs/dev/plugins.md): words in different scripts may sit side by side,
    # a look-alike inside one word may not. Full UTS #39 would refuse the first two.
    assert not mixed_script_word("Mesh \u0421\u0435\u0442\u044c")  # Mesh Сеть
    assert not mixed_script_word("\u041f\u043e\u0433\u043e\u0434\u0430 GPS")  # Погода GPS
    assert mixed_script_word("Mesh Me\u0455hloom")  # Cyrillic dze inside a Latin word


def test_script_extensions_semantics():
    assert script_set("\u30fc") == {"Hiragana", "Katakana"}
    assert script_set("a") == {"Latin"}
    assert script_set("1") is None  # Common
    assert {"Latin", "Cyrillic", "Greek"} <= script_set("\u0301")  # Inherited, with extensions
    assert script_set("\u200d") is None
    assert is_restricted_word("")


def test_table_is_the_generated_one():
    data = json.loads(DATA.read_text("utf-8"))
    assert data["unicode_version"] == "16.0.0"
    assert {"Latin", "Han", "Hiragana", "Katakana", "Hangul", "Bopomofo"} <= set(data["scripts"])
    assert "Common" not in data["scripts"] and "Inherited" not in data["scripts"]
    ranges = data["ranges"]
    assert ranges == sorted(ranges)
    assert all(a[1] < b[0] for a, b in zip(ranges, ranges[1:], strict=False))
