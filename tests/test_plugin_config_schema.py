"""Subset validator for a plugin's config.schema.json."""

import copy

import pytest

from app.plugins.config_schema import (
    check_config_schema,
    secret_fields,
    validate_config_values,
)
from app.plugins.errors import PluginValidationError

GOOD = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "title": {"en": "Weather", "fr": "Météo"},
    "properties": {
        "city": {
            "type": "string",
            "title": {"en": "City", "fr": "Ville"},
            "description": "Where to look",
            "maxLength": 80,
        },
        "api_key": {"type": "string", "title": "API key", "writeOnly": True},
        "units": {"type": "string", "enum": ["metric", "imperial"], "default": "metric"},
        "days": {"type": "integer", "minimum": 1, "maximum": 7, "default": 3},
        "threshold": {"type": "number"},
        "verbose": {"type": "boolean", "default": False},
        "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 5},
    },
    "required": ["city", "api_key"],
}


def codes(schema):
    with pytest.raises(PluginValidationError) as exc:
        check_config_schema(schema)
    return exc.value.codes


def mutated(path, value):
    s = copy.deepcopy(GOOD)
    node = s
    keys = path.split("/")
    for k in keys[:-1]:
        node = node[k]
    node[keys[-1]] = value
    return s


def test_good_schema_and_secret_fields():
    assert check_config_schema(GOOD) == {"api_key"}
    assert secret_fields(GOOD) == {"api_key"}


@pytest.mark.parametrize(
    ("schema", "code"),
    [
        (mutated("properties/x", {"type": "object"}), "config_schema_type_not_allowed"),
        (mutated("properties/x", {"$ref": "#/foo"}), "config_schema_type_not_allowed"),
        (mutated("properties/city/pattern", "(a+)+$"), "config_schema_keyword_not_allowed"),
        (mutated("allOf", []), "config_schema_keyword_not_allowed"),
        (mutated("properties/days/writeOnly", True), "config_schema_write_only_invalid"),
        (mutated("properties/api_key/writeOnly", False), "config_schema_write_only_invalid"),
        (mutated("properties/api_key/default", "abc"), "config_schema_secret_default"),
        (mutated("properties/tags/items", {"type": "integer"}), "config_schema_array_items"),
        (mutated("required", ["ghost"]), "config_schema_required_unknown"),
        (mutated("properties/BadName", {"type": "string"}), "config_schema_name_invalid"),
        (mutated("properties/city/title", {"fr": "Ville"}), "config_schema_i18n_invalid"),
        (mutated("properties/units/default", "kelvin"), "config_schema_default_invalid"),
        (mutated("$schema", "http://json-schema.org/draft-07/schema#"), "config_schema_dialect"),
        (mutated("type", "array"), "config_schema_invalid"),
        ([], "config_schema_invalid"),
    ],
)
def test_subset_violations(schema, code):
    assert code in codes(schema)


def test_values_validated_by_the_library_without_echoing_them():
    assert validate_config_values(GOOD, {"city": "Paris", "api_key": "k"}) == []
    issues = validate_config_values(
        GOOD, {"city": "Paris", "days": 99, "api_key": "hunter2-secret"}
    )
    assert {i.path for i in issues} == {"days"}
    issues = validate_config_values(GOOD, {"city": 3, "api_key": "k"})
    assert issues and "3" not in issues[0].message
    missing = validate_config_values(GOOD, {"city": "Paris"})
    assert [i.code for i in missing] == ["config_value_missing"]
    assert all("hunter2" not in i.message for i in issues)
