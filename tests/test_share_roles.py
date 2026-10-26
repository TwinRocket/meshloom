"""``/api/share`` per role (MSHL-248): the registry, the kiosk PRIV rule, the audit rows.

Writes are ``send`` (member, admin), the cache clear and the share settings
``admin``, reads ``read`` (every role, kiosk included). A kiosk never reads an
object through a direct message: 403 ``share_role_forbidden`` before any cache
read or egress. Turning sharing on or off, a delete and a report are audited
with the actor.
"""

from __future__ import annotations

import httpx
import pytest

from app.repository import AppSettingsRepository, MessageRepository
from app.repository.auth import AuthAuditRepository
from tests.share_fakes import Dns, Recorder, json_response

# The recorder replaces httpx.AsyncClient: keep the real class for the app client.
_RealAsyncClient = httpx.AsyncClient

ROLES = ("admin", "member", "viewer", "kiosk")
WRITERS = {"admin", "member"}
PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000201a5f6b1bd0000000049454e44ae426082"
)
LINK = {"id": "AbCdEfG", "url": "https://meshl.ink/AbCdEfG", "expires_at": 1791636000}
META = {"id": "PnGokAy", "kind": "link", "target_host": "example.com", "expires_at": 1791636000}

# Every write route of /api/share, as (method, path, request kwargs).
WRITES: list[tuple[str, str, dict]] = [
    ("POST", "/api/share/links", {"json": {"url": "https://example.com/a/long/path"}}),
    (
        "POST",
        "/api/share/files",
        {"content": PNG_1X1, "headers": {"Content-Type": "image/png", "X-Share-Mode": "public"}},
    ),
    ("DELETE", "/api/share/AbCdEfG", {}),
    ("POST", "/api/share/AbCdEfG/report", {"json": {"reason": "spam"}}),
    ("GET", "/api/share/mine", {}),
]


def _service(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/links":
        return json_response(201, LINK)
    if path == "/v1/reports":
        return json_response(202, {"received_at": 1760100000})
    if path == "/v1/mine":
        return json_response(200, {"objects": [], "next_cursor": None})
    if request.method == "DELETE":
        return httpx.Response(204)
    if path.startswith("/v1/meta/"):
        return json_response(200, META)
    return httpx.Response(200, content=PNG_1X1, headers={"Content-Type": "image/png"})


@pytest.fixture
async def client():
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def off(test_db, monkeypatch):
    Dns(monkeypatch)
    return Recorder(monkeypatch, _service)


@pytest.fixture
async def on(test_db, monkeypatch):
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    Dns(monkeypatch)
    return Recorder(monkeypatch, _service)


def _code(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return None
    return detail.get("code") if isinstance(detail, dict) else None


async def _audit(*actions: str):
    rows = list(reversed(await AuthAuditRepository.list_recent(limit=1000)))
    return [row for row in rows if row.action in actions]


async def _message(msg_type: str, text: str) -> int:
    message_id = await MessageRepository.create(
        msg_type=msg_type,
        text=text,
        received_at=1791000000,
        conversation_key="0" * 64 if msg_type == "PRIV" else "1" * 32,
    )
    assert message_id is not None
    return message_id


class TestRegistry:
    @pytest.mark.parametrize("role", ROLES)
    @pytest.mark.parametrize(("method", "path", "kwargs"), WRITES)
    async def test_writes_are_member_and_admin(
        self, off, client, sign_in_as, role, method, path, kwargs
    ):
        sign_in_as(role)
        response = await client.request(method, path, **kwargs)
        if role in WRITERS:
            # Past the gate: sharing is off, so the route itself answers.
            assert _code(response) == "share_disabled", response.text
        else:
            assert response.status_code == 403
            assert response.json()["detail"]["code"] == "auth_forbidden"
            assert response.json()["detail"]["params"] == {"permission": "send"}
        assert off.requests == []

    @pytest.mark.parametrize("role", ROLES)
    async def test_cache_clear_is_admin(self, off, client, sign_in_as, role):
        sign_in_as(role)
        response = await client.post("/api/share/cache/clear")
        assert response.status_code == (200 if role == "admin" else 403)

    @pytest.mark.parametrize("role", ROLES)
    @pytest.mark.parametrize(
        "field",
        [
            {"share_enabled": False},
            {"share_shorten_mode": "never"},
            {"share_preview_policy": "never"},
        ],
    )
    async def test_share_settings_are_admin(self, off, client, sign_in_as, role, field):
        sign_in_as(role)
        response = await client.patch("/api/settings", json=field)
        assert response.status_code == (200 if role == "admin" else 403)

    @pytest.mark.parametrize("role", ROLES)
    async def test_reads_are_open_to_every_role(self, on, client, sign_in_as, role):
        sign_in_as(role)
        assert (await client.get("/api/share/status")).status_code == 200
        assert (await client.get("/api/share/PnGokAy/meta")).status_code == 200
        assert (await client.get("/api/share/PnGokAy")).status_code == 200


class TestKioskPrivate:
    async def test_a_kiosk_gets_share_role_forbidden_on_a_direct_message(
        self, on, client, sign_in_as
    ):
        private = await _message("PRIV", "look https://meshl.ink/PnGokAy")
        channel = await _message("CHAN", "look https://meshl.ink/PnGokAy")
        sign_in_as("kiosk")
        for path in ("/api/share/PnGokAy", "/api/share/PnGokAy/meta"):
            refused = await client.get(f"{path}?message_id={private}")
            assert refused.status_code == 403
            assert _code(refused) == "share_role_forbidden"
        # Refused before any cache read or egress.
        assert on.requests == []
        assert (await client.get(f"/api/share/PnGokAy?message_id={channel}")).status_code == 200
        assert (
            await client.get(f"/api/share/PnGokAy/meta?message_id={channel}")
        ).status_code == 200

    @pytest.mark.parametrize("role", ["viewer", "member", "admin"])
    async def test_other_roles_read_a_direct_message_object(self, on, client, sign_in_as, role):
        private = await _message("PRIV", "look https://meshl.ink/PnGokAy")
        sign_in_as(role)
        assert (await client.get(f"/api/share/PnGokAy?message_id={private}")).status_code == 200


class TestAudit:
    async def test_enabling_and_disabling_write_a_row_with_the_actor(self, off, client, sign_in_as):
        sign_in_as("admin")
        enabled = await client.patch(
            "/api/settings", json={"share_enabled": True, "share_terms_accepted": True}
        )
        assert enabled.status_code == 200, enabled.text
        # A repeat of the current value writes nothing.
        repeat = await client.patch("/api/settings", json={"share_enabled": True})
        assert repeat.status_code == 200
        disabled = await client.patch("/api/settings", json={"share_enabled": False})
        assert disabled.status_code == 200
        rows = await _audit("share.enabled", "share.disabled")
        assert [(row.action, row.outcome, row.actor_user_id) for row in rows] == [
            ("share.enabled", "success", 2),
            ("share.disabled", "success", 2),
        ]
        assert rows[0].details["terms_version"] >= 1

    async def test_another_setting_writes_no_share_row(self, off, client, sign_in_as):
        sign_in_as("admin")
        response = await client.patch("/api/settings", json={"share_shorten_mode": "never"})
        assert response.status_code == 200
        assert await _audit("share.enabled", "share.disabled") == []

    async def test_a_delete_writes_a_row_with_the_actor(self, on, client, sign_in_as):
        sign_in_as("member")
        assert (await client.delete("/api/share/AbCdEfG")).status_code == 204
        rows = await _audit("share.object_deleted")
        assert [(r.outcome, r.actor_user_id, r.target) for r in rows] == [
            ("success", 2, "share:AbCdEfG")
        ]

    async def test_a_report_writes_a_row_with_the_reason_only(self, on, client, sign_in_as):
        sign_in_as("member")
        response = await client.post(
            "/api/share/AbCdEfG/report", json={"reason": "spam", "details": "private words"}
        )
        assert response.status_code == 202
        rows = await _audit("share.report_sent")
        assert [(r.outcome, r.actor_user_id, r.target, r.details) for r in rows] == [
            ("success", 2, "share:AbCdEfG", {"reason": "spam"})
        ]

    async def test_a_refused_role_writes_no_row(self, on, client, sign_in_as):
        sign_in_as("viewer")
        assert (await client.delete("/api/share/AbCdEfG")).status_code == 403
        assert await _audit("share.object_deleted") == []
