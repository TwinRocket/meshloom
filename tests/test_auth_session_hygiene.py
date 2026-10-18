"""Session hygiene (MSHL-57): revocation closes sockets, cap, purge, throttled last-seen."""

import asyncio
import time
from unittest.mock import AsyncMock

import pytest

from app import websocket as ws_module
from app.auth import sessions
from app.auth.models import User
from app.auth.principal import Principal
from app.repository.auth import MAX_SESSIONS_PER_USER, AuthSessionRepository, UserRepository
from app.services import stale_contacts
from app.websocket import REVOKED_CLOSE_CODE, ws_manager
from tests.auth_helpers import PASSWORD, create_user, login, make_client

pytestmark = [pytest.mark.real_auth, pytest.mark.usefixtures("test_db", "fast_argon2")]


class ManagedSocket:
    """A fake socket handed to the real ``ws_manager``: records its close."""

    def __init__(self) -> None:
        self.ws = AsyncMock()
        self.closed = asyncio.Event()
        self.code: int | None = None

        async def close(code: int = 1000, **_):
            self.code = code
            self.closed.set()

        self.ws.close.side_effect = close

    async def wait_closed(self, timeout: float = 1.0) -> int | None:
        await asyncio.wait_for(self.closed.wait(), timeout=timeout)
        return self.code


@pytest.fixture
async def managed():
    opened: list[ManagedSocket] = []

    async def open_socket(user: User, session_id: str | None, *, token_id: str | None = None):
        socket = ManagedSocket()
        principal = Principal(
            user=user,
            via="lan",
            credential="token" if token_id else "session",
            session_id=session_id,
            token_id=token_id,
        )
        await ws_manager.connect(socket.ws, principal)
        opened.append(socket)
        return socket

    yield open_socket
    for socket in opened:
        await ws_manager.disconnect(socket.ws)
    task = ws_manager._recheck_task
    if task is not None and not task.done():
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _session_ids(response_json: list[dict]) -> tuple[str, str]:
    current = next(s["id"] for s in response_json if s["current"])
    other = next(s["id"] for s in response_json if not s["current"])
    return current, other


async def test_revoking_a_session_closes_its_websocket_within_one_second(managed):
    user = await create_user()
    async with make_client() as here, make_client() as there:
        await login(here)
        await login(there)
        current, other = _session_ids((await here.get("/api/auth/sessions")).json())
        mine, theirs = await managed(user, current), await managed(user, other)

        response = await here.delete(f"/api/auth/sessions/{other}")
        assert response.status_code == 200
        assert await theirs.wait_closed(timeout=1.0) == REVOKED_CLOSE_CODE
        assert not mine.closed.is_set()
        assert mine.ws in ws_manager.active_connections
        assert theirs.ws not in ws_manager.active_connections


async def test_password_change_closes_the_other_sessions_sockets_and_logout_its_own(managed):
    user = await create_user()
    async with make_client() as here, make_client() as there:
        await login(here)
        await login(there)
        current, other = _session_ids((await here.get("/api/auth/sessions")).json())
        mine, theirs = await managed(user, current), await managed(user, other)

        response = await here.patch(
            "/api/auth/me",
            json={"current_password": PASSWORD, "new_password": "another long passphrase"},
        )
        assert response.status_code == 200
        assert await theirs.wait_closed() == REVOKED_CLOSE_CODE
        assert not mine.closed.is_set()

        assert (await here.post("/api/auth/logout")).status_code == 200
        assert await mine.wait_closed() == REVOKED_CLOSE_CODE


async def test_login_never_promotes_a_planted_session_id():
    await create_user()
    async with make_client() as victim:
        await login(victim)
        planted = victim.cookies.get("meshloom_session")
        response = await login(victim)
        issued = victim.cookies.get("meshloom_session")
        assert issued and issued != planted
        assert len(await AuthSessionRepository.list_for_user(1)) == 2
        assert response.json()["authenticated"] is True


async def test_the_51st_login_revokes_the_oldest_session_and_closes_its_socket(managed):
    user = await create_user()
    first = await sessions.issue_session(user, ip=None, user_agent=None, via="lan")
    oldest = await managed(user, first.session.id)
    for _ in range(MAX_SESSIONS_PER_USER - 1):
        await sessions.issue_session(user, ip=None, user_agent=None, via="lan")
    assert len(await AuthSessionRepository.list_for_user(user.id)) == MAX_SESSIONS_PER_USER
    assert not oldest.closed.is_set()

    async with make_client() as client:
        await login(client)
    active = await AuthSessionRepository.list_for_user(user.id)
    assert len(active) == MAX_SESSIONS_PER_USER
    assert first.session.id not in {s.id for s in active}
    assert await oldest.wait_closed() == REVOKED_CLOSE_CODE


async def test_the_housekeeping_loop_purges_expired_and_revoked_sessions(test_db, monkeypatch):
    user = await create_user()
    now = int(time.time())
    expired = await AuthSessionRepository.create(user.id, "e" * 64, lifetime_s=10, now=now - 100)
    revoked = await AuthSessionRepository.create(user.id, "r" * 64, lifetime_s=10**6, now=now)
    live = await AuthSessionRepository.create(user.id, "l" * 64, lifetime_s=10**6, now=now)
    await AuthSessionRepository.revoke(revoked.id)

    sleeps = 0

    async def fake_sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps > 1:  # the first sleep is the start delay; stop after one pass
            raise asyncio.CancelledError

    monkeypatch.setattr(stale_contacts.asyncio, "sleep", fake_sleep)
    # The other steps are covered by tests/test_stale_contacts.py.
    for step in ("purge_stale_contacts", "prune_raw_packets", "reclaim_free_pages"):
        monkeypatch.setattr(stale_contacts, step, AsyncMock(return_value=0))
    with pytest.raises(asyncio.CancelledError):
        await stale_contacts._stale_purge_loop()

    async with test_db.readonly() as conn:
        async with conn.execute("SELECT id FROM sessions") as cursor:
            remaining = {row["id"] for row in await cursor.fetchall()}
    assert remaining == {live.id}
    assert expired.id not in remaining


async def test_a_hundred_requests_in_a_minute_write_last_seen_once(monkeypatch):
    await create_user()
    async with make_client() as client:
        await login(client)
        [session] = await AuthSessionRepository.list_for_user(1)

        clock = [float(session.last_seen_at + 60)]
        monkeypatch.setattr(time, "time", lambda: clock[0])
        writes = 0
        real_touch = AuthSessionRepository.touch

        async def counting_touch(*args, **kwargs):
            nonlocal writes
            written = await real_touch(*args, **kwargs)
            writes += int(written)
            return written

        monkeypatch.setattr(AuthSessionRepository, "touch", counting_touch)
        for _ in range(100):
            assert (await client.get("/api/auth/me")).status_code == 200
            clock[0] += 0.5  # 100 requests over 50 seconds

    assert writes == 1
    [after] = await AuthSessionRepository.list_for_user(1, now=int(clock[0]))
    assert after.last_seen_at == session.last_seen_at + 60


async def test_the_recheck_closes_sockets_revoked_from_another_process(managed, monkeypatch):
    """``meshloom-admin`` writes the database directly; the periodic recheck closes them."""
    monkeypatch.setattr(ws_module, "SESSION_RECHECK_INTERVAL_SECONDS", 0.01)
    user = await create_user()
    other = await create_user("member1", role="member")
    kept = await sessions.issue_session(user, ip=None, user_agent=None, via="lan")
    revoked = await sessions.issue_session(user, ip=None, user_agent=None, via="lan")
    disabled = await sessions.issue_session(other, ip=None, user_agent=None, via="lan")
    kept_socket = await managed(user, kept.session.id)
    revoked_socket = await managed(user, revoked.session.id)
    disabled_socket = await managed(other, disabled.session.id)

    await AuthSessionRepository.revoke(revoked.session.id)
    await UserRepository.update(other.id, disabled=True)

    assert await revoked_socket.wait_closed() == REVOKED_CLOSE_CODE
    assert await disabled_socket.wait_closed() == REVOKED_CLOSE_CODE
    await asyncio.sleep(0.05)
    assert not kept_socket.closed.is_set()


async def test_the_recheck_stops_once_no_session_socket_is_open(managed, monkeypatch):
    monkeypatch.setattr(ws_module, "SESSION_RECHECK_INTERVAL_SECONDS", 0.01)
    user = await create_user()
    issued = await sessions.issue_session(user, ip=None, user_agent=None, via="lan")
    socket = await managed(user, issued.session.id)
    task = ws_manager._recheck_task
    assert task is not None and not task.done()
    await ws_manager.disconnect(socket.ws)
    await asyncio.wait_for(task, timeout=1.0)


async def test_closing_a_token_closes_only_its_sockets(managed):
    user = await create_user()
    token_socket = await managed(user, None, token_id="tok1")
    other_socket = await managed(user, None, token_id="tok2")
    assert ws_manager.close_tokens(["tok1"]) == 1
    assert await token_socket.wait_closed() == REVOKED_CLOSE_CODE
    assert not other_socket.closed.is_set()
