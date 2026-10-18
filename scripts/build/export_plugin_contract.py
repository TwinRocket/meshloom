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
