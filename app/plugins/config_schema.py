"""Validator for a plugin's `config.schema.json`: a small subset of JSON Schema 2020-12.

Only keywords listed here are accepted (a keyword pre-check); the schema itself is
then checked against the 2020-12 metaschema and values against it by `jsonschema`.
Secret fields are `"writeOnly": true` on strings (the annotation pydantic emits for
`SecretStr`, which the shared SchemaForm keys on).

`title` and `description` may be a string or an i18n map `{"en": ..., "fr": ...}`
with `en` required.
"""

import copy
import re
from typing import Any

from jsonschema import Draft202012Validator

from app.plugins.errors import PluginIssue, PluginValidationError

SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
MAX_PROPERTIES = 50
MAX_ENUM = 100
MAX_TEXT = 500

_ROOT_KEYS = {"$schema", "type", "title", "description", "properties", "required"}
_COMMON_KEYS = {"type", "title", "description", "default", "writeOnly"}
_KEYS_BY_TYPE: dict[str, set[str]] = {
    "string": {"enum", "minLength", "maxLength"},
    "integer": {"enum", "minimum", "maximum"},
    "number": {"enum", "minimum", "maximum"},
    "boolean": set(),
    "array": {"items", "minItems", "maxItems"},
}
_ITEM_KEYS = {"type", "enum", "minLength", "maxLength"}
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")


class _Collector:
    def __init__(self) -> None:
        self.issues: list[PluginIssue] = []

    def add(self, code: str, path: str, message: str) -> None:
        self.issues.append(PluginIssue(code, path, message))


def _text(value: Any, path: str, out: _Collector) -> str | None:
    """Check a string-or-i18n-map; returns the English text used for the metaschema pass."""
    if isinstance(value, str):
        if len(value) > MAX_TEXT:
            out.add("config_schema_text_too_long", path, "text is too long")
        return value
    if isinstance(value, dict):
        bad = [k for k, v in value.items() if not _LANG_RE.match(k) or not isinstance(v, str)]
        if bad or "en" not in value:
            out.add(
                "config_schema_i18n_invalid",
                path,
                "an i18n text maps language codes to strings and requires 'en'",
            )
            return None
        if any(len(v) > MAX_TEXT for v in value.values()):
            out.add("config_schema_text_too_long", path, "text is too long")
        return value["en"]
    out.add("config_schema_i18n_invalid", path, "title and description are strings or i18n maps")
    return None


def _unknown(keys: set[str], allowed: set[str], path: str, out: _Collector) -> None:
    for key in sorted(keys - allowed):
        out.add(
            "config_schema_keyword_not_allowed",
            f"{path}.{key}" if path else key,
            f"keyword '{key}' is outside the supported JSON Schema subset",
        )


def _check_property(name: str, prop: Any, out: _Collector) -> dict[str, Any] | None:
    path = f"properties.{name}"
    if not isinstance(prop, dict):
        out.add("config_schema_invalid", path, "a property is an object")
        return None
    ptype = prop.get("type")
    if ptype not in _KEYS_BY_TYPE:
        out.add(
            "config_schema_type_not_allowed",
            f"{path}.type",
            "type must be one of string, integer, number, boolean, array",
        )
        return None
    _unknown(set(prop), _COMMON_KEYS | _KEYS_BY_TYPE[ptype], path, out)
    normalized = {k: v for k, v in prop.items() if k not in ("title", "description")}
    for key in ("title", "description"):
        if key in prop:
            plain = _text(prop[key], f"{path}.{key}", out)
            if plain is not None:
                normalized[key] = plain
    if "writeOnly" in prop:
        if prop["writeOnly"] is not True or ptype != "string":
            out.add(
                "config_schema_write_only_invalid",
                f"{path}.writeOnly",
                "writeOnly is allowed on strings only, and only as true",
            )
        elif "default" in prop or "enum" in prop:
            out.add(
                "config_schema_secret_default",
                path,
                "a secret field cannot have a default or an enum",
            )
    enum = prop.get("enum")
    if enum is not None and (not isinstance(enum, list) or not 0 < len(enum) <= MAX_ENUM):
        out.add(
            "config_schema_invalid", f"{path}.enum", f"enum is a list of 1 to {MAX_ENUM} values"
        )
    if ptype == "array":
        items = prop.get("items")
        if not isinstance(items, dict) or items.get("type") != "string":
            out.add(
                "config_schema_array_items",
                f"{path}.items",
                "arrays hold strings: items must be {'type': 'string'}",
            )
        else:
            _unknown(set(items), _ITEM_KEYS, f"{path}.items", out)
    return normalized


def check_config_schema(schema: Any) -> frozenset[str]:
    """Validate a config schema; returns its secret field names.

    Raises PluginValidationError listing every problem found.
    """
    out = _Collector()
    if not isinstance(schema, dict):
        out.add("config_schema_invalid", "", "the config schema is a JSON object")
        raise PluginValidationError(out.issues)
    _unknown(set(schema), _ROOT_KEYS, "", out)
    if schema.get("$schema", SCHEMA_DIALECT) != SCHEMA_DIALECT:
        out.add("config_schema_dialect", "$schema", f"$schema must be {SCHEMA_DIALECT}")
    if schema.get("type") != "object":
        out.add("config_schema_invalid", "type", "the root type must be 'object'")
    props = schema.get("properties")
    if not isinstance(props, dict) or len(props) > MAX_PROPERTIES:
        out.add(
            "config_schema_invalid",
            "properties",
            f"properties is an object of up to {MAX_PROPERTIES}",
        )
        props = {}
    normalized: dict[str, Any] = {
        k: v for k, v in schema.items() if k not in ("title", "description")
    }
    normalized["properties"] = {}
    for key in ("title", "description"):
        if key in schema:
            plain = _text(schema[key], key, out)
            if plain is not None:
                normalized[key] = plain
    secrets: set[str] = set()
    for name, prop in props.items():
        if not _NAME_RE.match(name):
            out.add(
                "config_schema_name_invalid", f"properties.{name}", "use lowercase snake_case names"
            )
            continue
        norm = _check_property(name, prop, out)
        if norm is not None:
            normalized["properties"][name] = norm
            if norm.get("writeOnly") is True and norm.get("type") == "string":
                secrets.add(name)
    required = schema.get("required", [])
    if not isinstance(required, list) or any(r not in props for r in required):
        out.add(
            "config_schema_required_unknown", "required", "required lists declared properties only"
        )
    if not out.issues:
        # The keyword pre-check passed: let the library vouch for the schema itself
        # (types, enum/default consistency) on a copy with plain-text titles.
        meta = Draft202012Validator(Draft202012Validator.META_SCHEMA)
        for err in meta.iter_errors(normalized):
            out.add(
                "config_schema_invalid", ".".join(str(p) for p in err.absolute_path), err.message
            )
        validator = Draft202012Validator(copy.deepcopy(normalized))
        for name, prop in normalized["properties"].items():
            if "default" in prop and not validator.evolve(schema=prop).is_valid(prop["default"]):
                out.add(
                    "config_schema_default_invalid",
                    f"properties.{name}.default",
                    "default does not fit its own schema",
                )
    if out.issues:
        raise PluginValidationError(out.issues)
    return frozenset(secrets)


def secret_fields(schema: dict[str, Any]) -> frozenset[str]:
    """Names of the `writeOnly` fields of an already validated schema."""
    return frozenset(
        n for n, p in schema.get("properties", {}).items() if p.get("writeOnly") is True
    )


def validate_config_values(schema: dict[str, Any], values: dict[str, Any]) -> list[PluginIssue]:
    """Check config values against a validated schema (i18n texts are irrelevant here)."""
    plain = copy.deepcopy(schema)
    for node in [plain, *plain.get("properties", {}).values()]:
        for key in ("title", "description"):
            if isinstance(node.get(key), dict):
                node[key] = node[key].get("en", "")
    validator = Draft202012Validator(plain)
    return [
        PluginIssue(
            "config_value_invalid" if e.validator != "required" else "config_value_missing",
            ".".join(str(p) for p in e.absolute_path),
            f"value violates '{e.validator}'",  # never echo the value: it may be a secret
        )
        for e in sorted(
            validator.iter_errors(values), key=lambda e: list(map(str, e.absolute_path))
        )
    ]
