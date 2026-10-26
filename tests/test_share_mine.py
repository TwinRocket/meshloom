"""``GET /api/share/mine`` and ``DELETE /api/share/{id}`` (MSHL-247): roles, egress, eviction."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.repository import AppSettingsRepository
from app.services import share
from app.services.share_cache import (
    CachedObject,
    ShareCache,
    reset_share_cache_for_tests,
    share_cache,
)
from tests.share_fakes import Dns, Recorder, json_response

_RealAsyncClient = httpx.AsyncClient

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f0005000201a5f6b1bd0000000049454e44ae426082"
)
MINE = {
    "objects": [
        {
            "id": "AcTiVe1",
            "kind": "file",
            "status": "active",
            "blocked_category": None,
            "created_at": 1791000000,
            "expires_at": 1791636000,
            "mode": "public",
            "content_type": "image/png",
            "size": 68,
            "future_member": "ignored",
        },
        {
            "id": "BkQcKed",
            "kind": "link",
            "status": "blocked",
            "blocked_category": "phishing",
            "created_at": 1790000000,
            "expires_at": 1798000000,
            "target_host": "example.com",
        },
        {
            "id": "EnCrYpt",
            "kind": "file",
            "status": "blocked",
            "blocked_category": "a-new-category",
            "mode": "encrypted",
            "content_type": "application/octet-stream",
            "size_kib": 12,
        },
        # Outside the contract: dropped, never shown.
        {"id": "../etc", "kind": "file", "status": "active"},
        {"id": "WeIrDkd", "kind": "folder", "status": "active"},
    ],
    "next_cursor": "c2",
}


def _service(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/v1/mine":
        return json_response(200, MINE)
    if request.method == "DELETE":
        share_id = path.rsplit("/", 1)[1]
        if share_id == "BkQcKed":
            return json_response(410, {"code": "gone"})
        if share_id == "UtHeRkY":
            return json_response(403, {"code": "not_owner"})
        if share_id == "MisSiNg":
            return json_response(404, {"code": "not_found"})
        return httpx.Response(204)
    if path.startswith("/v1/meta/"):
        return json_response(200, {"id": path.rsplit("/", 1)[1], "kind": "link"})
    return httpx.Response(200, content=PNG_1X1, headers={"Content-Type": "image/png"})


@pytest.fixture
async def client():
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
async def on(test_db, monkeypatch):
    await AppSettingsRepository.update(share_enabled=True, share_terms_accepted=True)
    Dns(monkeypatch)
    return Recorder(monkeypatch, _service)


@pytest.fixture
async def off(test_db, monkeypatch):
    Dns(monkeypatch)
    return Recorder(monkeypatch, _service)


@pytest.fixture
def anonymous(monkeypatch):
    """Requests carry no credentials at all."""
    from app.auth import middleware

    async def _resolve(scope, headers, via, secure):
        return middleware._Resolution()

    monkeypatch.setattr(middleware, "resolve", _resolve)


def _code(response: httpx.Response) -> str:
    return response.json()["detail"]["code"]


class TestList:
    async def test_lists_the_contract_objects_only(self, on, client):
        response = await client.get("/api/share/mine")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["next_cursor"] == "c2"
        assert [o["id"] for o in body["objects"]] == ["AcTiVe1", "BkQcKed", "EnCrYpt"]
        active, blocked, encrypted = body["objects"]
        assert active["blocked_category"] is None
        assert "future_member" not in active
        assert (blocked["status"], blocked["blocked_category"]) == ("blocked", "phishing")
        assert blocked["target_host"] == "example.com"
        # A category outside the contract list is shown as "other".
        assert encrypted["blocked_category"] == "other"
        assert encrypted["size_kib"] == 12
        (request,) = on.requests
        assert request.url.path == "/v1/mine"
        assert request.url.params["limit"] == "100"
        # Tokened: the binding is the literal "mine".
        assert request.headers["authorization"].startswith("Bearer ")

    async def test_passes_limit_and_cursor(self, on, client):
        response = await client.get("/api/share/mine?limit=20&cursor=abc_-.~=")
        assert response.status_code == 200
        params = on.requests[0].url.params
        assert (params["limit"], params["cursor"]) == ("20", "abc_-.~=")

    @pytest.mark.parametrize(
        "query",
        ["?other=1", "?limit=1&limit=2", "?cursor=a%26limit%3D5", "?cursor=a/b"],
    )
    async def test_refuses_unexpected_queries_without_a_request(self, on, client, query):
        response = await client.get(f"/api/share/mine{query}")
        assert response.status_code == 400
        assert _code(response) == "share_invalid_request"
        assert on.requests == []

    @pytest.mark.parametrize("query", ["?limit=0", "?limit=201", f"?cursor={'a' * 513}"])
    async def test_refuses_out_of_bounds(self, on, client, query):
        assert (await client.get(f"/api/share/mine{query}")).status_code == 422
        assert on.requests == []

    async def test_off_is_share_disabled_with_no_egress(self, off, client):
        response = await client.get("/api/share/mine")
        assert response.status_code == 403
        assert _code(response) == "share_disabled"
        assert off.requests == []

    async def test_a_banned_instance_still_lists(self, on, client):
        share._note_banned()
        try:
            response = await client.get("/api/share/mine")
            assert response.status_code == 200
            deleted = await client.delete("/api/share/AcTiVe1")
            assert deleted.status_code == 403
            assert _code(deleted) == "share_banned"
        finally:
            share.note_share_settings_changed(enabled=True)
        assert [r.method for r in on.requests] == ["GET"]


class TestDelete:
    async def test_deletes_and_evicts_the_local_copies(self, on, client):
        assert (await client.get("/api/share/AcTiVe1")).status_code == 200
        assert (await client.get("/api/share/AcTiVe1/meta")).status_code == 200
        assert await share_cache().get("AcTiVe1") is not None
        on.requests.clear()
        response = await client.delete("/api/share/AcTiVe1")
        assert response.status_code == 204
        (request,) = on.requests
        assert (request.method, request.url.path) == ("DELETE", "/v1/objects/AcTiVe1")
        assert request.headers["authorization"].startswith("Bearer ")
        assert await share_cache().get("AcTiVe1") is None
        # Remembered as gone: neither the bytes nor the meta are asked again.
        for path in ("/api/share/AcTiVe1", "/api/share/AcTiVe1/meta"):
            gone = await client.get(path)
            assert gone.status_code == 410
        assert len(on.requests) == 1

    async def test_blocked_is_not_deletable_and_is_evicted(self, on, client):
        await share_cache().set("BkQcKed", CachedObject(content=PNG_1X1, content_type="image/png"))
        response = await client.delete("/api/share/BkQcKed")
        assert response.status_code == 409
        assert _code(response) == "share_object_blocked"
        assert await share_cache().get("BkQcKed") is None

    async def test_another_keys_object_is_not_owner(self, on, client):
        await share_cache().set("UtHeRkY", CachedObject(content=PNG_1X1, content_type="image/png"))
        response = await client.delete("/api/share/UtHeRkY")
        assert response.status_code == 403
        assert _code(response) == "share_not_owner"
        # Not ours, still there: a reader of that message keeps its copy.
        assert await share_cache().get("UtHeRkY") is not None

    async def test_a_disk_delete_failure_never_serves_the_deleted_object(
        self, on, client, monkeypatch, caplog
    ):
        now = [1000.0]
        monkeypatch.setattr(share._state, "clock", lambda: now[0])
        assert (await client.get("/api/share/AcTiVe1")).status_code == 200

        def broken(self, key: str) -> bool:
            raise OSError(28, "No space left on device", "/data/share-cache/x")

        monkeypatch.setattr(ShareCache, "_delete", broken)
        on.requests.clear()
        caplog.clear()
        with caplog.at_level("WARNING", logger="app.services.share"):
            response = await client.delete("/api/share/AcTiVe1")
        assert response.status_code == 204
        (request,) = on.requests
        assert request.method == "DELETE"
        logged = " ".join(r.getMessage() for r in caplog.records if r.name == "app.services.share")
        assert "OSError" in logged
        assert "AcTiVe1" not in logged
        assert "share-cache" not in logged
        # The bytes are still on disk, yet never served again: not even once the
        # negative cache has expired, as long as the disk entry can live.
        assert await share_cache().get("AcTiVe1") is not None
        for elapsed in (0.0, share.NEGATIVE_CACHE_S + 1, share_cache().ttl_s - 1):
            now[0] = 1000.0 + elapsed
            gone = await client.get("/api/share/AcTiVe1")
            assert gone.status_code == 410
            assert _code(gone) == "share_gone"
        assert len(on.requests) == 1

    async def test_a_disk_delete_failure_on_not_found_is_still_not_found(
        self, on, client, monkeypatch
    ):
        await share_cache().set("MisSiNg", CachedObject(content=PNG_1X1, content_type="image/png"))

        def broken(self, key: str) -> bool:
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(ShareCache, "_delete", broken)
        response = await client.delete("/api/share/MisSiNg")
        assert response.status_code == 404
        assert _code(response) == "share_not_found"
        assert (await client.get("/api/share/MisSiNg")).status_code == 410

    async def test_a_read_of_the_disk_cache_racing_the_delete_is_gone(
        self, on, client, monkeypatch
    ):
        assert (await client.get("/api/share/AcTiVe1")).status_code == 200
        cache = share_cache()
        real_get = cache.get
        reading, release = asyncio.Event(), asyncio.Event()

        async def slow_get(key: str):
            value = await real_get(key)  # the bytes, read before the delete
            reading.set()
            await release.wait()
            return value

        monkeypatch.setattr(cache, "get", slow_get)
        read = asyncio.create_task(share.read_object("AcTiVe1"))
        await reading.wait()
        await share.delete_object("AcTiVe1")
        release.set()
        with pytest.raises(share.ShareError) as exc:
            await read
        assert exc.value.code == "share_gone"

    async def test_a_fetch_racing_the_delete_is_gone_and_not_stored(self, on, client, monkeypatch):
        real_fetch = share.fetch_object
        fetching, release = asyncio.Event(), asyncio.Event()

        async def slow_fetch(share_id: str):
            fetched = await real_fetch(share_id)  # answered before the delete
            fetching.set()
            await release.wait()
            return fetched

        monkeypatch.setattr(share, "fetch_object", slow_fetch)
        cache = share_cache()
        real_set = cache.set
        stores: list[str] = []

        async def spy_set(key: str, obj: CachedObject) -> None:
            stores.append(key)
            await real_set(key, obj)

        monkeypatch.setattr(cache, "set", spy_set)
        read = asyncio.create_task(share.read_object("AcTiVe1"))
        await fetching.wait()
        await share.delete_object("AcTiVe1")
        release.set()
        with pytest.raises(share.ShareError) as exc:
            await read
        assert exc.value.code == "share_gone"
        # The post-fetch check refuses it before the store, not after.
        assert stores == []
        assert await share_cache().get("AcTiVe1") is None

    async def test_a_deleted_id_never_reads_the_disk_cache(self, on, client, monkeypatch):
        assert (await client.get("/api/share/AcTiVe1")).status_code == 200
        monkeypatch.setattr(ShareCache, "_delete", lambda self, key: False)  # left on disk
        await share.delete_object("AcTiVe1")
        cache = share_cache()
        real_get = cache.get
        reads: list[str] = []

        async def spy_get(key: str):
            reads.append(key)
            return await real_get(key)

        monkeypatch.setattr(cache, "get", spy_get)
        with pytest.raises(share.ShareError) as exc:
            await share.read_object("AcTiVe1")
        assert exc.value.code == "share_gone"
        # Answered by the pre-read check: the disk cache is not even opened.
        assert reads == []

    async def test_a_meta_fetch_racing_the_delete_is_gone_and_not_cached(
        self, on, client, monkeypatch
    ):
        real_request = share.share_request
        fetching, release = asyncio.Event(), asyncio.Event()

        async def slow_request(method: str, path: str, **kwargs):
            response = await real_request(method, path, **kwargs)
            if path.startswith("/v1/meta/"):  # answered before the delete
                fetching.set()
                await release.wait()
            return response

        monkeypatch.setattr(share, "share_request", slow_request)
        meta = asyncio.create_task(share.get_meta("AcTiVe1"))
        await fetching.wait()
        await share.delete_object("AcTiVe1")
        release.set()
        with pytest.raises(share.ShareError) as exc:
            await meta
        assert exc.value.code == "share_gone"
        # Not re-cached: once the negative cache has expired, still gone, no request.
        assert share._state.meta.get("AcTiVe1", share._state.clock()) is None
        on.requests.clear()
        now = share._state.clock() + share.NEGATIVE_CACHE_S + 1
        monkeypatch.setattr(share._state, "clock", lambda: now)
        with pytest.raises(share.ShareError) as exc:
            await share.get_meta("AcTiVe1")
        assert exc.value.code == "share_gone"
        assert on.requests == []

    async def test_a_failed_disk_delete_is_not_served_after_a_restart(
        self, on, client, monkeypatch, tmp_path, request
    ):
        directory = tmp_path / "share-cache"
        reset_share_cache_for_tests(ShareCache(directory))
        request.addfinalizer(reset_share_cache_for_tests)
        assert (await client.get("/api/share/AcTiVe1")).status_code == 200
        cache = share_cache()
        disk = cache._open()
        real_delete = type(disk).delete

        def failing_delete(self, key, *args, **kwargs):
            if key == "AcTiVe1":
                raise OSError(5, "Input/output error")
            return real_delete(self, key, *args, **kwargs)

        monkeypatch.setattr(type(disk), "delete", failing_delete)
        await share.delete_object("AcTiVe1")
        monkeypatch.setattr(type(disk), "delete", real_delete)
        assert disk.get("AcTiVe1") is not None  # the bytes are still on disk
        # Restart: the in-memory tombstone is gone, the disk cache reopened.
        cache.close()
        share._state = share._State(share._state.clock)
        reopened = ShareCache(directory)
        try:
            assert await reopened.get("AcTiVe1") is None
            assert reopened._open().get("AcTiVe1") is None  # dropped on that access
            await reopened.set("AcTiVe1", CachedObject(content=PNG_1X1, content_type="image/png"))
            assert reopened._open().get("AcTiVe1") is None  # never stored again
        finally:
            reopened.close()

    async def test_a_store_racing_the_delete_is_dropped(self, on, client, monkeypatch):
        cache = share_cache()
        real_set = cache.set
        storing, release = asyncio.Event(), asyncio.Event()

        async def slow_set(key: str, obj: CachedObject) -> None:
            storing.set()
            await release.wait()
            await real_set(key, obj)  # written after the delete's disk step

        monkeypatch.setattr(cache, "set", slow_set)
        read = asyncio.create_task(share.read_object("AcTiVe1"))
        await storing.wait()
        await share.delete_object("AcTiVe1")
        release.set()
        with pytest.raises(share.ShareError) as exc:
            await read
        assert exc.value.code == "share_gone"
        assert await cache.get("AcTiVe1") is None

    async def test_unknown_is_not_found(self, on, client):
        response = await client.delete("/api/share/MisSiNg")
        assert response.status_code == 404
        assert _code(response) == "share_not_found"

    async def test_bad_id_makes_no_request(self, on, client):
        response = await client.delete("/api/share/bad-id!")
        assert response.status_code == 400
        assert _code(response) == "share_invalid_id"
        assert on.requests == []

    async def test_off_is_share_disabled_with_no_egress(self, off, client):
        response = await client.delete("/api/share/AcTiVe1")
        assert response.status_code == 403
        assert _code(response) == "share_disabled"
        assert off.requests == []

    async def test_cross_site_is_refused(self, on, client):
        response = await client.delete(
            "/api/share/AcTiVe1", headers={"Sec-Fetch-Site": "cross-site"}
        )
        assert response.status_code == 403
        # The auth middleware's origin check refuses it first; the route guard backs it up.
        assert _code(response) in {"auth_origin_rejected", "share_cross_site"}
        assert on.requests == []


class TestRoles:
    @pytest.mark.parametrize(
        ("role", "allowed"),
        [("kiosk", False), ("viewer", False), ("member", True), ("admin", True)],
    )
    async def test_only_members_and_admins_list_and_delete(
        self, on, client, sign_in_as, role, allowed
    ):
        sign_in_as(role)
        listed = await client.get("/api/share/mine")
        deleted = await client.delete("/api/share/AcTiVe1")
        if allowed:
            assert (listed.status_code, deleted.status_code) == (200, 204)
            assert len(on.requests) == 2
        else:
            assert (listed.status_code, deleted.status_code) == (403, 403)
            assert on.requests == []

    async def test_unauthenticated_cannot_list_or_delete(self, on, client, anonymous):
        listed = await client.get("/api/share/mine")
        deleted = await client.delete("/api/share/AcTiVe1")
        assert listed.status_code == 401
        assert deleted.status_code == 401
        assert on.requests == []

    async def test_cache_clear_stays_admin_only(self, on, client, sign_in_as):
        for role in ("kiosk", "viewer", "member"):
            sign_in_as(role)
            assert (await client.post("/api/share/cache/clear")).status_code == 403
        sign_in_as("admin")
        assert (await client.post("/api/share/cache/clear")).status_code == 200
