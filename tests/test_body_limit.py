"""Request body limit (app/middleware/body_limit.py), through the real application."""

from collections.abc import AsyncIterator

import pytest

from tests.auth_helpers import ADMIN, PASSWORD, create_user, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]

MIB = 1024 * 1024


async def _chunks(total: int, size: int = 64 * 1024) -> AsyncIterator[bytes]:
    sent = 0
    while sent < total:
        yield b"x" * size
        sent += size


def _code(response) -> str:
    return response.json()["detail"]["code"]


async def test_an_anonymous_oversized_body_is_refused_on_its_declared_length():
    async with make_client() as client:
        response = await client.post(
            "/api/auth/login",
            content=b"x" * (2 * MIB),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert _code(response) == "request_too_large"
    assert response.headers["connection"] == "close"


async def test_an_anonymous_chunked_body_is_cut_at_the_limit():
    from app.middleware import body_limit

    consumed = 0

    async def counting() -> AsyncIterator[bytes]:
        nonlocal consumed
        async for chunk in _chunks(64 * MIB):
            consumed += len(chunk)
            yield chunk

    async with make_client() as client:
        response = await client.post(
            "/api/auth/login",
            content=counting(),
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert _code(response) == "request_too_large"
    # Nothing past the chunk that crossed the limit was asked for.
    assert consumed <= body_limit.DEFAULT_MAX_BODY_BYTES + 2 * 64 * 1024


async def test_a_body_under_the_limit_reaches_the_route():
    await create_user()
    async with make_client() as client:
        response = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": PASSWORD}
        )
    assert response.status_code == 200, response.text


async def test_a_refusal_is_not_answered_to_a_body_the_route_never_reads():
    """A small GET with a body stays what it was."""
    async with make_client() as client:
        response = await client.request("GET", "/api/health", content=b"x" * 16)
    assert response.status_code == 200


async def _admin_client(client) -> None:
    await create_user()
    login = await client.post("/api/auth/login", json={"username": ADMIN, "password": PASSWORD})
    assert login.status_code == 200, login.text


@pytest.mark.parametrize("path", ["/api/settings/backup/inspect", "/api/settings/backup/restore"])
async def test_a_backup_over_the_default_limit_still_reaches_its_route(path):
    """Up to the route's own 64 MiB: here an invalid file, refused by the route after reading."""
    async with make_client() as client:
        await _admin_client(client)
        response = await client.post(path, content=b"x" * (2 * MIB))
    assert response.status_code == 400, response.text
    assert _code(response) == "backup_invalid"


@pytest.mark.parametrize("path", ["/api/settings/backup/inspect", "/api/settings/backup/restore"])
@pytest.mark.parametrize("chunked", [False, True])
async def test_a_backup_over_its_own_limit_keeps_its_own_refusal(path, chunked):
    from app.services.backup import MAX_BACKUP_BODY_BYTES

    size = MAX_BACKUP_BODY_BYTES + MIB
    async with make_client() as client:
        await _admin_client(client)
        response = await client.post(path, content=_chunks(size) if chunked else b"x" * size)
    assert response.status_code == 413
    assert _code(response) == "backup_too_large"


async def test_a_self_limited_route_reads_nothing_from_an_anonymous_caller():
    """Leaving a route to its own cap is no way around the gate."""
    consumed = 0

    async def counting() -> AsyncIterator[bytes]:
        nonlocal consumed
        async for chunk in _chunks(8 * MIB):
            consumed += len(chunk)
            yield chunk

    await create_user()
    async with make_client() as client:
        response = await client.post("/api/settings/backup/restore", content=counting())
    assert response.status_code == 401
    assert consumed <= 64 * 1024


async def test_a_large_plugin_source_is_not_refused_by_the_default_limit():
    """1 MiB of source takes more than 1 MiB once written as JSON: the route's own check decides."""
    from app.routers.plugins import MAX_SOURCE_BYTES

    source = '"' * (MAX_SOURCE_BYTES - 1)
    async with make_client() as client:
        await _admin_client(client)
        response = await client.put("/api/plugins/nope/source", json={"source": source})
    assert response.status_code == 404, response.text


def test_the_caps_follow_the_routes_own_limits():
    from app.middleware import body_limit
    from app.routers.plugins import MAX_SOURCE_BYTES

    # These read in chunks and refuse past their own cap, with their own error code.
    assert body_limit.limit_for("/api/settings/backup/inspect") is None
    assert body_limit.limit_for("/api/settings/backup/restore") is None
    assert body_limit.limit_for("/api/share/files") is None
    # \uXXXX is the longest JSON writes a byte.
    assert body_limit.limit_for("/api/plugins/abc/source") >= 6 * MAX_SOURCE_BYTES
    for path in (
        "/api/auth/login",
        "/api/settings/backup/inspect/",
        "/api/settings/backup",
        "/api/plugins/abc/source/x",
        "/api/plugins/a/b/source",
        "/",
    ):
        assert body_limit.limit_for(path) == body_limit.DEFAULT_MAX_BODY_BYTES
