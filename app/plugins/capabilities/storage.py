"""`storage.get {key}`, `storage.set {key, value}`, `storage.delete {key}`,
`storage.list {prefix?, after?, limit?}` (permission `storage`).

The instance's rows of `plugin_kv` (`PluginKvRepository`): JSON values, keys of
1 to 256 UTF-8 bytes, 256 KiB per value, 5 MiB per instance (keys plus values).
A write past the 5 MiB is `quota_exceeded` (`params.limit`); a bad key or value
is `invalid_params`. `get` answers `null` for an absent key. `list` answers the
keys in order, at most `limit` (default and maximum `LIST_MAX`), after `after`.
"""

from typing import Any

from app.plugins.capabilities.base import Call, CapabilityError, capability, invalid_params

LIST_MAX = 200


def _key(params: dict[str, Any], name: str = "key", required: bool = True) -> str | None:
    value = params.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str):
        raise invalid_params(f"{name} must be a string", fields=[name])
    return value


def _store_error(exc: Exception) -> CapabilityError:
    from app.repository.plugins import KV_MAX_VALUE_BYTES, KV_QUOTA_BYTES

    code = getattr(exc, "code", "")
    if code == "kv_quota_exceeded":
        return CapabilityError(
            "quota_exceeded", "the storage of this instance is full", {"limit": KV_QUOTA_BYTES}
        )
    if code == "kv_value_too_large":
        return invalid_params("the value is too large", fields=["value"], limit=KV_MAX_VALUE_BYTES)
    if code == "kv_key_invalid":
        return invalid_params("invalid key", fields=["key"])
    return invalid_params("the value is not JSON", fields=["value"])


@capability("storage.get", "storage")
async def get(call: Call) -> Any:
    from app.repository.plugins import PluginKvRepository

    key = _key(call.params)
    assert key is not None
    return await PluginKvRepository.get(call.instance.instance_id, key)


@capability("storage.set", "storage")
async def set_(call: Call) -> None:
    from app.repository.plugins import PluginKvRepository, PluginStoreError

    key = _key(call.params)
    assert key is not None
    if "value" not in call.params:
        raise invalid_params("value is required", fields=["value"])
    try:
        await PluginKvRepository.set(call.instance.instance_id, key, call.params["value"])
    except PluginStoreError as exc:
        raise _store_error(exc) from None


@capability("storage.delete", "storage")
async def delete(call: Call) -> None:
    from app.repository.plugins import PluginKvRepository

    key = _key(call.params)
    assert key is not None
    await PluginKvRepository.delete(call.instance.instance_id, key)


@capability("storage.list", "storage")
async def list_(call: Call) -> list[str]:
    from app.repository.plugins import PluginKvRepository

    prefix = _key(call.params, "prefix", required=False) or ""
    after = _key(call.params, "after", required=False)
    limit = call.params.get("limit", LIST_MAX)
    if type(limit) is not int or not 1 <= limit <= LIST_MAX:
        raise invalid_params(f"limit is 1 to {LIST_MAX}", fields=["limit"])
    keys = [
        k
        for k in await PluginKvRepository.keys(call.instance.instance_id)
        if k.startswith(prefix) and (after is None or k > after)
    ]
    return keys[:limit]
