"""Custom Store repositories (MSHL-196, Store T22): ``app/plugins/custom_repos.py`` and
``/api/store/repositories``.

GitHub is mocked through the real egress guard (``Net`` from
``tests/test_plugin_installer.py``: DNS from a table, httpx routed to canned answers).
The archives are built from ``tests/plugin_dev/echo``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from typing import Any

import httpx
import pytest

from app.plugins import custom_repos, installer, store_egress
from app.plugins.custom_repos import (
    SHA256_MAX_BYTES,
    InstallError,
    Repository,
    parse_repository,
)
from app.repository import AppSettingsRepository
from app.repository.plugins import PluginRepository
from tests.test_plugin_installer import (
    GRANT,
    Net,
    _Stream,
    build_archive,
    entry_of,
    set_version,
)
from tests.test_plugin_installer import manager as manager  # noqa: F401  (fixture)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

REPO = "Acme/Echo"
BASE = "https://github.com/Acme/Echo"
ASSET_HOST = "https://objects.githubusercontent.com"
_RealAsyncClient = httpx.AsyncClient


@pytest.fixture(autouse=True)
async def _reset():
    await store_egress.reset_store_egress_for_tests()
    custom_repos.register()
    installer.set_download_gate(store_egress.store_egress_allowed)
    yield
    installer.set_download_gate(None)
    custom_repos.unregister()
    await store_egress.reset_store_egress_for_tests()


@pytest.fixture
def net(monkeypatch):
    return Net(monkeypatch)


@pytest.fixture
async def online(test_db):
    await AppSettingsRepository.update(store_online=True)


@pytest.fixture
async def api(test_db):
    from app.main import app

    async with _RealAsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def redirect(location: str, status: int = 302):
    return lambda: httpx.Response(status, headers={"location": location}, stream=_Stream([b""]))


def publish(
    net: Net,
    data: bytes,
    *,
    tag: str = "v1.0.0",
    manifest: bytes | None = None,
    sha: bytes | None = None,
    skip: tuple[str, ...] = (),
) -> None:
    """A release ``tag`` of ``Acme/Echo``: ``releases/latest`` redirects to it and each
    asset redirects to the asset host, like GitHub."""
    net.routes[f"{BASE}/releases/latest"] = redirect(f"{BASE}/releases/tag/{tag}")
    assets = {
        "manifest.json": manifest if manifest is not None else json.dumps(entry_of(data)).encode(),
        "plugin.zip": data,
        "plugin.zip.sha256": sha
        if sha is not None
        else f"{hashlib.sha256(data).hexdigest()}  plugin.zip\n".encode(),
    }
    for name, body in assets.items():
        if name in skip:
            continue
        asset = f"{ASSET_HOST}/{tag}/{name}?sig=1"
        net.routes[f"{BASE}/releases/download/{tag}/{name}"] = redirect(asset)
        net.serve(asset, body)


def hosts(net: Net) -> list[str]:
    return [r.headers["host"] for r in net.requests]


def detail(response: httpx.Response) -> dict[str, Any]:
    return response.json()["detail"]


# --- owner/name --------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, key",
    [
        ("acme/echo", "acme/echo"),
        ("Acme/Echo", "acme/echo"),
        (" a-b/c.d_e-1 ", "a-b/c.d_e-1"),
        ("a" * 39 + "/x", "a" * 39 + "/x"),
    ],
)
def test_valid_repositories(text, key):
    assert parse_repository(text).key == key


@pytest.mark.parametrize(
    "text",
    [
        "",
        "acme",
        "acme/",
        "/echo",
        "acme/echo/extra",
        "-acme/echo",
        "acme-/echo",
        "ac--me/echo",
        "a" * 40 + "/x",
        "acme/" + "x" * 101,
        "acme/..",
        "acme/.",
        "acme/ec ho",
        "acme/echo?x=1",
        "acme/echo#x",
        "https://github.com/acme/echo",
        "github.com/acme/echo",
        "acme/%2e%2e",
        "evil.example.org/echo",
        123,
    ],
)
def test_invalid_repositories(text):
    with pytest.raises(InstallError) as err:
        parse_repository(text)
    assert err.value.code == "repository_invalid"


# --- the Store consent -------------------------------------------------------------


async def test_nothing_is_fetched_while_the_store_is_off(api, net):
    assert (await api.get("/api/store/repositories")).json()["store_online"] is False
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert response.status_code == 409
    assert detail(response)["code"] == "store_offline"
    assert net.requests == []
    assert (await api.get("/api/store/repositories")).json()["repositories"] == []


async def test_install_and_refresh_fetch_nothing_while_off(api, net, tmp_path):
    await AppSettingsRepository.update(store_online=True)
    publish(net, build_archive(tmp_path))
    assert (await api.post("/api/store/repositories", json={"repository": REPO})).status_code == 201
    await AppSettingsRepository.update(store_online=False)
    before = len(net.requests)
    with pytest.raises(store_egress.StoreOffline):
        await custom_repos.refresh_repositories()
    with pytest.raises(InstallError) as err:
        await installer.resolve_source("custom", "acme.echo", None, "acme/echo")
    assert err.value.code == "store_offline"
    assert len(net.requests) == before


# --- adding ------------------------------------------------------------------------


async def test_add_list_and_remove(api, net, online, tmp_path):
    data = build_archive(tmp_path)
    publish(net, data)
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert response.status_code == 201, response.text
    view = response.json()
    assert (view["repository"], view["display_name"]) == ("acme/echo", REPO)
    assert view["last_error"] is None
    entry = view["entry"]
    assert (entry["tier"], entry["signed"], entry["warning"]) == (
        "custom",
        False,
        "custom_unsigned",
    )
    assert set(entry["warning_text"]) == {"en", "fr"}
    assert "integrity" in entry["warning_text"]["en"]
    assert "intégrité" in entry["warning_text"]["fr"]
    assert (entry["plugin_id"], entry["version"], entry["tag"]) == ("acme.echo", "1.0.0", "v1.0.0")
    assert entry["archive_sha256"] == hashlib.sha256(data).hexdigest()
    assert entry["archive_size"] == len(data)
    assert entry["permissions"] == entry_of(data)["permissions"]
    # github.com for each lookup, then the asset host: nothing else.
    assert set(hosts(net)) == {"github.com", "objects.githubusercontent.com"}

    listed = (await api.get("/api/store/repositories")).json()
    assert listed["store_online"] is True
    assert listed["warning"] == "custom_unsigned"
    assert [r["repository"] for r in listed["repositories"]] == ["acme/echo"]

    again = await api.post("/api/store/repositories", json={"repository": "acme/ECHO"})
    assert (again.status_code, detail(again)["code"]) == (409, "repository_exists")

    assert (await api.delete("/api/store/repositories/acme/echo")).status_code == 204
    missing = await api.delete("/api/store/repositories/acme/echo")
    assert (missing.status_code, detail(missing)["code"]) == (404, "repository_not_found")
    assert (await api.get("/api/store/repositories")).json()["repositories"] == []

    from app.repository.auth import AuthAuditRepository

    rows = await AuthAuditRepository.list_recent(limit=50)
    actions = [(r.action, r.outcome, r.target) for r in rows]
    assert ("store.repository_added", "success", "repository:acme/echo") in actions
    assert ("store.repository_removed", "success", "repository:acme/echo") in actions
    assert ("store.repository_removed", "failure", "repository:acme/echo") in actions


async def test_an_invalid_name_is_refused_before_any_request(api, net, online):
    response = await api.post(
        "/api/store/repositories", json={"repository": "https://evil.example.org/x"}
    )
    assert (response.status_code, detail(response)["code"]) == (400, "repository_invalid")
    response = await api.delete("/api/store/repositories/-bad/x")
    assert (response.status_code, detail(response)["code"]) == (400, "repository_invalid")
    assert net.requests == []


@pytest.mark.parametrize(
    "answer",
    [
        lambda: httpx.Response(404, stream=_Stream([b"Not Found"])),
        redirect(f"{BASE}/releases"),
        redirect(f"{BASE}"),
    ],
    ids=["404", "to-releases", "to-repository"],
)
async def test_a_repository_without_release(api, net, online, answer):
    net.routes[f"{BASE}/releases/latest"] = answer
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert (response.status_code, detail(response)["code"]) == (404, "repository_no_release")
    assert detail(response)["message"]
    assert len(net.requests) == 1
    assert (await api.get("/api/store/repositories")).json()["repositories"] == []


async def test_a_moved_repository_is_refused(api, net, online):
    net.routes[f"{BASE}/releases/latest"] = redirect(
        "https://github.com/other/echo/releases/tag/v1.0.0"
    )
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert (response.status_code, detail(response)["code"]) == (409, "repository_moved")
    assert len(net.requests) == 1


async def test_an_unusable_tag_is_refused(api, net, online):
    net.routes[f"{BASE}/releases/latest"] = redirect(f"{BASE}/releases/tag/..%2Fx")
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert detail(response)["code"] == "repository_tag_invalid"


@pytest.mark.parametrize(
    "skip, missing",
    [
        (("plugin.zip.sha256",), ["plugin.zip.sha256"]),
        (("manifest.json", "plugin.zip.sha256"), ["manifest.json", "plugin.zip.sha256"]),
        (("plugin.zip",), ["plugin.zip"]),
    ],
)
async def test_missing_assets_are_named(api, net, online, tmp_path, skip, missing):
    publish(net, build_archive(tmp_path), skip=skip)
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert (response.status_code, detail(response)["code"]) == (404, "repository_assets_missing")
    assert detail(response)["params"]["missing"] == missing
    assert (await api.get("/api/store/repositories")).json()["repositories"] == []


async def test_a_redirect_to_another_host_is_refused(api, net, online, tmp_path):
    publish(net, build_archive(tmp_path))
    net.routes[f"{BASE}/releases/download/v1.0.0/manifest.json"] = redirect(
        "https://evil.example.org/manifest.json"
    )
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert (response.status_code, detail(response)["code"]) == (409, "download_blocked")
    assert "evil.example.org" not in hosts(net)
    assert "evil" not in response.text


async def test_the_lookup_redirect_is_never_followed(api, net, online):
    # A releases/latest answer pointing off GitHub: refused as a move, not followed.
    net.routes[f"{BASE}/releases/latest"] = redirect("https://evil.example.org/releases/tag/v1")
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert detail(response)["code"] == "repository_moved"
    assert hosts(net) == ["github.com"]


async def test_size_caps(api, net, online, tmp_path):
    publish(net, build_archive(tmp_path), sha=b"0" * (SHA256_MAX_BYTES + 1))
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert detail(response)["code"] == "repository_asset_too_large"


async def test_a_sha256_mismatch_is_refused(api, net, online, tmp_path):
    publish(net, build_archive(tmp_path), sha=b"ab" * 32)
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert detail(response)["code"] == "archive_mismatch"
    assert detail(response)["params"]["fields"] == ["sha256"]


async def test_a_sha256_file_without_digest_is_refused(api, net, online, tmp_path):
    publish(net, build_archive(tmp_path), sha=b"not a digest\n")
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert detail(response)["code"] == "repository_sha256_invalid"


async def test_an_invalid_manifest_is_refused(api, net, online, tmp_path):
    publish(net, build_archive(tmp_path), manifest=b'{"id": "x"}')
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert detail(response)["code"] == "repository_manifest_invalid"
    assert all(set(i) == {"code", "path"} for i in detail(response)["params"]["issues"])


async def test_a_manifest_that_disagrees_with_the_archive(api, net, online, tmp_path):
    data = build_archive(tmp_path)
    other = entry_of(data) | {"version": "9.9.9"}
    publish(net, data, manifest=json.dumps(other).encode())
    response = await api.post("/api/store/repositories", json={"repository": REPO})
    assert detail(response)["code"] == "archive_mismatch"
    assert detail(response)["params"]["fields"] == ["version"]


async def test_the_list_is_capped(api, net, online, tmp_path, monkeypatch):
    monkeypatch.setattr(custom_repos, "MAX_REPOSITORIES", 1)
    publish(net, build_archive(tmp_path))
    assert (await api.post("/api/store/repositories", json={"repository": REPO})).status_code == 201
    response = await api.post("/api/store/repositories", json={"repository": "acme/other"})
    assert (response.status_code, detail(response)["code"]) == (409, "repository_limit")


# --- refresh -----------------------------------------------------------------------


async def test_refresh_reads_a_new_release_and_keeps_the_last_good_one(api, net, online, tmp_path):
    publish(net, build_archive(tmp_path))
    assert (await api.post("/api/store/repositories", json={"repository": REPO})).status_code == 201

    # Same tag: only the lookup.
    before = len(net.requests)
    await custom_repos.refresh_repositories()
    assert len(net.requests) == before + 1

    newer = build_archive(tmp_path, version="1.1.0")
    publish(net, newer, tag="v1.1.0")
    await custom_repos.refresh_repositories()
    row = await custom_repos.get_row("acme/echo")
    assert row is not None
    assert (row["version"], row["tag"], row["last_error"]) == ("1.1.0", "v1.1.0", None)
    assert row["archive_sha256"] == hashlib.sha256(newer).hexdigest()

    # A broken newer release: the error is recorded, the last good release kept.
    publish(net, newer, tag="v1.2.0", skip=("plugin.zip.sha256",))
    await custom_repos.refresh_repositories()
    row = await custom_repos.get_row("acme/echo")
    assert row is not None
    assert (row["version"], row["last_error"]) == ("1.1.0", "repository_assets_missing")


# --- install -----------------------------------------------------------------------


async def test_install_from_a_repository(api, manager, net, online, tmp_path, monkeypatch):
    from app.routers import plugins as plugins_router

    monkeypatch.setattr(installer, "BUNDLED_ROOT", tmp_path / "no-bundled")
    monkeypatch.setattr(plugins_router, "plugin_manager", manager)
    set_version(monkeypatch, "5.0.0")
    data = build_archive(tmp_path)
    publish(net, data)
    body = {
        "kind": "custom",
        "plugin_id": "acme.echo",
        "repository": REPO,
        "granted_permissions": GRANT,
    }

    # Not added yet: unknown source, no request.
    refused = await api.post("/api/plugins/install", json=body)
    assert (refused.status_code, detail(refused)["code"]) == (404, "plugin_source_unavailable")
    assert net.requests == []

    assert (await api.post("/api/store/repositories", json={"repository": REPO})).status_code == 201
    response = await api.post("/api/plugins/install", json=body)
    assert response.status_code == 201, response.text
    row = response.json()
    assert (row["kind"], row["version"]) == ("custom", "1.0.0")
    stored = await PluginRepository.get(row["instance_id"])
    assert stored is not None
    assert stored["repository"] == "acme/echo"
    assert stored["archive_sha256"] == hashlib.sha256(data).hexdigest()

    listed = (await api.get("/api/store/repositories")).json()["repositories"][0]
    assert [i["instance_id"] for i in listed["instances"]] == [row["instance_id"]]
    assert listed["update_available"] is False

    # Another plugin id than the repository's: unknown source.
    wrong = await api.post("/api/plugins/install", json=body | {"plugin_id": "acme.other"})
    assert detail(wrong)["code"] == "plugin_source_unavailable"

    # Forgotten repository: the installed instance still allows a reinstall source.
    assert (await api.delete("/api/store/repositories/acme/echo")).status_code == 204
    # The instance keeps the lowercase key; GitHub answers any case (the mock does not).
    for url in [u for u in net.routes if u.startswith(BASE)]:
        net.routes[url.replace(BASE, BASE.lower())] = net.routes[url]
    source = await installer.resolve_source("custom", "acme.echo", None, "acme/echo")
    assert source is not None and source.repository == "acme/echo"


async def test_resolver_checks_the_version(net, online, tmp_path):
    publish(net, build_archive(tmp_path))
    await custom_repos.add_repository(REPO)
    assert await custom_repos.resolve_custom("acme.echo", "0.9.0", REPO) is None
    source = await custom_repos.resolve_custom("acme.echo", "1.0.0", REPO)
    assert source is not None
    assert source.archive_url == f"{BASE}/releases/download/v1.0.0/plugin.zip"
    assert source.expected_size is not None
    assert await custom_repos.resolve_custom("acme.echo", None, None) is None
    assert await custom_repos.resolve_custom("acme.echo", None, "not a repo") is None


def test_repository_dataclass_keeps_the_display_case():
    assert parse_repository("Acme/Echo") == Repository("acme/echo", "Acme/Echo")
