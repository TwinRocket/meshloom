#!/usr/bin/env python3
"""Write (or check) the plugin manifest contract files.

    uv run python scripts/build/export_plugin_contract.py          # regenerate
    uv run python scripts/build/export_plugin_contract.py --check  # fail when stale

Outputs, under docs/dev/plugins/:
  manifest.schema.json   JSON Schema of the manifest (from app/plugins/manifest.py)
  vectors/manifest.json  golden vectors: valid manifests and failing ones with the
                         expected error codes and paths, for the index CI and the site

Both are part of the contract shared with meshloom-plugins: change them in a
`CONTRACTS:` commit. Every vector is run through the validator while generating,
so a vector can never record something the code does not do.
"""

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.plugins.errors import PluginValidationError  # noqa: E402
from app.plugins.manifest import manifest_json_schema, parse_manifest  # noqa: E402
from app.plugins.permissions import RESERVED_PERMISSIONS  # noqa: E402

OUT_DIR = ROOT / "docs" / "dev" / "plugins"
SCHEMA_PATH = OUT_DIR / "manifest.schema.json"
VECTORS_PATH = OUT_DIR / "vectors" / "manifest.json"

SAMPLE: dict[str, Any] = {
    "manifest": 1,
    "id": "acme.weather",
    "version": "1.2.0",
    "name": {"en": "Weather", "fr": "Météo"},
    "summary": {
        "en": "Answers !weather with the local forecast",
        "fr": "Répond à !meteo avec la prévision locale",
    },
    "authors": [{"name": "Acme", "url": "https://github.com/acme"}],
    "license": "MIT",
    "homepage": "https://github.com/acme/meshloom-plugin-weather",
    "icon": "cloud-sun",
    "categories": ["bot"],
    "runtime": {"type": "python", "api": 1, "python": ">=3.11"},
    "compat": {"meshloom": ">=5.0.0"},
    "entry": "plugin.py:Weather",
    "hooks": ["message", "timer"],
    "permissions": {
        "messages.read": {"channels": "public", "direct": True, "outgoing": False},
        "messages.send": {"targets": "reply", "per_hour": 20},
        "http": {"hosts": ["api.open-meteo.com"]},
        "storage": {},
    },
    "privacy": {
        "sends_to_third_parties": ["api.open-meteo.com"],
        "stores_message_content": False,
    },
    "config_schema": "config.schema.json",
    "i18n": {"en": "i18n/en.json", "fr": "i18n/fr.json"},
    "readme": {"en": "README.md", "fr": "README.fr.md"},
}

Mutation = Callable[[dict[str, Any]], None]


def _set(path: str, value: Any) -> Mutation:
    def apply(m: dict[str, Any]) -> None:
        node = m
        keys = path.split("/")
        for k in keys[:-1]:
            node = node[k]
        node[keys[-1]] = value

    return apply


def _drop(key: str) -> Mutation:
    return lambda m: m.pop(key)


def _perm(name: str, value: Any) -> Mutation:
    return lambda m: m["permissions"].__setitem__(name, value)


# (name, origin, mutation, expected [(code, path), ...]); the first error must match
# the first expected entry and every expected entry must be present.
FAILING: list[tuple[str, str, Mutation, list[tuple[str, str]]]] = [
    (
        "id-uppercase",
        "third_party",
        _set("id", "Acme.Weather"),
        [("string_pattern_mismatch", "id")],
    ),
    ("id-no-publisher", "third_party", _set("id", "weather"), [("string_pattern_mismatch", "id")]),
    (
        "id-reserved-meshloom",
        "third_party",
        _set("id", "meshloom.echo"),
        [("id_namespace_reserved", "")],
    ),
    ("id-reserved-local", "third_party", _set("id", "local.echo"), [("id_namespace_reserved", "")]),
    ("id-bundled-wrong-namespace", "bundled", lambda m: None, [("id_namespace_mismatch", "")]),
    (
        "version-two-parts",
        "third_party",
        _set("version", "1.2"),
        [("string_pattern_mismatch", "version")],
    ),
    (
        "version-leading-zero",
        "third_party",
        _set("version", "01.2.0"),
        [("string_pattern_mismatch", "version")],
    ),
    (
        "name-contains-meshloom",
        "third_party",
        _set("name", {"en": "Meshloom Weather"}),
        [("name_misleading", "")],
    ),
    (
        "name-contains-official",
        "third_party",
        _set("name", {"en": "Official Weather"}),
        [("name_misleading", "")],
    ),
    (
        "name-contains-officiel-fr",
        "third_party",
        _set("name", {"en": "Weather", "fr": "Météo officielle"}),
        [("name_misleading", "")],
    ),
    (
        "name-without-en",
        "third_party",
        _set("name", {"fr": "Météo"}),
        [("i18n_en_required", "name")],
    ),
    (
        "summary-without-en",
        "third_party",
        _set("summary", {"fr": "Météo"}),
        [("i18n_en_required", "summary")],
    ),
    (
        "license-not-listed",
        "third_party",
        _set("license", "GPL-2.0-only"),
        [("license_not_accepted", "license")],
    ),
    (
        "license-not-spdx",
        "third_party",
        _set("license", "Proprietary"),
        [("license_not_accepted", "license")],
    ),
    (
        "homepage-javascript",
        "third_party",
        _set("homepage", "javascript:alert(1)"),
        [("url_not_https", "homepage")],
    ),
    (
        "homepage-http",
        "third_party",
        _set("homepage", "http://example.com"),
        [("url_not_https", "homepage")],
    ),
    (
        "author-url-data",
        "third_party",
        _set("authors", [{"name": "A", "url": "data:text/html,x"}]),
        [("url_not_https", "authors.0.url")],
    ),
    (
        "icon-unknown",
        "third_party",
        _set("icon", "skull-and-crossbones"),
        [("icon_unknown", "icon")],
    ),
    (
        "permission-unknown",
        "third_party",
        _perm("files.write", {}),
        [("extra_forbidden", "permissions.files.write")],
    ),
    (
        "send-per-hour-over-cap",
        "third_party",
        _perm("messages.send", {"per_hour": 61}),
        [("less_than_equal", "permissions.messages.send.per_hour")],
    ),
    (
        "send-per-day-over-cap",
        "third_party",
        _perm("messages.send", {"per_day": 501}),
        [("less_than_equal", "permissions.messages.send.per_day")],
    ),
    (
        "send-bad-target",
        "third_party",
        _perm("messages.send", {"targets": "everyone"}),
        [("literal_error", "permissions.messages.send.targets")],
    ),
    (
        "http-wildcard-host",
        "third_party",
        _perm("http", {"hosts": ["*.example.com"]}),
        [("host_wildcard", "permissions.http.hosts.0")],
    ),
    (
        "http-ip-range",
        "third_party",
        _perm("http", {"hosts": ["10.0.0.0/8"]}),
        [("host_range", "permissions.http.hosts.0")],
    ),
    (
        "http-url-instead-of-host",
        "third_party",
        _perm("http", {"hosts": ["https://example.com/x"]}),
        [("host_range", "permissions.http.hosts.0")],
    ),
    (
        "http-no-hosts",
        "third_party",
        _perm("http", {"hosts": []}),
        [("too_short", "permissions.http.hosts")],
    ),
    (
        "http-meshloom-host",
        "third_party",
        _perm("http", {"hosts": ["store.meshloom.app"]}),
        [("http_host_reserved", "")],
    ),
    (
        "http-meshloom-apex",
        "third_party",
        _perm("http", {"hosts": ["meshloom.app"]}),
        [("http_host_reserved", "")],
    ),
    (
        "runtime-not-python",
        "third_party",
        _set("runtime/type", "node"),
        [("literal_error", "runtime.type")],
    ),
    (
        "runtime-python-range",
        "third_party",
        _set("runtime/python", "three-eleven"),
        [("specifier_invalid", "runtime.python")],
    ),
    (
        "compat-range",
        "third_party",
        _set("compat/meshloom", "newest"),
        [("specifier_invalid", "compat.meshloom")],
    ),
    (
        "entry-traversal",
        "third_party",
        _set("entry", "../plugin.py:Weather"),
        [("entry_invalid", "")],
    ),
    (
        "entry-dotdot-inside",
        "third_party",
        _set("entry", "a/../plugin.py:Weather"),
        [("path_unsafe", "")],
    ),
    (
        "hook-unknown",
        "third_party",
        _set("hooks", ["message", "boot"]),
        [("literal_error", "hooks.1")],
    ),
    (
        "readme-without-en",
        "third_party",
        _set("readme", {"fr": "README.fr.md"}),
        [("i18n_en_required", "readme")],
    ),
    (
        "config-schema-absolute",
        "third_party",
        _set("config_schema", "/etc/passwd"),
        [("path_unsafe", "config_schema")],
    ),
    (
        "i18n-path-traversal",
        "third_party",
        _set("i18n", {"en": "../en.json"}),
        [("path_unsafe", "i18n")],
    ),
    (
        "extra-field",
        "third_party",
        _set("telemetry_url", "https://example.com"),
        [("extra_forbidden", "telemetry_url")],
    ),
    ("manifest-version-2", "third_party", _set("manifest", 2), [("literal_error", "manifest")]),
    ("privacy-missing", "third_party", _drop("privacy"), [("missing", "privacy")]),
]
FAILING += [
    ("manifest-true", "third_party", _set("manifest", True), [("literal_error", "manifest")]),
    ("runtime-api-string", "third_party", _set("runtime/api", "1"), [("int_type", "runtime.api")]),
    (
        "privacy-bool-as-string",
        "third_party",
        _set("privacy/stores_message_content", "yes"),
        [("bool_type", "privacy.stores_message_content")],
    ),
    (
        "permission-bool-as-int",
        "third_party",
        _perm("messages.read", {"direct": 1}),
        [("bool_type", "permissions.messages.read.direct")],
    ),
    (
        "language-key-trailing-newline",
        "third_party",
        _set("name", {"en": "Weather", "fr\n": "Meteo"}),
        [("i18n_language_invalid", "name")],
    ),
    (
        "name-homoglyph",
        "third_party",
        _set("name", {"en": "Me\u0455hloom Echo"}),
        [("name_homoglyph", "name")],
    ),
    (
        "name-bidi-control",
        "third_party",
        _set("name", {"en": "Weather\u202e"}),
        [("text_invalid", "name")],
    ),
]
for _label, _char in [
    ("soft-hyphen", "\u00ad"),
    ("combining-grapheme-joiner", "\u034f"),
    ("hangul-choseong-filler", "\u115f"),
    ("hangul-jungseong-filler", "\u1160"),
    ("hangul-filler", "\u3164"),
    ("halfwidth-hangul-filler", "\uffa0"),
    ("braille-blank", "\u2800"),
    ("bare-tag-character", "\U000e0041"),
]:
    FAILING.append(
        (
            f"name-invisible-{_label}",
            "third_party",
            _set("name", {"en": f"Wea{_char}ther"}),
            [("text_invalid", "name")],
        )
    )
FAILING.append(
    (
        "name-only-hangul-filler",
        "third_party",
        _set("name", {"en": "\u3164"}),
        [("text_invalid", "name")],
    )
)
FAILING.append(
    (
        "name-hidden-tag-text",
        "third_party",
        _set("summary", {"en": "Forecast" + "".join(chr(0xE0000 + ord(c)) for c in "ignore")}),
        [("text_invalid", "summary")],
    )
)
for _label, _host, _code in [
    ("trailing-newline", "example.com\n", "host_invalid"),
    ("numeric-short", "127.1", "host_invalid"),
    ("numeric-decimal", "2130706433", "host_invalid"),
    ("numeric-hex", "0x7f.1", "host_invalid"),
    ("ipv6-scope-id", "fe80::1%eth0", "host_invalid"),
    ("ipv6-uppercase", "2A00:1450:4001:81B::200E", "host_invalid"),
    ("nat64-loopback", "64:ff9b::7f00:1", "host_private_ip"),
    ("multicast", "224.0.0.1", "host_private_ip"),
    ("ietf-protocol-range", "192.0.0.9", "host_private_ip"),
    ("localhost", "localhost", "host_private_name"),
    ("localhost-subdomain", "api.localhost", "host_private_name"),
    ("lan-name", "router.lan", "host_private_name"),
    ("mdns-name", "printer.local", "host_private_name"),
    ("home-arpa-name", "nas.home.arpa", "host_private_name"),
    ("internal-name", "metadata.google.internal", "host_private_name"),
    ("single-label", "intranet", "host_private_name"),
    ("loopback", "127.0.0.1", "host_private_ip"),
    ("ipv6-loopback", "::1", "host_private_ip"),
    ("private-range", "192.168.1.10", "host_private_ip"),
    ("link-local-metadata", "169.254.169.254", "host_private_ip"),
]:
    FAILING.append(
        (
            f"http-host-{_label}",
            "third_party",
            _perm("http", {"hosts": [_host]}),
            [(_code, "permissions.http.hosts.0")],
        )
    )
FAILING += [
    (
        f"permission-reserved-{name.replace('.', '-')}",
        "third_party",
        _perm(name, {}),
        [("permission_reserved", "permissions")],
    )
    for name in sorted(RESERVED_PERMISSIONS)
]


def _vector(
    name: str, origin: str, manifest: dict[str, Any], expected: list[tuple[str, str]] | None
) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "origin": origin, "manifest": manifest}
    if expected is None:
        out["valid"] = True
    else:
        out["valid"] = False
        out["errors"] = [{"code": c, "path": p} for c, p in expected]
    return out


def _bundled() -> dict[str, Any]:
    m = copy.deepcopy(SAMPLE)
    m.update(id="meshloom.echo", name={"en": "Meshloom Echo"})
    m["permissions"]["http"] = {"hosts": ["store.meshloom.app"]}
    m["privacy"]["sends_to_third_parties"] = ["store.meshloom.app"]
    return m


def _with_text(text: str) -> dict[str, Any]:
    m = copy.deepcopy(SAMPLE)
    m["name"] = {"en": "Weather", "fa": text}
    m["summary"] = {"en": "Forecast", "hi": text}
    return m


def build_vectors() -> dict[str, Any]:
    minimal = {
        k: SAMPLE[k]
        for k in [
            "manifest",
            "id",
            "version",
            "name",
            "summary",
            "authors",
            "license",
            "categories",
            "runtime",
            "compat",
            "entry",
            "hooks",
            "permissions",
            "privacy",
            "readme",
        ]
    }
    minimal["permissions"] = {}
    minimal["hooks"] = []
    local = copy.deepcopy(SAMPLE)
    local["id"] = "local.my-bot"
    vectors = [
        _vector("sample", "third_party", SAMPLE, None),
        _vector("minimal", "third_party", minimal, None),
        _vector("bundled-meshloom-namespace", "bundled", _bundled(), None),
        _vector("local-namespace", "local", local, None),
        _vector(
            "text-persian-zwnj",
            "third_party",
            _with_text("\u067e\u06cc\u0634\u200c\u0628\u06cc\u0646\u06cc"),
            None,
        ),
        _vector(
            "text-hindi-zwj",
            "third_party",
            _with_text("\u0915\u094d\u200d\u0937 \u092a\u094d\u0930\u0915\u094d\u200d\u0937"),
            None,
        ),
        _vector(
            "text-emoji-zwj-and-new-emoji",
            "third_party",
            _with_text("Family \U0001f468\u200d\U0001f469\u200d\U0001f467 \U0001fabf"),
            None,
        ),
        _vector(
            "text-japanese-prolonged-sound-mark",
            "third_party",
            _with_text("\u30e9\u30fc\u30e1\u30f3 \u30b5\u30fc\u30d0\u30fc \u4f50\u3005\u6728"),
            None,
        ),
        _vector(
            "text-korean-and-chinese-with-latin",
            "third_party",
            _with_text("\ud55c\uad6d\uc5b4 \u4e2d\u6587 Tokyo\u6771\u4eac"),
            None,
        ),
        _vector(
            "text-japanese-mixed-scripts",
            "third_party",
            _with_text("\u5929\u6c17\u30dc\u30c3\u30c8 \u3066\u3059\u3068"),
            None,
        ),
    ]
    for name, origin, mutate, expected in FAILING:
        manifest = copy.deepcopy(SAMPLE)
        mutate(manifest)
        vectors.append(_vector(name, origin, manifest, expected))
    # Every vector must behave exactly as recorded.
    for v in vectors:
        try:
            parse_manifest(v["manifest"], origin=v["origin"])
            got: list[tuple[str, str]] | None = None
        except PluginValidationError as exc:
            got = [(i.code, i.path) for i in exc.issues]
        want = None if v["valid"] else [(e["code"], e["path"]) for e in v.get("errors", [])]
        if want is None:
            assert got is None, f"vector {v['name']} should be valid, got {got}"
        else:
            assert got is not None and all(w in got for w in want), (
                f"vector {v['name']}: expected {want}, got {got}"
            )
    return {
        "description": (
            "Golden vectors for the plugin manifest contract. 'origin' is who submits the "
            "manifest (third_party, bundled for meshloom.*, local for local.*). A failing "
            "vector lists errors that must all be reported; other errors may accompany them."
        ),
        "schema": "../manifest.schema.json",
        "vectors": vectors,
    }


def _dump(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def expected_files() -> dict[Path, str]:
    return {
        SCHEMA_PATH: _dump(manifest_json_schema()),
        VECTORS_PATH: _dump(build_vectors()),
    }


def main(argv: list[str]) -> int:
    files = expected_files()
    if "--check" in argv:
        stale = [p for p, text in files.items() if not p.exists() or p.read_text("utf-8") != text]
        for p in stale:
            print(f"stale: {p.relative_to(ROOT)} (run scripts/build/export_plugin_contract.py)")
        return 1 if stale else 0
    for path, text in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
