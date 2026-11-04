"""Request body limit (app/middleware/body_limit.py), through the real application."""

import asyncio
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


# ---------------------------------------------------------------- deadline and shared budget


def _asgi_scope(path: str = "/api/auth/login", length: int | None = None):
    headers = [] if length is None else [(b"content-length", str(length).encode())]
    return {
        "type": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "method": "POST",
        "headers": headers,
        "state": {},
    }


async def _answer(app, scope, messages) -> list[dict]:
    """Run ``app`` with ``messages`` as the request; past them, ``receive`` never returns."""
    pending = list(messages)
    sent: list[dict] = []

    async def receive():
        if pending:
            return pending.pop(0)
        await asyncio.Event().wait()

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


def _reading_app():
    """A route that reads its whole body, through the real error handling of FastAPI."""
    from fastapi import FastAPI, Request

    from app.middleware.body_limit import BodyLimitMiddleware

    app = FastAPI()

    @app.post("/api/auth/login")
    @app.post("/api/settings/backup/restore")
    async def read(request: Request):
        return {"size": len(await request.body())}

    return BodyLimitMiddleware(app)


def _status(sent: list[dict]) -> int:
    return next(m["status"] for m in sent if m["type"] == "http.response.start")


def _headers(sent: list[dict]) -> dict[bytes, bytes]:
    return dict(next(m["headers"] for m in sent if m["type"] == "http.response.start"))


@pytest.fixture
def _clean_budget():
    from app.middleware import body_limit

    body_limit.reset_budget()
    yield body_limit
    assert body_limit.budget_in_use() == 0
    body_limit.reset_budget()


async def test_a_body_that_stops_arriving_is_refused_at_the_deadline(_clean_budget, monkeypatch):
    monkeypatch.setattr(_clean_budget, "BODY_READ_DEADLINE_S", 0.05)
    sent = await _answer(
        _reading_app(),
        _asgi_scope(length=100),
        [{"type": "http.request", "body": b"x" * 99, "more_body": True}],
    )
    assert _status(sent) == 408
    assert _headers(sent)[b"connection"] == b"close"


async def test_the_deadline_counts_from_the_first_read_not_from_the_request(
    _clean_budget, monkeypatch
):
    """A busy gate or a slow Pi before the route reads costs the client nothing."""
    from fastapi import FastAPI, Request

    from app.middleware.body_limit import BodyLimitMiddleware

    monkeypatch.setattr(_clean_budget, "BODY_READ_DEADLINE_S", 0.05)
    app = FastAPI()

    @app.post("/api/auth/login")
    async def slow_then_read(request: Request):
        await asyncio.sleep(0.1)
        return {"size": len(await request.body())}

    sent = await _answer(
        BodyLimitMiddleware(app),
        _asgi_scope(length=3),
        [{"type": "http.request", "body": b"abc", "more_body": False}],
    )
    assert _status(sent) == 200


async def test_a_self_limited_route_has_no_deadline(_clean_budget, monkeypatch):
    """A large backup restore over a slow link is signed in and bounded by its own cap."""
    monkeypatch.setattr(_clean_budget, "BODY_READ_DEADLINE_S", 0.01)
    monkeypatch.setattr(_clean_budget, "MIN_BODY_RATE_BYTES_PER_S", 10**12)
    messages = [{"type": "http.request", "body": b"x" * 10, "more_body": True}] * 3
    pending = list(messages) + [{"type": "http.request", "body": b"", "more_body": False}]
    sent: list[dict] = []

    async def slow_receive():
        await asyncio.sleep(0.03)
        return pending.pop(0)

    async def send(message):
        sent.append(message)

    await _reading_app()(_asgi_scope("/api/settings/backup/restore"), slow_receive, send)
    assert _status(sent) == 200


def test_the_deadline_grows_with_a_larger_cap():
    from app.middleware import body_limit

    assert body_limit.deadline_for(body_limit.DEFAULT_MAX_BODY_BYTES) == (
        body_limit.BODY_READ_DEADLINE_S
    )
    assert body_limit.deadline_for(8 * MIB) > body_limit.BODY_READ_DEADLINE_S


async def test_large_anonymous_bodies_share_a_budget_and_small_ones_do_not(
    _clean_budget, monkeypatch
):
    """Past the budget a large anonymous body is refused before it is read; a login still passes."""
    body_limit = _clean_budget
    app = _reading_app()
    holders: list[asyncio.Task] = []
    release = asyncio.Event()
    size = MIB

    def holder() -> asyncio.Task:
        started = {"count": 0}

        async def receive():
            if started["count"] == 0:
                started["count"] = 1
                return {"type": "http.request", "body": b"x" * 1024, "more_body": True}
            await release.wait()
            return {"type": "http.disconnect"}

        async def send(_message):
            return None

        async def run():
            try:
                await app(_asgi_scope(length=size), receive, send)
            except Exception:  # noqa: BLE001 - the disconnect ends this request; not under test
                pass

        return asyncio.create_task(run())

    fits = body_limit.ANONYMOUS_BUDGET_BYTES // (size - body_limit.SMALL_ANONYMOUS_BODY_BYTES)
    holders = [holder() for _ in range(fits)]
    await asyncio.sleep(0.01)
    assert body_limit.budget_in_use() > 0

    refused = await _answer(app, _asgi_scope(length=size), [])
    assert _status(refused) == 503
    assert _headers(refused)[b"connection"] == b"close"
    assert b"retry-after" in _headers(refused)

    small = await _answer(
        app,
        _asgi_scope(length=512),
        [{"type": "http.request", "body": b"x" * 512, "more_body": False}],
    )
    assert _status(small) == 200

    release.set()
    await asyncio.gather(*holders)
    assert body_limit.budget_in_use() == 0

    # Freed: the same body goes through.
    passes = await _answer(
        app,
        _asgi_scope(length=size),
        [{"type": "http.request", "body": b"x" * size, "more_body": False}],
    )
    assert _status(passes) == 200


async def test_a_chunked_anonymous_body_enters_the_budget_as_it_grows(_clean_budget, monkeypatch):
    body_limit = _clean_budget
    monkeypatch.setattr(body_limit, "ANONYMOUS_BUDGET_BYTES", 64 * 1024)
    chunk = {"type": "http.request", "body": b"x" * (32 * 1024), "more_body": True}
    sent = await _answer(_reading_app(), _asgi_scope(), [chunk] * 4)
    assert _status(sent) == 503


async def test_a_signed_in_request_is_outside_the_budget(_clean_budget, monkeypatch):
    from app.auth.models import User
    from app.auth.principal import Principal, set_principal

    body_limit = _clean_budget
    monkeypatch.setattr(body_limit, "ANONYMOUS_BUDGET_BYTES", 1)
    scope = _asgi_scope(length=64 * 1024)
    user = User.model_construct(id=1, role="admin")
    set_principal(scope, Principal(user=user, via="lan", credential="session"))
    sent = await _answer(
        _reading_app(),
        scope,
        [{"type": "http.request", "body": b"x" * (64 * 1024), "more_body": False}],
    )
    assert _status(sent) == 200


async def test_an_anonymous_refusal_with_an_unread_body_closes_the_connection():
    await create_user()
    async with make_client() as client:
        response = await client.post("/api/contacts/bulk-delete", json={"public_keys": []})
    assert response.status_code == 401
    assert response.headers["connection"] == "close"


async def test_a_self_limited_route_keeps_the_connection_on_a_refusal():
    """Closing over a large unread upload can reset the socket and lose the 401 itself."""
    await create_user()
    async with make_client() as client:
        response = await client.post("/api/settings/backup/restore", content=b"x" * 1024)
    assert response.status_code == 401
    assert "connection" not in response.headers


async def test_an_answered_request_keeps_its_connection():
    await create_user()
    async with make_client() as client:
        response = await client.post(
            "/api/auth/login", json={"username": ADMIN, "password": PASSWORD}
        )
    assert response.status_code == 200
    assert "connection" not in response.headers
