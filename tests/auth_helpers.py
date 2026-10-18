"""Shared helpers for the auth gate tests (``tests/test_auth_*.py``)."""

from __future__ import annotations

import base64

import httpx

PASSWORD = "correct horse battery"
ADMIN = "owner"


def make_client(
    base_url: str = "http://test", client: tuple[str, int] = ("127.0.0.1", 50000)
) -> httpx.AsyncClient:
    from app.main import app

    transport = httpx.ASGITransport(app=app, client=client)
    return httpx.AsyncClient(transport=transport, base_url=base_url)


def basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


async def create_user(username: str = ADMIN, password: str | None = PASSWORD, role: str = "admin"):
    from app.auth.passwords import hash_password_async
    from app.repository.auth import UserRepository

    password_hash = await hash_password_async(password) if password else None
    return await UserRepository.create(username, role, password_hash=password_hash)  # type: ignore[arg-type]


async def login(client: httpx.AsyncClient, username: str = ADMIN, password: str = PASSWORD):
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert response.status_code == 200, response.text
    return response


def set_cookie_headers(response: httpx.Response) -> list[str]:
    """The ``Set-Cookie`` headers other than the known-device cookie
    (``app/auth/device_cookie.py``, tested in ``tests/test_auth_ratelimit.py``)."""
    return [c for c in response.headers.get_list("set-cookie") if "meshloom_device=" not in c]
