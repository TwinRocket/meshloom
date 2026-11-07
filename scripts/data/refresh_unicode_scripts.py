#!/usr/bin/env python3
"""Rebuild ``app/data/unicode_scripts.json`` from the Unicode Character Database.

    uv run python scripts/data/refresh_unicode_scripts.py              # download, pinned version
    uv run python scripts/data/refresh_unicode_scripts.py --from-dir D # D holds the three .txt files

The standard library has no Script or Script_Extensions property, and the mixed-script
rule for plugin names (UTS #39, ``app/plugins/scripts.py``) needs both, with the same
answer on every Python version. The table is generated from ``Scripts.txt``,
``ScriptExtensions.txt`` and ``PropertyValueAliases.txt`` of the pinned UCD version and
keeps only what the rule uses: ranges of characters whose script is neither Common nor
Inherited, and the Script_Extensions overrides. Do not edit the JSON by hand.
"""

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

UNICODE_VERSION = "16.0.0"
BASE = f"https://www.unicode.org/Public/{UNICODE_VERSION}/ucd/"
FILES = ("Scripts.txt", "ScriptExtensions.txt", "PropertyValueAliases.txt")
OUT = Path(__file__).resolve().parents[2] / "app" / "data" / "unicode_scripts.json"
IGNORED = {"Common", "Inherited", "Unknown"}


def _lines(text: str):
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            yield [part.strip() for part in line.split(";")]


def _range(token: str) -> tuple[int, int]:
    lo, _, hi = token.partition("..")
    return int(lo, 16), int(hi or lo, 16)


def build(texts: dict[str, str]) -> dict:
    short_to_long: dict[str, str] = {}
    for fields in _lines(texts["PropertyValueAliases.txt"]):
        if fields[0] == "sc":
            short_to_long[fields[1]] = fields[2].replace("_", " ")
    names: list[str] = []

    def idx(name: str) -> int:
        if name not in names:
            names.append(name)
        return names.index(name)

    ranges: list[list[int]] = []
    for fields in _lines(texts["Scripts.txt"]):
        script = fields[1].replace("_", " ")
        if script in IGNORED:
            continue
        lo, hi = _range(fields[0])
        if ranges and ranges[-1][2] == idx(script) and ranges[-1][1] + 1 == lo:
            ranges[-1][1] = hi
        else:
            ranges.append([lo, hi, idx(script)])
    ranges.sort()
    extensions: list[list] = []
    for fields in _lines(texts["ScriptExtensions.txt"]):
        lo, hi = _range(fields[0])
        scripts = sorted(idx(short_to_long[code]) for code in fields[1].split())
        extensions.append([lo, hi, scripts])
    extensions.sort()
    return {
        "unicode_version": UNICODE_VERSION,
        "scripts": names,
        "ranges": ranges,
        "extensions": extensions,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-dir", type=Path)
    args = parser.parse_args()
    texts = {}
    for name in FILES:
        if args.from_dir:
            texts[name] = (args.from_dir / name).read_text(encoding="utf-8")
        else:
            with urllib.request.urlopen(BASE + name, timeout=30) as resp:  # noqa: S310
                texts[name] = resp.read().decode("utf-8")
    data = build(texts)
    text = json.dumps(data, separators=(",", ":")) + "\n"
    OUT.write_text(re.sub(r"\],\[", "],\n[", text), encoding="utf-8")
    print(f"wrote {OUT} ({len(data['ranges'])} ranges, {len(data['extensions'])} extensions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
