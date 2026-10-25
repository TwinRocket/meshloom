"""The generic installer (``app/plugins/installer.py``, MSHL-195) and its routes.

Remote archives come through the real egress guard: DNS answers from a table
(``socket.getaddrinfo`` replaced) and httpx a ``MockTransport`` that records every
request. The archives are built from ``tests/plugin_dev/echo``.
"""

import asyncio
import hashlib
import io
import json
import re
import shutil
import socket
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest

from app.net.egress_guard import HostAllowlist
from app.plugins import installer, store_egress
from app.plugins.errors import PluginValidationError
from app.plugins.host import Budgets, PluginManager
from app.plugins.package import MAX_COMPRESSED_BYTES
from app.repository.plugins import PluginKvRepository, PluginRepository

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

ECHO_SRC = Path(__file__).parent / "plugin_dev" / "echo"
ARCHIVE_URL = "https://github.com/acme/echo/releases/download/1.0.0/plugin.zip"
ASSET_URL = "https://objects.githubusercontent.com/asset/plugin.zip"
GITHUB = HostAllowlist.of("github.com", "objects.githubusercontent.com")
DNS = {
    "github.com": "140.82.121.4",
    "objects.githubusercontent.com": "185.199.108.133",
    "evil.example.org": "93.184.215.14",
}
GRANT = {
    "messages.read": {"channels": "public", "direct": True, "outgoing": False},
    "messages.send": {"targets": "reply"},
}
SCHEMA = {
    "type": "object",
    "properties": {
        "greeting": {"type": "string"},
        "token": {"type": "string", "writeOnly": True},
    },
    "required": ["greeting"],
}
SECRET = "tok-51c0ffee"
FAST = Budgets(
    init_timeout=10.0,
    event_timeout=5.0,
    cancel_grace=0.6,
    shutdown_grace=1.0,
    stop_timeout=5.0,
    backoff_initial=0.05,
    backoff_max=0.2,
    stagger=0.05,
    message_settle=0.0,
)


# --- helpers ---------------------------------------------------------------------


def build_archive(tmp: Path, *, schema: dict | None = None, **manifest_changes: Any) -> bytes:
    from meshloom_plugin.dev import zip_directory

    tree = tmp / f"src-{len(list(tmp.glob('src-*')))}"
    shutil.copytree(ECHO_SRC, tree)
    manifest = json.loads((tree / "manifest.json").read_text())
    manifest.update(manifest_changes)
    if schema is not None:
        manifest["config_schema"] = "config.schema.json"
        (tree / "config.schema.json").write_text(json.dumps(schema))
    (tree / "manifest.json").write_text(json.dumps(manifest))
    return zip_directory(tree)


def entry_of(data: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return json.loads(zf.read("manifest.json"))


def remote(data: bytes, **overrides: Any) -> installer.RemoteArchiveSource:
    kwargs: dict[str, Any] = {
        "tier": "custom",
        "entry": entry_of(data),
        "archive_url": ARCHIVE_URL,
        "expected_sha256": hashlib.sha256(data).hexdigest(),
        "expected_size": len(data),
        "allowed_hosts": GITHUB,
        "repository": "acme/echo",
    }
    kwargs.update(overrides)
    return installer.RemoteArchiveSource(**kwargs)


class _Stream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.sent = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.sent += 1
            yield chunk

    async def aclose(self) -> None:
        return None


class Net:
    """DNS from a table, and httpx routed to ``routes`` (url -> response factory)."""

    def __init__(self, monkeypatch) -> None:
        self.routes: dict[str, Any] = {}
        self.requests: list[httpx.Request] = []

        def getaddrinfo(host, port, *args, **kwargs):
            if host not in DNS:
                raise socket.gaierror(socket.EAI_NONAME, "unknown")
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (DNS[host], port or 443))]

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)

        def handle(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            # The guard connects to the pinned address and names the host in the headers.
            url = f"https://{request.headers['host']}{request.url.raw_path.decode()}"
            factory = self.routes.get(url)
            if factory is None:
                return httpx.Response(404, stream=_Stream([b""]))
            return factory()

        real = httpx.AsyncClient

        class Routed(real):
            def __init__(self, *args, **kwargs):
                assert kwargs.get("follow_redirects") is False
                assert kwargs.get("trust_env") is False
                kwargs["transport"] = httpx.MockTransport(handle)
                super().__init__(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", Routed)

    def serve(self, url: str, data: bytes, *, length: int | None = None) -> None:
        headers = {} if length is None else {"content-length": str(length)}
        self.routes[url] = lambda: httpx.Response(200, headers=headers, stream=_Stream([data]))


@pytest.fixture
def net(monkeypatch):
    return Net(monkeypatch)


@pytest.fixture
def online(monkeypatch):
    """The Store consent on: the installer's gate and ``store_fetch`` both read it."""

    async def allowed() -> bool:
        return True

    monkeypatch.setattr(installer, "_download_gate", allowed)
    monkeypatch.setattr(store_egress, "store_egress_allowed", allowed)


def leftovers(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def set_version(monkeypatch, version: str) -> None:
    from app import version_info

    info = version_info.AppBuildInfo(version, "test", None, None)
    monkeypatch.setattr(version_info, "get_app_build_info", lambda: info)


# --- the pipeline ----------------------------------------------------------------


async def test_remote_archive_is_checked_extracted_and_moved(tmp_path, net, online):
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data, length=len(data))
    root = tmp_path / "plugins"
    prepared = await installer.prepare_install(remote(data), root, plugin_id="acme.echo")
    assert prepared.archive_sha256 == hashlib.sha256(data).hexdigest()
    assert prepared.staging.parent == root / "_staging"
    prepared.commit()
    assert (root / "acme.echo" / "plugin.py").is_file()
    assert (root / "acme.echo" / "plugin.py").stat().st_mode & 0o777 == 0o644
    assert not (root / "_staging").exists()
    assert net.requests[0].headers["user-agent"].startswith("Meshloom/")


async def test_redirect_to_an_allowed_host_is_followed(tmp_path, net, online):
    data = build_archive(tmp_path)
    net.routes[ARCHIVE_URL] = lambda: httpx.Response(
        302, headers={"location": ASSET_URL}, stream=_Stream([b""])
    )
    net.serve(ASSET_URL, data)
    prepared = await installer.prepare_install(
        remote(data), tmp_path / "plugins", plugin_id="acme.echo"
    )
    prepared.discard()
    assert [r.headers["host"] for r in net.requests] == [
        "github.com",
        "objects.githubusercontent.com",
    ]
    assert leftovers(tmp_path / "plugins") == []


async def test_redirect_off_the_allowlist_is_refused(tmp_path, net, online):
    data = build_archive(tmp_path)
    net.routes[ARCHIVE_URL] = lambda: httpx.Response(
        302, headers={"location": "https://evil.example.org/x.zip"}, stream=_Stream([b""])
    )
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(remote(data), tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "download_blocked"
    assert len(net.requests) == 1
    assert leftovers(tmp_path / "plugins") == []


async def test_a_failed_download_is_download_failed(tmp_path, net, online):
    data = build_archive(tmp_path)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(remote(data), tmp_path / "plugins", plugin_id="acme.echo")
    assert (err.value.code, err.value.status) == ("download_failed", 502)
    assert err.value.params == {"http_status": 404}
    assert leftovers(tmp_path / "plugins") == []


# --- the Store consent (MSHL-366: downloads go through store_fetch) ----------------


async def test_downloads_follow_the_store_consent_even_past_the_gate(tmp_path, net, monkeypatch):
    async def allowed() -> bool:
        return True

    async def refused() -> bool:
        return False

    # The gate let it through (a stale check, a race): store_fetch still refuses.
    monkeypatch.setattr(installer, "_download_gate", allowed)
    monkeypatch.setattr(store_egress, "store_egress_allowed", refused)
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(remote(data), tmp_path / "plugins", plugin_id="acme.echo")
    assert (err.value.code, err.value.status) == ("store_offline", 409)
    assert net.requests == []
    assert leftovers(tmp_path / "plugins") == []


async def test_the_gate_is_the_store_consent(tmp_path, net, monkeypatch, test_db):
    from app.repository import AppSettingsRepository

    monkeypatch.setattr(installer, "_download_gate", None)
    installer.set_download_gate(store_egress.store_egress_allowed)
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data)
    assert not await installer.downloads_allowed()
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(remote(data), tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "store_offline"
    assert net.requests == []
    await AppSettingsRepository.update(store_online=True)
    try:
        assert await installer.downloads_allowed()
        prepared = await installer.prepare_install(
            remote(data), tmp_path / "plugins", plugin_id="acme.echo"
        )
        prepared.discard()
    finally:
        await store_egress.reset_store_egress_for_tests()


async def test_turning_the_store_off_cancels_a_download_in_flight(tmp_path, net, online):
    started = asyncio.Event()

    class Hanging(httpx.AsyncByteStream):
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""

        async def aclose(self) -> None:
            return None

    data = build_archive(tmp_path)
    net.routes[ARCHIVE_URL] = lambda: httpx.Response(200, stream=Hanging())
    root = tmp_path / "plugins"
    pending = asyncio.create_task(
        installer.prepare_install(remote(data), root, plugin_id="acme.echo")
    )
    try:
        await asyncio.wait_for(started.wait(), 3)
        await store_egress.note_store_online_changed(False)
        with pytest.raises(installer.InstallError) as err:
            await asyncio.wait_for(pending, 3)
        assert (err.value.code, err.value.status) == ("store_offline", 409)
        assert leftovers(root) in ([], ["_staging"])
    finally:
        await store_egress.reset_store_egress_for_tests()


async def test_an_archive_url_with_user_info_is_refused(tmp_path, net, online):
    data = build_archive(tmp_path)
    source = remote(data, archive_url="https://user:pw@github.com/acme/echo/plugin.zip")
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(source, tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "download_blocked"
    assert net.requests == []


async def test_a_source_cannot_widen_the_store_allowlist(tmp_path, net, online):
    data = build_archive(tmp_path)
    url = "https://evil.example.org/plugin.zip"
    net.serve(url, data)
    source = remote(data, archive_url=url, allowed_hosts=HostAllowlist.of("evil.example.org"))
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(source, tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "download_blocked"
    assert net.requests == []


async def test_sha256_mismatch_leaves_nothing(tmp_path, net, online):
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data)
    source = remote(data, expected_sha256="0" * 64)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(source, tmp_path / "plugins", plugin_id="acme.echo")
    assert (err.value.code, err.value.params) == ("archive_mismatch", {"fields": ["sha256"]})
    assert leftovers(tmp_path / "plugins") == []


@pytest.mark.parametrize("delta", [-1, 1])
async def test_announced_size_mismatch_leaves_nothing(tmp_path, net, online, delta):
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data)
    source = remote(data, expected_size=len(data) + delta)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(source, tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "archive_mismatch"
    assert err.value.params == {"fields": ["size"]}
    assert leftovers(tmp_path / "plugins") == []


async def test_declared_length_over_the_cap_is_refused_before_the_body(tmp_path, net, online):
    data = build_archive(tmp_path)
    stream = _Stream([data])
    net.routes[ARCHIVE_URL] = lambda: httpx.Response(
        200, headers={"content-length": str(MAX_COMPRESSED_BYTES + 1)}, stream=stream
    )
    source = remote(data, expected_size=None)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(source, tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "archive_too_large"
    assert stream.sent == 0


async def test_a_download_past_the_cap_is_aborted_while_streaming(tmp_path, net, online):
    data = build_archive(tmp_path)
    stream = _Stream([b"\0" * (1024 * 1024)] * 20)
    net.routes[ARCHIVE_URL] = lambda: httpx.Response(200, stream=stream)
    source = remote(data, expected_size=None)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(source, tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "archive_too_large"
    assert stream.sent <= 6
    assert leftovers(tmp_path / "plugins") == []


async def test_more_bytes_than_announced_is_a_mismatch(tmp_path, net, online):
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data + b"\0" * 10)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(remote(data), tmp_path / "plugins", plugin_id="acme.echo")
    assert (err.value.code, err.value.params) == ("archive_mismatch", {"fields": ["size"]})


async def test_an_announced_size_over_the_cap_downloads_nothing(tmp_path, net, online):
    data = build_archive(tmp_path)
    source = remote(data, expected_size=MAX_COMPRESSED_BYTES + 1)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(source, tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "archive_too_large"
    assert net.requests == []


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"version": "1.0.1"}, "version"),
        ({"permissions": {"messages.read": GRANT["messages.read"]}}, "permissions"),
    ],
)
async def test_manifest_differing_from_the_entry_is_refused(tmp_path, net, online, change, field):
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data)
    source = remote(data, entry={**entry_of(data), **change})
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(source, tmp_path / "plugins", plugin_id="acme.echo")
    assert (err.value.code, err.value.params) == ("archive_mismatch", {"fields": [field]})
    assert leftovers(tmp_path / "plugins") == []


async def test_an_entry_for_another_plugin_is_refused(tmp_path, net, online):
    data = build_archive(tmp_path)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(remote(data), tmp_path / "plugins", plugin_id="acme.other")
    assert err.value.code == "archive_mismatch"
    assert net.requests == []


async def test_downloads_are_refused_without_a_gate(tmp_path, net):
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data)
    with pytest.raises(installer.InstallError) as err:
        await installer.prepare_install(remote(data), tmp_path / "plugins", plugin_id="acme.echo")
    assert err.value.code == "store_offline"
    assert net.requests == []


async def test_a_failing_gate_refuses(monkeypatch):
    async def broken() -> bool:
        raise RuntimeError("boom")

    monkeypatch.setattr(installer, "_download_gate", broken)
    assert await installer.downloads_allowed() is False


def test_a_remote_source_needs_a_sha256():
    with pytest.raises(ValueError):
        installer.RemoteArchiveSource(
            tier="custom",
            entry={},
            archive_url=ARCHIVE_URL,
            expected_sha256="",
            allowed_hosts=GITHUB,
        )


def _with_extra(data: bytes, name: str, mode: int, content: bytes) -> bytes:
    info = zipfile.ZipInfo(name)
    info.external_attr = mode << 16
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as src, zipfile.ZipFile(buf, "w") as out:
        for item in src.infolist():
            out.writestr(item, src.read(item))
        out.writestr(info, content)
    return buf.getvalue()


@pytest.mark.parametrize(
    ("name", "mode", "content"),
    [
        ("../escape.py", 0o100644, b"x"),
        ("/abs.py", 0o100644, b"x"),
        ("link", 0o120777, b"/etc/passwd"),
    ],
)
async def test_unsafe_archives_write_nothing(tmp_path, net, online, name, mode, content):
    data = _with_extra(build_archive(tmp_path), name, mode, content)
    net.serve(ARCHIVE_URL, data)
    with pytest.raises(Exception) as err:
        await installer.prepare_install(remote(data), tmp_path / "plugins", plugin_id="acme.echo")
    assert not isinstance(err.value, AssertionError)
    assert leftovers(tmp_path / "plugins") == []
    assert not (tmp_path / "escape.py").exists()


async def test_commit_replaces_a_symlink_without_following_it(tmp_path, net, online):
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data)
    root = tmp_path / "plugins"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")
    (root / "acme.echo").symlink_to(outside)
    prepared = await installer.prepare_install(remote(data), root, plugin_id="acme.echo")
    prepared.commit()
    assert not (root / "acme.echo").is_symlink()
    assert (root / "acme.echo" / "plugin.py").is_file()
    assert (outside / "keep").is_file()
    assert sorted(p.name for p in root.iterdir()) == ["acme.echo"]


def test_remove_code_never_follows_a_symlink(tmp_path):
    root = tmp_path / "plugins"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")
    (root / "acme.echo").symlink_to(outside)
    installer.remove_code(root, "acme.echo")
    installer.remove_code(root, "..")
    assert not (root / "acme.echo").exists()
    assert (outside / "keep").is_file()


@pytest.mark.parametrize(
    ("version", "spec", "ok"),
    [
        ("5.0.0", ">=5.0.0", True),
        ("5.0.0rc1", ">=5.0.0", True),
        ("4.18.2", ">=5.0.0", False),
        ("5.1.0", ">=5.0.0,<5.1", False),
        ("not-a-version", ">=5.0.0", True),
    ],
)
def test_compatible(monkeypatch, version, spec, ok):
    set_version(monkeypatch, version)
    assert installer.compatible({"compat": {"meshloom": spec}}) is ok


# --- the routes ------------------------------------------------------------------


@pytest.fixture
async def manager(test_db, tmp_path, monkeypatch):
    from app.plugins import host as host_module
    from app.routers import plugins as router

    monkeypatch.setattr(host_module, "_broadcast_status", lambda payload: None)
    monkeypatch.setattr(installer, "BUNDLED_ROOT", tmp_path / "no-bundled")
    mgr = PluginManager(
        FAST, plugins_root=tmp_path / "plugins", cpu_reader=None, mem_total=lambda: 8 * 1024**3
    )
    monkeypatch.setattr(router, "plugin_manager", mgr)
    yield mgr
    await mgr.stop_all(5.0)


@pytest.fixture
async def api(manager):
    from app.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def catalog(monkeypatch, net, online):
    """A custom resolver serving the archives put in ``sources``, by plugin id."""
    sources: dict[str, bytes] = {}
    asked: list[tuple[str, str | None, str | None]] = []

    async def resolve(plugin_id, version, repository):
        asked.append((plugin_id, version, repository))
        data = sources.get(plugin_id)
        if data is None:
            return None
        net.serve(ARCHIVE_URL, data)
        return remote(data, repository=repository)

    monkeypatch.setattr(installer, "_resolvers", {"custom": resolve})
    set_version(monkeypatch, "5.0.0")
    return sources, asked


def code_of(response: httpx.Response) -> str:
    return response.json()["detail"]["code"]


async def install(api, **extra: Any) -> httpx.Response:
    body = {
        "kind": "custom",
        "plugin_id": "acme.echo",
        "repository": "acme/echo",
        "granted_permissions": GRANT,
        **extra,
    }
    return await api.post("/api/plugins/install", json=body)


async def test_custom_install_second_instance_and_uninstall(api, manager, net, catalog, tmp_path):
    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path)
    first = await install(api)
    assert first.status_code == 201, first.text
    row = first.json()
    assert (row["kind"], row["version"]) == ("custom", "1.0.0")
    stored = await PluginRepository.get(row["instance_id"])
    assert stored is not None
    assert stored["archive_sha256"] == hashlib.sha256(sources["acme.echo"]).hexdigest()
    assert stored["repository"] == "acme/echo"
    code = manager.plugins_root() / "acme.echo"
    assert (code / "manifest.json").is_file()
    tree = leftovers(manager.plugins_root())
    downloads = len(net.requests)

    second = await install(api, label="Second")
    assert second.status_code == 201, second.text
    assert len(net.requests) == downloads  # the code dir is reused
    assert leftovers(manager.plugins_root()) == tree

    assert (await api.delete(f"/api/plugins/{row['instance_id']}")).status_code == 204
    assert code.is_dir()
    assert (await api.delete(f"/api/plugins/{second.json()['instance_id']}")).status_code == 204
    assert leftovers(manager.plugins_root()) == []


async def test_narrower_consent_lists_what_is_missing(api, manager, net, catalog, tmp_path):
    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path)
    response = await install(api, granted_permissions={"messages.read": GRANT["messages.read"]})
    assert response.status_code == 400
    assert code_of(response) == "consent_required"
    assert response.json()["detail"]["params"]["missing"] == ["messages.send"]
    assert net.requests == []
    assert leftovers(manager.plugins_root()) == []
    assert await PluginRepository.get_all() == []


async def test_missing_required_config_is_config_required_and_no_start(
    api, manager, catalog, tmp_path
):
    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path, schema=SCHEMA)
    response = await install(api)
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["enabled"] is False
    assert row["attention"] == {"type": "config_required", "fields": ["greeting"]}
    assert manager.get(row["instance_id"]) is None


async def _secrets(test_db, instance_id: str) -> list[str]:
    async with test_db.readonly() as conn:
        async with conn.execute(
            "SELECT field FROM plugin_secrets WHERE instance_id = ?", (instance_id,)
        ) as cursor:
            return [r[0] for r in await cursor.fetchall()]


async def _kept(test_db) -> list[tuple[str, str, str, str | None]]:
    async with test_db.readonly() as conn:
        async with conn.execute(
            "SELECT instance_id, plugin_id, kind, repository FROM plugin_kept_data"
        ) as cursor:
            return [tuple(r) for r in await cursor.fetchall()]


async def test_keep_data_keeps_the_kv_and_the_secrets(api, manager, catalog, test_db, tmp_path):
    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path, schema=SCHEMA)
    for keep in (True, False):
        response = await install(
            api, repository=f"acme/{keep}", config={"greeting": "hi", "token": SECRET}
        )
        assert response.status_code == 201, response.text
        assert SECRET not in response.text
        iid = response.json()["instance_id"]
        await PluginKvRepository.set(iid, "k", 1)
        assert await _secrets(test_db, iid) == ["token"]
        deleted = await api.delete(f"/api/plugins/{iid}?keep_data={str(keep).lower()}")
        assert deleted.status_code == 204
        assert await PluginKvRepository.keys(iid) == (["k"] if keep else [])
        assert await _secrets(test_db, iid) == (["token"] if keep else [])
        assert leftovers(manager.plugins_root()) == []
    # Only the kept uninstall is recorded, with its owner.
    assert [row[1:] for row in await _kept(test_db)] == [("acme.echo", "custom", "acme/True")]


async def test_a_reinstall_takes_the_kept_data_back(api, manager, catalog, test_db, tmp_path):
    from app.repository.plugins import PluginRepository as Repo

    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path, schema=SCHEMA)
    first = await install(api, config={"greeting": "hi", "token": SECRET})
    old = first.json()["instance_id"]
    await PluginKvRepository.set(old, "k", 1)
    assert (await api.delete(f"/api/plugins/{old}?keep_data=true")).status_code == 204

    again = await install(api, config={"greeting": "hello"})
    assert again.status_code == 201, again.text
    new = again.json()["instance_id"]
    assert new != old
    assert SECRET not in again.text
    assert again.json()["config"]["token"] == "********"
    assert await PluginKvRepository.keys(new) == ["k"]
    assert await _secrets(test_db, new) == ["token"]
    assert (await Repo.config_for_init(new))["token"] == SECRET
    assert await PluginKvRepository.keys(old) == []
    assert await _secrets(test_db, old) == []
    assert await _kept(test_db) == []


async def test_a_reinstall_can_replace_or_clear_a_kept_secret(
    api, manager, catalog, test_db, tmp_path
):
    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path, schema=SCHEMA)
    for replacement, expected in (("tok-new", ["token"]), ("", [])):
        first = await install(api, config={"greeting": "hi", "token": SECRET})
        old = first.json()["instance_id"]
        assert (await api.delete(f"/api/plugins/{old}?keep_data=true")).status_code == 204
        again = await install(api, config={"greeting": "hi", "token": replacement})
        new = again.json()["instance_id"]
        assert await _secrets(test_db, new) == expected
        if expected:
            assert (await PluginRepository.config_for_init(new))["token"] == replacement
        assert await _kept(test_db) == []
        assert (await api.delete(f"/api/plugins/{new}")).status_code == 204


async def test_another_source_never_gets_the_kept_data(api, manager, catalog, test_db, tmp_path):
    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path, schema=SCHEMA)
    first = await install(api, config={"greeting": "hi", "token": SECRET})
    old = first.json()["instance_id"]
    await PluginKvRepository.set(old, "k", 1)
    assert (await api.delete(f"/api/plugins/{old}?keep_data=true")).status_code == 204

    other = await install(api, repository="mallory/echo", config={"greeting": "hi"})
    assert other.status_code == 201, other.text
    new = other.json()["instance_id"]
    assert await PluginKvRepository.keys(new) == []
    assert await _secrets(test_db, new) == []
    assert "token" not in other.json()["config"]
    # Still kept for the right source.
    assert [row[0] for row in await _kept(test_db)] == [old]
    assert await PluginKvRepository.keys(old) == ["k"]


async def test_kept_data_goes_after_the_retention(test_db):
    from app.repository.plugins import KEPT_DATA_RETENTION_S, PluginKeptDataRepository

    row = await PluginRepository.create(
        plugin_id="acme.echo",
        label="Echo",
        kind="custom",
        version="1.0.0",
        manifest={},
        config_schema=SCHEMA,
        config={"greeting": "hi", "token": SECRET},
        repository="acme/echo",
    )
    iid = row["instance_id"]
    await PluginKvRepository.set(iid, "k", 1)
    await PluginRepository.delete(iid, keep_data=True)
    async with test_db.readonly() as conn:
        async with conn.execute("SELECT kept_at FROM plugin_kept_data") as cursor:
            kept_at = (await cursor.fetchone())[0]
    # Rows nobody owns (left before migration 124) are recorded, not deleted at once.
    async with test_db.tx() as conn:
        await conn.execute(
            "INSERT INTO plugin_kv (instance_id, key, value, updated_at) VALUES ('ghost', 'k', '1', 0)"
        )
    assert await PluginKeptDataRepository.prune(kept_at + 10) == 0
    assert sorted(r[0] for r in await _kept(test_db)) == sorted([iid, "ghost"])
    assert await PluginKeptDataRepository.find("acme.echo", "custom", "acme/echo") is not None
    assert await PluginKeptDataRepository.prune(kept_at + KEPT_DATA_RETENTION_S) == 1
    assert await PluginKvRepository.keys(iid) == []
    assert await _secrets(test_db, iid) == []
    assert [r[0] for r in await _kept(test_db)] == ["ghost"]
    assert await PluginKeptDataRepository.find("acme.echo", "custom", "acme/echo") is None
    assert await PluginKeptDataRepository.prune(kept_at + 10 + KEPT_DATA_RETENTION_S) == 1
    assert await PluginKvRepository.keys("ghost") == []
    assert await _kept(test_db) == []


async def test_local_kept_data_is_never_taken_back_by_an_install(test_db):
    from app.repository.plugins import PluginKeptDataRepository

    row = await PluginRepository.create(
        plugin_id="me.local", label="Local", kind="local", version="1", manifest={}
    )
    await PluginKvRepository.set(row["instance_id"], "k", 1)
    await PluginRepository.delete(row["instance_id"], keep_data=True)
    assert [r[2] for r in await _kept(test_db)] == ["local"]
    assert await PluginKeptDataRepository.find("me.local", "local", None) is None


async def test_keep_data_without_data_records_nothing(test_db):
    row = await PluginRepository.create(
        plugin_id="acme.echo", label="Echo", kind="custom", version="1", manifest={}
    )
    await PluginRepository.delete(row["instance_id"], keep_data=True)
    assert await _kept(test_db) == []


async def test_a_restore_reuses_an_id_only_for_the_same_owner(test_db):
    from app.repository.plugins import PluginKeptDataRepository

    row = await PluginRepository.create(
        plugin_id="acme.echo",
        label="Echo",
        kind="custom",
        version="1",
        manifest={},
        repository="acme/echo",
    )
    iid = row["instance_id"]
    await PluginKvRepository.set(iid, "k", 1)
    await PluginRepository.delete(iid, keep_data=True)
    async with test_db.tx() as conn:
        assert not await PluginKeptDataRepository.restorable_id(
            conn, iid, "acme.echo", "custom", "mallory/echo"
        )
        assert not await PluginKeptDataRepository.restorable_id(
            conn, iid, "other.plugin", "custom", "acme/echo"
        )
        assert await PluginKeptDataRepository.restorable_id(
            conn, "fresh-id", "acme.echo", "custom", "acme/echo"
        )
        await conn.execute(
            "INSERT INTO plugin_kv (instance_id, key, value, updated_at) VALUES ('ghost', 'k', '1', 0)"
        )
        # Data with no record: never handed to a restored row.
        assert not await PluginKeptDataRepository.restorable_id(
            conn, "ghost", "acme.echo", "custom", "acme/echo"
        )
        assert await PluginKeptDataRepository.restorable_id(
            conn, iid, "acme.echo", "custom", "acme/echo"
        )
    assert [r[0] for r in await _kept(test_db)] == []


async def test_archive_mismatch_through_the_route(api, manager, net, monkeypatch, online, tmp_path):
    data = build_archive(tmp_path)
    net.serve(ARCHIVE_URL, data)

    async def resolve(plugin_id, version, repository):
        return remote(data, expected_sha256="f" * 64)

    monkeypatch.setattr(installer, "_resolvers", {"custom": resolve})
    response = await install(api)
    assert response.status_code == 400
    assert code_of(response) == "archive_mismatch"
    assert response.json()["detail"]["params"] == {"fields": ["sha256"]}
    assert leftovers(manager.plugins_root()) == []
    assert await PluginRepository.get_all() == []


async def test_incompatible_plugin_is_refused(api, manager, catalog, tmp_path):
    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path, compat={"meshloom": ">=6.0.0"})
    response = await install(api)
    assert response.status_code == 409
    assert code_of(response) == "incompatible"
    assert leftovers(manager.plugins_root()) == []
    assert await PluginRepository.get_all() == []


async def test_store_offline_and_unknown_sources(api, manager, net, monkeypatch):
    async def resolve(plugin_id, version, repository):
        raise AssertionError("resolved while offline")

    monkeypatch.setattr(installer, "_resolvers", {"custom": resolve})
    response = await install(api)
    assert (response.status_code, code_of(response)) == (409, "store_offline")
    store = await install(api, kind="store")
    assert (store.status_code, code_of(store)) == (404, "plugin_source_unavailable")
    assert net.requests == []


async def test_reinstall_brings_back_the_code_of_a_restored_instance(
    api, manager, catalog, tmp_path
):
    sources, asked = catalog
    data = build_archive(tmp_path)
    sources["acme.echo"] = data
    manifest = entry_of(data)
    row = await PluginRepository.create(
        plugin_id="acme.echo",
        label="Restored",
        kind="custom",
        version="1.0.0",
        manifest=manifest,
        granted_permissions=installer.declared_permissions(manifest),
        status="needs_attention",
        attention={"type": "reinstall"},
        repository="acme/echo",
    )
    iid = row["instance_id"]
    response = await api.post(f"/api/plugins/{iid}/reinstall")
    assert response.status_code == 200, response.text
    assert response.json()["attention"]["type"] == "restored"
    assert asked[-1] == ("acme.echo", "1.0.0", "acme/echo")
    assert (manager.plugins_root() / "acme.echo" / "manifest.json").is_file()
    stored = await PluginRepository.get(iid)
    assert stored is not None
    assert stored["archive_sha256"] == hashlib.sha256(data).hexdigest()

    again = await api.post(f"/api/plugins/{iid}/reinstall")
    assert (again.status_code, code_of(again)) == (409, "plugin_not_reinstallable")


async def test_conflicting_kind_or_version_is_refused(api, manager, catalog, tmp_path):
    sources, _ = catalog
    sources["acme.echo"] = build_archive(tmp_path)
    assert (await install(api)).status_code == 201
    response = await install(api, kind="store")
    assert (response.status_code, code_of(response)) == (409, "plugin_kind_conflict")
    other = await install(api, version="2.0.0")
    assert (other.status_code, code_of(other)) == (409, "plugin_version_conflict")


def test_failed_extraction_spares_a_concurrent_staging_dir(tmp_path: Path) -> None:
    """A failed install empties only its own `_staging/<uuid>`, never a sibling's."""
    sibling = tmp_path / installer._STAGING / "other-install"
    sibling.mkdir(parents=True)
    (sibling / "manifest.json").write_text("{}")
    source = cast(installer.ArchiveSource, SimpleNamespace(tier="third_party", repository=None))
    with pytest.raises(PluginValidationError):
        installer._stage(b"not a zip", "0" * 64, source, ("x.y", "1.0.0", {}), tmp_path)
    assert (sibling / "manifest.json").exists()
    assert [p.name for p in (tmp_path / installer._STAGING).iterdir()] == ["other-install"]


def test_bad_plugin_ids_never_reach_the_filesystem(tmp_path: Path) -> None:
    assert installer._tree_version(tmp_path / "..") is None
    assert not installer.code_installed(tmp_path, "../etc", "1.0.0")


@pytest.mark.parametrize(
    "value",
    [
        "a.b",
        "acme.weather",
        "a-b.c-d-e",
        "x1.y2",
        "a.b.c",
        "a..b",
        "-a.b",
        "a-.b",
        "a--b.c",
        "A.b",
        "a.b/",
        "../etc",
        "ab",
        "a.",
        ".b",
        "a_b.c",
        "a.b ",
        "é.b",
        "a" * 63 + ".b",
        "",
    ],
)
def test_plugin_id_check_matches_the_pattern(value: str) -> None:
    expected = bool(re.fullmatch(installer._PLUGIN_ID_RE, value)) and len(value) <= 64
    assert installer._is_plugin_id(value) is expected
