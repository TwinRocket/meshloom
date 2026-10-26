"""`contacts.list {limit?, offset?}` and `contacts.get {key}` (permission
`contacts.read`): read-only projections of `ContactRepository`.

Each contact is `{public_key, name, type, last_seen, last_advert, lat, lon,
favorite}`: names, keys, last activity and positions, never routes or read
state. `list` pages by `limit` (1 to `LIST_MAX`, default 100) and `offset`;
`get` takes a full 64-hex key and answers `null` when unknown.
"""

import re
from typing import Any

from app.plugins.capabilities.base import Call, capability, invalid_params

LIST_MAX = 200
_KEY_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


def project(contact: Any) -> dict[str, Any]:
    return {
        "public_key": contact.public_key,
        "name": contact.name,
        "type": contact.type,
        "last_seen": contact.last_seen,
        "last_advert": contact.last_advert,
        "lat": contact.lat,
        "lon": contact.lon,
        "favorite": contact.favorite,
    }


def _int(params: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = params.get(name, default)
    if type(value) is not int or not low <= value <= high:
        raise invalid_params(f"{name} is {low} to {high}", fields=[name])
    return value


def list_args(params: dict[str, Any]) -> tuple[int, int]:
    """`(limit, offset)` of a `contacts.list`, `invalid_params` when out of range."""
    return _int(params, "limit", 100, 1, LIST_MAX), _int(params, "offset", 0, 0, 10**9)


def key_arg(params: dict[str, Any]) -> str:
    """The `key` of a `contacts.get`: a full 64-hex public key, else `invalid_params`."""
    key = params.get("key")
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise invalid_params("key is a 64-hex public key", fields=["key"])
    return key


@capability("contacts.list", "contacts.read")
async def list_(call: Call) -> list[dict[str, Any]]:
    from app.repository import ContactRepository

    limit, offset = list_args(call.params)
    return [project(c) for c in await ContactRepository.get_all(limit=limit, offset=offset)]


@capability("contacts.get", "contacts.read")
async def get(call: Call) -> dict[str, Any] | None:
    from app.repository import ContactRepository

    key = key_arg(call.params)
    contact = await ContactRepository.get_by_key(key)
    return project(contact) if contact is not None else None
