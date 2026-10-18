"""Plugin manifest model, permission registry, icon list and the exported contract."""

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

from app.plugins.errors import PluginValidationError
from app.plugins.manifest import load_manifest_json, parse_manifest, plugin_icons

ROOT = Path(__file__).resolve().parent.parent
VECTORS = json.loads((ROOT / "docs/dev/plugins/vectors/manifest.json").read_text("utf-8"))
SAMPLE = next(v["manifest"] for v in VECTORS["vectors"] if v["name"] == "sample")


def _load_exporter():
    spec = importlib.util.spec_from_file_location(
        "export_plugin_contract", ROOT / "scripts/build/export_plugin_contract.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _issues(manifest, origin="third_party"):
    with pytest.raises(PluginValidationError) as exc:
        parse_manifest(manifest, origin=origin)
    return [(i.code, i.path) for i in exc.value.issues]


def test_cadrage_sample_validates():
    m = parse_manifest(copy.deepcopy(SAMPLE))
    assert m.id == "acme.weather"
    assert m.entry_path == "plugin.py"
    assert m.entry_attr == "Weather"
    assert m.permissions.declared()["messages.send"]["per_hour"] == 20


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_golden_vectors(vector):
    if vector["valid"]:
        parse_manifest(copy.deepcopy(vector["manifest"]), origin=vector["origin"])
        return
    got = _issues(copy.deepcopy(vector["manifest"]), vector["origin"])
    for err in vector["errors"]:
        assert (err["code"], err["path"]) in got


def test_vectors_cover_the_acceptance_cases():
    names = {v["name"] for v in VECTORS["vectors"]}
    assert {
        "permission-reserved-ui-panel",
        "license-not-listed",
        "homepage-javascript",
        "name-contains-official",
        "http-meshloom-host",
    } <= names


def test_meshloom_namespace_allows_meshloom_name_and_host():
    m = next(v for v in VECTORS["vectors"] if v["name"] == "bundled-meshloom-namespace")
    parse_manifest(m["manifest"], origin="bundled")
    # The same manifest from a third party is refused for the id, then name and host.
    assert ("id_namespace_reserved", "") in _issues(m["manifest"])


def test_errors_never_echo_the_rejected_value():
    bad = copy.deepcopy(SAMPLE)
    bad["homepage"] = "javascript:alert('secret-token-123')"
    with pytest.raises(PluginValidationError) as exc:
        parse_manifest(bad)
    assert "secret-token-123" not in str(exc.value)


def test_all_issues_are_reported_together():
    bad = copy.deepcopy(SAMPLE)
    bad["license"] = "WTFPL"
    bad["homepage"] = "http://x.example"
    bad["icon"] = "nope"
    assert {c for c, _ in _issues(bad)} == {"license_not_accepted", "url_not_https", "icon_unknown"}


def test_prerelease_and_build_metadata_versions():
    for ok in ("1.0.0-rc.1", "2.0.0-beta.2", "1.0.0+build.5"):
        m = copy.deepcopy(SAMPLE)
        m["version"] = ok
        parse_manifest(m)
    m = copy.deepcopy(SAMPLE)
    m["version"] = "1.0.0-alpha.beta"  # valid SemVer, but packaging cannot order it
    assert ("version_not_comparable", "version") in _issues(m)


def test_http_host_rules():
    for host in ("8.8.8.8", "api.example.com", "2a00:1450:4001:81b::200e"):
        m = copy.deepcopy(SAMPLE)
        m["permissions"]["http"] = {"hosts": [host]}
        parse_manifest(m)
    for host, code in (
        ("API.example.com", "host_invalid"),
        ("example.com:8080", "host_invalid"),
        ("192.168.0.0/16", "host_range"),
        ("*", "host_wildcard"),
        ("10.0.0.1", "host_private_ip"),
        ("fe80::1", "host_private_ip"),
        ("0.0.0.0", "host_private_ip"),
        ("::ffff:127.0.0.1", "host_private_ip"),
        ("-bad.example.com", "host_invalid"),
    ):
        m = copy.deepcopy(SAMPLE)
        m["permissions"]["http"] = {"hosts": [host]}
        assert (code, "permissions.http.hosts.0") in _issues(m)


def test_duplicate_keys_and_oversize_manifest_are_refused():
    with pytest.raises(PluginValidationError) as exc:
        load_manifest_json(b'{"manifest": 1, "manifest": 1}')
    assert exc.value.codes == ["manifest_not_json"]
    with pytest.raises(PluginValidationError) as exc:
        load_manifest_json(b"{" + b" " * (64 * 1024) + b"}")
    assert exc.value.codes == ["manifest_too_large"]
    with pytest.raises(PluginValidationError) as exc:
        load_manifest_json(b'{"a": NaN}')
    assert exc.value.codes == ["manifest_not_json"]
    with pytest.raises(PluginValidationError) as exc:
        load_manifest_json(b"[" * 30000)
    assert exc.value.codes == ["manifest_not_json"]


def test_control_characters_in_texts_are_refused():
    m = copy.deepcopy(SAMPLE)
    m["name"] = {"en": "Wea\x1bther"}
    assert ("text_invalid", "name") in _issues(m)


def test_icon_list_is_sorted_unique_kebab_case():
    icons = list(plugin_icons())
    assert icons == sorted(set(icons))
    assert all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i) for i in icons)


def test_exported_contract_is_not_stale():
    exporter = _load_exporter()
    for path, text in exporter.expected_files().items():
        assert path.read_text("utf-8") == text, (
            f"{path.relative_to(ROOT)} is stale: run scripts/build/export_plugin_contract.py"
        )
    assert exporter.main(["--check"]) == 0


def test_exported_schema_describes_the_contract():
    schema = json.loads((ROOT / "docs/dev/plugins/manifest.schema.json").read_text("utf-8"))
    assert schema["additionalProperties"] is False
    perms = schema["$defs"]["Permissions"]["properties"]
    assert {"messages.read", "messages.send", "http", "storage", "timer"} <= set(perms)
    assert not {"ui.panel", "feed.post"} & set(perms)


# --- Parity between the pydantic model and the exported JSON schema -------------------

# Error codes that JSON Schema can express: a manifest failing with one of these must
# be invalid for the schema too, or the contract and the code disagree.
SCHEMA_CODES = {
    "missing",
    "extra_forbidden",
    "literal_error",
    "int_type",
    "bool_type",
    "string_type",
    "string_pattern_mismatch",
    "too_short",
    "less_than_equal",
    "greater_than_equal",
}
PROBES = {
    "manifest-true": ("manifest", True),
    "manifest-one-string": ("manifest", "1"),
    "api-string": ("runtime", {"type": "python", "api": "1", "python": ">=3.11"}),
    "api-bool": ("runtime", {"type": "python", "api": True, "python": ">=3.11"}),
    "stores-yes": (
        "privacy",
        {"sends_to_third_parties": [], "stores_message_content": "yes"},
    ),
    "stores-int": ("privacy", {"sends_to_third_parties": [], "stores_message_content": 1}),
    "direct-string": ("permissions", {"messages.read": {"direct": "true"}}),
    "per-hour-string": ("permissions", {"messages.send": {"per_hour": "20"}}),
    "per-hour-float": ("permissions", {"messages.send": {"per_hour": 20.5}}),
    "hooks-string": ("hooks", "message"),
    "categories-string": ("categories", "bot"),
}


@pytest.fixture(scope="module")
def schema_validator():
    from jsonschema import Draft202012Validator

    schema = json.loads((ROOT / "docs/dev/plugins/manifest.schema.json").read_text("utf-8"))
    return Draft202012Validator(schema)


def _model_valid(manifest, origin="third_party"):
    try:
        parse_manifest(copy.deepcopy(manifest), origin=origin)
        return True
    except PluginValidationError:
        return False


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_model_and_schema_agree_on_the_vectors(vector, schema_validator):
    schema_ok = schema_validator.is_valid(vector["manifest"])
    if _model_valid(vector["manifest"], vector["origin"]):
        assert schema_ok, "the model accepts what the exported schema rejects"
    elif vector["errors"][0]["code"] in SCHEMA_CODES:
        assert not schema_ok, "the schema accepts what the model rejects with a schema code"


@pytest.mark.parametrize(("key", "value"), PROBES.values(), ids=list(PROBES))
def test_strict_typing_matches_the_schema(key, value, schema_validator):
    bad = copy.deepcopy(SAMPLE)
    bad[key] = value
    assert not schema_validator.is_valid(bad)
    assert not _model_valid(bad)


def test_trailing_newline_is_not_an_anchor_escape():
    for field in (
        ("name", {"en": "W", "fr\n": "x"}),
        ("readme", {"en": "README.md", "fr\n": "R.md"}),
    ):
        bad = copy.deepcopy(SAMPLE)
        bad[field[0]] = field[1]
        assert ("i18n_language_invalid", field[0]) in _issues(bad)
    for pattern_field, value in (("id", "acme.weather\n"), ("version", "1.2.0\n")):
        bad = copy.deepcopy(SAMPLE)
        bad[pattern_field] = value
        assert not _model_valid(bad)


def test_homoglyph_and_invisible_characters():
    for name in ("Meѕhloom", "Mеshloom", "Weather​", "Wea‮ther"):
        bad = copy.deepcopy(SAMPLE)
        bad["name"] = {"en": name}
        assert not _model_valid(bad), name
    bad = copy.deepcopy(SAMPLE)
    bad["authors"] = [{"name": "Aсme"}]
    assert ("name_homoglyph", "authors.0.name") in _issues(bad)
    # Compatibility forms fold before the misleading-word check.
    bad = copy.deepcopy(SAMPLE)
    bad["name"] = {"en": "Ｍeshloom"}
    assert ("name_misleading", "") in _issues(bad)
    ok = copy.deepcopy(SAMPLE)
    ok["name"] = {"en": "Weather", "ru": "Погода GPS-трекер"}
    parse_manifest(ok)


def test_model_is_stricter_than_the_schema_on_integral_floats():
    # JSON Schema treats 1.0 as the integer 1; the model refuses it for `api`.
    bad = copy.deepcopy(SAMPLE)
    bad["runtime"] = {"type": "python", "api": 1.0, "python": ">=3.11"}
    assert not _model_valid(bad)
