"""The plugin key-value rules, pure (no database): shared by the repository
(`PluginKvRepository`), the `storage` capability and the developer harness
(`meshloom_plugin.dev`). Docs: docs/dev/plugins.md, "Data model"."""

import json
import unicodedata
from typing import Any

KV_MAX_KEY_BYTES = 256
KV_MAX_VALUE_BYTES = 256 * 1024
KV_QUOTA_BYTES = 5 * 1024 * 1024


class PluginStoreError(ValueError):
    """A coded refusal of the plugin repository. ``code`` and ``fields`` never
    hold a value, only field or key names."""

    def __init__(self, code: str, fields: list[str] | None = None) -> None:
        self.code = code
        self.fields = fields or []
        super().__init__(f"{code}: {', '.join(self.fields)}" if self.fields else code)


def check_kv_key(key: str) -> None:
    """A key is 1 to ``KV_MAX_KEY_BYTES`` UTF-8 bytes with no control, format,
    surrogate or unassigned character."""
    if not isinstance(key, str) or not key:
        raise PluginStoreError("kv_key_invalid")
    try:
        size = len(key.encode("utf-8"))
    except UnicodeEncodeError:
        raise PluginStoreError("kv_key_invalid") from None
    if size > KV_MAX_KEY_BYTES or any(unicodedata.category(c).startswith("C") for c in key):
        raise PluginStoreError("kv_key_invalid")


def encode_kv_value(key: str, value: Any) -> tuple[str, int]:
    """The stored JSON text of a value and its size in bytes; ``kv_value_invalid`` when
    it is not JSON, ``kv_value_too_large`` over ``KV_MAX_VALUE_BYTES``."""
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeEncodeError):
        raise PluginStoreError("kv_value_invalid", [key]) from None
    if size > KV_MAX_VALUE_BYTES:
        raise PluginStoreError("kv_value_too_large", [key])
    return encoded, size
