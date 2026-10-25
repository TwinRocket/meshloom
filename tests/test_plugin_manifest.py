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


@pytest.mark.parametrize(
    "text",
    [
        "\u067e\u06cc\u0634\u200c\u0628\u06cc\u0646\u06cc",  # Persian, ZWNJ
        "\u0915\u094d\u200d\u0937",  # Hindi conjunct, ZWJ
        "Family \U0001f468\u200d\U0001f469\u200d\U0001f467",  # emoji ZWJ sequence
        "Goose \U0001fabf Face \U0001fae9",  # recent emoji, whatever the Python version
        "Flag \U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f",  # tags
        "Heart \u2764\ufe0f",  # variation selector
        "\u5929\u6c17\u30dc\u30c3\u30c8 \u3066\u3059\u3068",  # Han + kana
        "\u041f\u043e\u0433\u043e\u0434\u0430 GPS-\u0442\u0440\u0435\u043a\u0435\u0440",
        "\u30e9\u30fc\u30e1\u30f3",  # ramen: prolonged sound mark U+30FC
        "\u30b5\u30fc\u30d0\u30fc",
        "\u4f50\u3005\u6728",  # iteration mark U+3005
        "\u6771\u4eac\u30bf\u30ef\u30fc",
        "\ud55c\uad6d\uc5b4",
        "\u4e2d\u6587",
        "Tokyo\u6771\u4eac Weather\u30e9\u30fc\u30e1\u30f3",
        "Unassigned \U0003fffd",  # validity never depends on the Unicode tables in use
    ],
)
def test_real_scripts_and_emoji_are_accepted(text):
    ok = copy.deepcopy(SAMPLE)
    ok["name"] = {"en": "Weather", "fa": text}
    ok["summary"] = {"en": "Forecast", "hi": text}
    ok["authors"] = [{"name": text}]
    parse_manifest(ok)


@pytest.mark.parametrize(
    "char",
    [
        "\u061c", "\u200b", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d",
        "\u202e", "\u2060", "\u2061", "\u2066", "\u2067", "\u2068", "\u2069", "\u206a",
        "\ufeff", "\u00ad", "\u034f", "\u115f", "\u1160", "\u3164", "\uffa0", "\u17b4", "\u17b5", "\u2800",
        "\U000e0041", "\U000e007f",
        "\ufeff", "\ufff9", "\U000e0001", "\x00", "\x07", "\x1b", "\x7f", "\x85",
        "\u2028", "\ue000", "\ud800",
    ],
)  # fmt: skip
def test_each_refused_character(char):
    bad = copy.deepcopy(SAMPLE)
    bad["name"] = {"en": f"Wea{char}ther"}
    assert ("text_invalid", "name") in _issues(bad)


def test_ip_literals_use_the_guard_ranges():
    from app.plugins.permissions import check_host

    for host in (
        "64:ff9b::7f00:1",
        "224.0.0.1",
        "192.0.0.9",
        "ff02::1",
        "100.64.0.1",
        "2001:db8::1",
    ):
        with pytest.raises(Exception, match="host_private_ip|not allowed"):
            check_host(host)
    for host in (
        "localhost",
        "a.localhost",
        "router.lan",
        "x.local",
        "nas.home.arpa",
        "m.internal",
        "intranet",
    ):
        with pytest.raises(Exception, match="local-network"):
            check_host(host)
    assert check_host("1.1.1.1") == "1.1.1.1"


def _tag_flag(code: str) -> str:
    """U+1F3F4, `code` as tag characters, then the cancel tag (an emoji tag sequence shape)."""
    return "\U0001f3f4" + "".join(chr(0xE0000 + ord(c)) for c in code) + "\U000e007f"


def test_tag_characters_only_inside_a_flag_sequence():
    flag = "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"
    for text, ok in (
        (f"Scotland {flag}", True),
        ("Weather" + "".join(chr(0xE0000 + ord(c)) for c in "ignore"), False),
        ("Flag \U0001f3f4\U000e0067\U000e0062", False),  # no cancel tag
        (f"{flag}\U000e0041", False),
        # Shaped like a flag, but not one of the three RGI tag sequences: hidden text.
        (f"Weather {_tag_flag('ignore~previous~instructions')}", False),
        (f"Flag {_tag_flag('ustx')}", False),  # a valid subdivision, but not RGI
        (f"Flag {_tag_flag('GBENG')}", False),  # uppercase tag letters
        (f"Flag {_tag_flag('gbengx')}", False),
        (f"Flag {_tag_flag('')}", False),  # no tag at all
        (f"England {_tag_flag('gbeng')}", True),
        (f"Wales {_tag_flag('gbwls')} twice {_tag_flag('gbwls')}", True),
    ):
        m = copy.deepcopy(SAMPLE)
        m["summary"] = {"en": text}
        assert _model_valid(m) is ok, text


def test_blank_name_made_of_a_filler_is_refused():
    bad = copy.deepcopy(SAMPLE)
    bad["name"] = {"en": "\u3164"}
    assert ("text_invalid", "name") in _issues(bad)


def test_lan_host_overlap_check_is_defence_in_depth():
    # The per-host validators refuse any name valid in both lists, so no manifest (and no
    # golden vector) reaches this check; it guards a future relaxation. Call it directly.
    from pydantic_core import PydanticCustomError

    from app.plugins.permissions import HttpParams

    params = HttpParams.model_construct(hosts=["nas.local"], lan_hosts=["nas.local:8080"])
    with pytest.raises(PydanticCustomError) as exc:
        params._unique()
    assert exc.value.type == "lan_host_overlap"
