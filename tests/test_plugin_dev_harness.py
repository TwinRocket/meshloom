"""`python -m meshloom_plugin.dev run --replay` and `pack` (MSHL-208, Store T27)."""

# ruff: noqa: F811  (the fixtures of the custom repository tests are imported by name)

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.plugins.package import validate_package
from meshloom_plugin import dev

# The pack-to-install test reuses the custom repository harness.
from tests.test_plugin_installer import GRANT, set_version
from tests.test_plugin_installer import manager as manager  # noqa: F401  (fixture)
from tests.test_store_repositories import (  # noqa: F401  (fixtures)
    REPO,
    _reset,
    api,
    net,
    online,
    publish,
)

ROOT = Path(__file__).resolve().parent.parent
ECHO = ROOT / "tests/plugin_dev/echo"
FIXTURE = ROOT / "tests/plugin_dev/echo.replay.json"
CLOCK = 1_700_000_000


@pytest.fixture
def plugin(tmp_path):
    dest = tmp_path / "plugin"
    shutil.copytree(ECHO, dest)
    return dest


def message(sender: str = "Alice", at: int = 0, **params) -> dict:
    return {"hook": "message", "at": at, "params": {"sender_name": sender, **params}}


def replay(plugin: Path, **fixture) -> tuple[dev.ReplayReport, list[str]]:
    lines: list[str] = []
    report = dev.replay_directory(plugin, {"clock": CLOCK, **fixture}, trace=lines.append)
    return report, lines


def refusal_codes(report: dev.ReplayReport) -> list[str]:
    return [r["code"] for r in report.refusals]


READ = {"messages.read": {"channels": "public", "direct": True}}

# A plugin that survives its refusals.
TRYING = """
from meshloom_plugin import host


async def on_message(event):
    for params in [
        {"to": "reply", "text": "one"},
        {"to": {"channel_key": "AA" * 16}, "text": "elsewhere"},
        {"to": "reply", "text": "x" * 400},
    ]:
        try:
            await host().call("messages.send", params)
        except Exception as exc:
            await host().call("log", {"level": "info", "message": repr(exc)})
    try:
        await host().call("storage.set", {"key": "k", "value": 1})
    except Exception:
        pass
"""


# --- run --replay -----------------------------------------------------------------


def test_replay_prints_the_calls_and_the_reply(capsys):
    assert dev.main(["run", str(ECHO), "--replay", str(FIXTURE)]) == 0
    out = capsys.readouterr().out
    assert '-> messages.send {"to": "reply", "text": "hello"}' in out
    assert '<- {"sent": true, "message_id": null}' in out
    assert "nothing is transmitted" in out
    assert out.strip().endswith("OK")


def test_replay_json(capsys):
    assert dev.main(["run", str(ECHO), "--replay", str(FIXTURE), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is True
    assert data["plugin"] == {"id": "acme.echo", "version": "1.0.0"}
    assert data["sends"] == [{"to": "reply", "text": "hello"}]
    assert data["events"] == [{"n": 1, "hook": "message", "delivered": True}]
    assert data["isolation"] == "none (development only)"


def test_a_missing_permission_is_refused_as_the_server_does(plugin):
    report, lines = replay(
        plugin, granted_permissions={"messages.read": READ["messages.read"]}, events=[message()]
    )
    assert refusal_codes(report) == ["permission_denied"]
    assert report.sends == []
    assert any(
        "permission_denied: messages.send needs the messages.send permission" in x for x in lines
    )
    assert report.refusals[0]["method"] == "messages.send"


def test_refusals_carry_the_servers_params(plugin):
    (plugin / "plugin.py").write_text(TRYING, "utf-8")
    report, lines = replay(plugin, events=[message()])
    assert report.ok, report.errors
    # reply ok; explicit channel under targets "reply"; too long; storage not declared.
    assert refusal_codes(report) == ["target_not_allowed", "message_too_long", "permission_denied"]
    assert [s["text"] for s in report.sends] == ["one"]
    assert any('"targets": "reply"' in x for x in lines)
    assert any("message_too_long" in x and "400 bytes" in x for x in lines)


def test_the_per_plugin_quota_refuses_the_next_send(plugin):
    report, lines = replay(
        plugin,
        granted_permissions={**READ, "messages.send": {"targets": "reply", "per_hour": 1}},
        events=[message("Alice", 0), message("Bob", 20)],
    )
    assert refusal_codes(report) == ["quota_exceeded"]
    assert len(report.sends) == 1
    line = next(x for x in lines if "params {" in x)
    assert '"scope": "plugin"' in line and '"period": "hour"' in line and '"limit": 1' in line


def test_the_user_can_lower_the_quota(plugin):
    report, _ = replay(
        plugin,
        quotas={"messages.send": {"per_hour": 1}},
        events=[message("A"), message("B", 20)],
    )
    assert refusal_codes(report) == ["quota_exceeded"]


def test_the_sender_cooldown_and_the_quiet_window(plugin):
    cooldown, lines = replay(plugin, events=[message("Alice", 0), message("Alice", 10)])
    assert refusal_codes(cooldown) == ["quota_exceeded"]
    assert any('"scope": "sender_cooldown"' in x for x in lines)
    # Another sender, received 3 s after the first reply: the quiet window.
    quiet, lines = replay(plugin, events=[message("Alice", 0), message("Bob", 3)])
    assert refusal_codes(quiet) == ["quota_exceeded"]
    assert any('"scope": "quiet_window"' in x for x in lines)
    # Far enough apart and from other senders: both go out.
    fine, _ = replay(plugin, events=[message("Alice", 0), message("Bob", 60)])
    assert fine.refusals == [] and len(fine.sends) == 2


def test_the_destination_budget_stops_a_busy_channel(plugin):
    report, lines = replay(plugin, events=[message(f"user{i}", i * 60) for i in range(21)])
    assert len(report.sends) == 20
    assert refusal_codes(report) == ["quota_exceeded"]
    assert any('"scope": "destination"' in x for x in lines)


def test_an_event_the_server_would_not_deliver_is_skipped(plugin):
    report, _ = replay(plugin, scope={"messages": "none"}, events=[message()])
    assert report.events[0]["delivered"] is False
    assert report.calls == [] and report.ok


def test_a_direct_message_needs_the_direct_grant(plugin):
    dm = message(is_dm=True)
    granted = {"messages.read": {"channels": "public", "direct": False}}
    skipped, _ = replay(
        plugin,
        granted_permissions={**granted, "messages.send": {"targets": "reply"}},
        events=[dm],
    )
    assert skipped.events[0]["delivered"] is False
    delivered, _ = replay(plugin, events=[dm])
    assert delivered.events[0]["delivered"] is True and delivered.ok


def test_a_failing_handler_fails_the_run(plugin, tmp_path, capsys):
    (plugin / "plugin.py").write_text(
        "async def on_message(event):\n    raise RuntimeError('boom')\n", "utf-8"
    )
    path = tmp_path / "f.json"
    path.write_text(json.dumps({"events": [message()]}), "utf-8")
    assert dev.main(["run", str(plugin), "--replay", str(path)]) == 1
    out = capsys.readouterr().out
    assert "handler_failed" in out and out.strip().endswith("FAILED (1 error(s))")


def test_an_invalid_plugin_is_reported_like_check(plugin, capsys):
    (plugin / "LICENSE").unlink()
    assert dev.main(["run", str(plugin), "--replay", str(FIXTURE)]) == 1
    assert "LICENSE is required" in capsys.readouterr().out


@pytest.mark.parametrize(
    "fixture",
    [
        {"events": []},
        {"events": [{"hook": "nope"}]},
        {"events": [{"hook": "message", "params": []}]},
        {"events": [{"hook": "message", "at": "soon"}]},
        {"events": [{"hook": "message"}], "scope": []},
        {"events": [{"hook": "message"}], "contacts": {}},
        {"events": [{"hook": "message"}], "clock": "now"},
        [],
    ],
)
def test_a_bad_fixture_is_a_usage_error(tmp_path, capsys, fixture):
    path = tmp_path / "f.json"
    path.write_text(json.dumps(fixture), "utf-8")
    assert dev.main(["run", str(ECHO), "--replay", str(path)]) == 2
    assert capsys.readouterr().err.startswith("error:")


def test_missing_fixture_and_bad_directory_are_usage_errors(tmp_path):
    assert dev.main(["run", str(ECHO), "--replay", str(tmp_path / "none.json")]) == 2
    assert dev.main(["run", str(tmp_path / "nowhere"), "--replay", str(FIXTURE)]) == 2


def _declare(plugin: Path, **permissions) -> None:
    manifest = json.loads((plugin / "manifest.json").read_text())
    manifest["permissions"].update(permissions)
    (plugin / "manifest.json").write_text(json.dumps(manifest), "utf-8")


def test_storage_timer_and_status_work_in_memory(plugin):
    _declare(plugin, storage={}, timer={})
    (plugin / "plugin.py").write_text(
        """
from meshloom_plugin import host


async def on_message(event):
    h = host()
    await h.call("storage.set", {"key": "seen", "value": event["message_text"]})
    seen = await h.call("storage.get", {"key": "seen"})
    keys = await h.call("storage.list", {})
    timer = await h.call("timer.set", {"seconds": 60})
    await h.call("status.set", {"summary": "ok"})
    await h.call("log", {"level": "info", "message": f"{seen} {keys} {timer}"})
    try:
        await h.call("timer.set", {"seconds": 1})
    except Exception:
        pass
""",
        "utf-8",
    )
    report, lines = replay(
        plugin,
        granted_permissions={**READ, "storage": {}, "timer": {}},
        events=[message(message_text="hi")],
    )
    assert report.ok, report.errors
    assert refusal_codes(report) == ["invalid_params"]
    assert any('<- {"timer_id": 1}' in x for x in lines)
    assert any("hi ['seen'] {'timer_id': 1}" in x for x in lines)


def test_http_never_uses_the_network(plugin):
    _declare(plugin, http={"hosts": ["example.com"]})
    (plugin / "plugin.py").write_text(
        """
from meshloom_plugin import host


async def on_message(event):
    for url in ("https://example.com/a", "https://example.com/b"):
        try:
            await host().call("http.fetch", {"url": url})
        except Exception:
            pass
""",
        "utf-8",
    )
    report, lines = replay(
        plugin,
        granted_permissions={**READ, "http": {"hosts": ["example.com"]}},
        http={"https://example.com/a": {"status": 200, "body": "pong"}},
        events=[message()],
    )
    assert refusal_codes(report) == ["fetch_failed"]
    assert any('"body": "pong"' in x for x in lines)


def test_the_replay_uses_the_servers_core_not_a_copy():
    from app.plugins import quotas
    from app.plugins.capabilities import base, grants, messages

    assert dev.REGISTRY is base.REGISTRY
    assert dev.effective_permissions is grants.effective_permissions
    assert dev.message_visible is grants.message_visible
    assert dev.SendGuard is quotas.SendGuard
    assert dev.effective_send_limits is quotas.effective_send_limits
    assert dev.messages is messages


def test_the_harness_imports_neither_fastapi_nor_the_database():
    code = (
        "import sys, json, pathlib, tempfile\n"
        "from meshloom_plugin import dev\n"
        "fixture = json.loads(pathlib.Path('tests/plugin_dev/echo.replay.json').read_text())\n"
        "echo = pathlib.Path('tests/plugin_dev/echo')\n"
        "assert dev.replay_directory(echo, fixture).ok\n"
        "with tempfile.TemporaryDirectory() as out:\n"
        "    assert dev.pack_directory(echo, pathlib.Path(out))[1]\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('fastapi', 'starlette', 'aiosqlite')"
        " or m in ('app.database', 'app.main', 'app.config', 'app.repository')]\n"
        "assert not bad, bad\n"
    )
    run = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120
    )
    assert run.returncode == 0, run.stderr


# --- pack --------------------------------------------------------------------------


def test_pack_writes_a_valid_archive_and_its_hash(plugin, tmp_path, capsys):
    out = tmp_path / "out"
    assert dev.main(["pack", str(plugin), "--out", str(out)]) == 0
    archive = (out / "plugin.zip").read_bytes()
    digest = hashlib.sha256(archive).hexdigest()
    assert (out / "plugin.zip.sha256").read_text() == f"{digest}  plugin.zip\n"
    printed = capsys.readouterr().out
    assert "Wrote" in printed and digest in printed
    assert validate_package(archive, origin="third_party").manifest.id == "acme.echo"


def test_pack_is_reproducible(plugin, tmp_path):
    first = tmp_path / "a"
    dev.pack_directory(plugin, first, dry_run=False)
    # Other mtimes and a fresh copy must not change a byte.
    for path in plugin.rglob("*"):
        os.utime(path, (CLOCK + 99999, CLOCK + 99999))
    copy = tmp_path / "copy"
    shutil.copytree(plugin, copy)
    second = tmp_path / "b"
    dev.pack_directory(copy, second, dry_run=False)
    assert (first / "plugin.zip").read_bytes() == (second / "plugin.zip").read_bytes()
    assert (first / "plugin.zip.sha256").read_text() == (second / "plugin.zip.sha256").read_text()


def test_pack_entries_are_sorted_and_stamped_with_the_zip_epoch(plugin, tmp_path):
    (plugin / "lib").mkdir()
    (plugin / "lib" / "util.py").write_text("X = 1\n", "utf-8")
    (plugin / "a.txt").write_text("a", "utf-8")
    (plugin / "__pycache__").mkdir()
    (plugin / "__pycache__" / "x.pyc").write_bytes(b"\0")
    _, archive, _ = dev.pack_directory(plugin, tmp_path / "out", dry_run=False)
    assert archive is not None
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert names == sorted(names)
        assert "lib/util.py" in names and not any("__pycache__" in n for n in names)
        assert {i.date_time for i in zf.infolist()} == {(1980, 1, 1, 0, 0, 0)}
        assert {i.external_attr >> 16 for i in zf.infolist()} == {0o644}


def test_pack_refuses_a_failing_check_and_writes_nothing(plugin, tmp_path, capsys):
    (plugin / "LICENSE").unlink()
    out = tmp_path / "out"
    assert dev.main(["pack", str(plugin), "--out", str(out)]) == 1
    assert not out.exists()
    assert "LICENSE is required" in capsys.readouterr().out


def test_pack_runs_the_dry_run_unless_told_not_to(plugin, tmp_path):
    (plugin / "plugin.py").write_text("raise RuntimeError('import time')\n", "utf-8")
    report, archive, _ = dev.pack_directory(plugin, tmp_path / "o1")
    assert not report.ok and archive is None
    report, archive, _ = dev.pack_directory(plugin, tmp_path / "o2", dry_run=False)
    assert report.ok and archive is not None


def test_pack_refuses_an_output_inside_the_plugin(plugin):
    report, archive, _ = dev.pack_directory(plugin, plugin / "dist", dry_run=False)
    assert archive is None and [f.code for f in report.errors] == ["output_inside_plugin"]
    assert not (plugin / "dist").exists()


def test_pack_json_and_usage(plugin, tmp_path, capsys):
    assert dev.main(["pack", str(plugin), "--out", str(tmp_path / "o"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] and data["archive"]["path"].endswith("plugin.zip")
    assert len(data["archive"]["sha256"]) == 64
    assert dev.main(["pack", str(tmp_path / "nowhere")]) == 2


async def test_packed_output_installs_through_the_custom_repository_flow(
    plugin, tmp_path, api, manager, net, online, monkeypatch
):
    from app.plugins import installer
    from app.routers import plugins as plugins_router

    monkeypatch.setattr(installer, "BUNDLED_ROOT", tmp_path / "no-bundled")
    monkeypatch.setattr(plugins_router, "plugin_manager", manager)
    set_version(monkeypatch, "5.0.0")
    out = tmp_path / "release"
    _, archive, _ = dev.pack_directory(plugin, out, dry_run=False)
    assert archive is not None
    # The release assets are the author's files: manifest.json, and plugin.zip with
    # plugin.zip.sha256 exactly as `pack` wrote them.
    publish(
        net,
        archive.read_bytes(),
        manifest=(plugin / "manifest.json").read_bytes(),
        sha=(out / "plugin.zip.sha256").read_bytes(),
    )
    added = await api.post("/api/store/repositories", json={"repository": REPO})
    assert added.status_code == 201, added.text
    response = await api.post(
        "/api/plugins/install",
        json={
            "kind": "custom",
            "plugin_id": "acme.echo",
            "repository": REPO,
            "granted_permissions": GRANT,
        },
    )
    assert response.status_code == 201, response.text
    assert (response.json()["kind"], response.json()["version"]) == ("custom", "1.0.0")


# --- hidden files, divergences from the server ---------------------------------------


def test_pack_leaves_out_hidden_files_and_lists_them(plugin, tmp_path, capsys):
    (plugin / ".env").write_text("TOKEN=secret", "utf-8")
    (plugin / ".env.local").write_text("TOKEN=secret", "utf-8")
    (plugin / ".git").mkdir()
    (plugin / ".git" / "config").write_text("[remote]", "utf-8")
    (plugin / ".venv").mkdir()
    (plugin / ".venv" / "lib.py").write_text("x = 1", "utf-8")
    (plugin / "lib").mkdir()
    (plugin / "lib" / ".DS_Store").write_bytes(b"\0")
    (plugin / "lib" / "mod.pyc").write_bytes(b"\0")
    (plugin / "lib" / "mod.py").write_text("Y = 2\n", "utf-8")
    out = tmp_path / "out"
    assert dev.main(["pack", str(plugin), "--out", str(out)]) == 0
    with zipfile.ZipFile(out / "plugin.zip") as zf:
        names = zf.namelist()
    assert "lib/mod.py" in names
    assert not [n for n in names if n.startswith(".") or "/." in n or n.endswith(".pyc")]
    printed = capsys.readouterr().out
    for left_out in (".env", ".env.local", ".git/", ".venv/", "lib/.DS_Store", "lib/mod.pyc"):
        assert left_out in printed
    assert "secret" not in printed


def test_check_json_lists_the_skipped_paths(plugin, capsys):
    (plugin / ".env").write_text("TOKEN=secret", "utf-8")
    assert dev.main(["check", str(plugin), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["skipped"] == [".env"]


def test_hidden_files_do_not_change_the_archive(plugin, tmp_path):
    clean = tmp_path / "clean"
    dev.pack_directory(plugin, clean, dry_run=False)
    (plugin / ".env").write_text("TOKEN=secret", "utf-8")
    (plugin / ".git").mkdir()
    (plugin / ".git" / "config").write_text("x", "utf-8")
    dirty = tmp_path / "dirty"
    dev.pack_directory(plugin, dirty, dry_run=False)
    assert (clean / "plugin.zip").read_bytes() == (dirty / "plugin.zip").read_bytes()


def _call_plugin(calls: list[tuple[str, dict]]) -> str:
    return (
        "from meshloom_plugin import host\n\n\nasync def on_message(event):\n"
        + f"    for method, params in {calls!r}:\n"
        + "        try:\n            await host().call(method, params)\n"
        + "        except Exception:\n            pass\n"
    )


def _try_calls(plugin: Path, calls, **fixture):
    (plugin / "plugin.py").write_text(_call_plugin(calls), "utf-8")
    return replay(plugin, events=[message()], **fixture)


def test_channels_list_hides_private_channels_without_channels_all(plugin):
    _declare(plugin, **{"channels.read": {}})
    channels = [
        {"key": "8B3387E9C5CDEA6AC9E5EDBAA115CD72", "name": "Public"},
        {"key": "CD" * 16, "name": "secret-room"},
    ]
    granted = {**READ, "channels.read": {}}
    report, lines = _try_calls(
        plugin, [("channels.list", {})], granted_permissions=granted, channels=channels
    )
    listed = next(x for x in lines if x.startswith("    <- ") and "Public" in x)
    assert "secret-room" not in listed and '"is_public": true' in listed
    everything = {
        **granted,
        "messages.read": {"channels": "all", "direct": True},
    }
    _declare(plugin, **{"messages.read": {"channels": "all", "direct": True}})
    _, lines = _try_calls(
        plugin, [("channels.list", {})], granted_permissions=everything, channels=channels
    )
    assert any("secret-room" in x and '"is_public": false' in x for x in lines)


def test_contacts_arguments_are_validated_like_the_server(plugin):
    _declare(plugin, **{"contacts.read": {}})
    report, _ = _try_calls(
        plugin,
        [
            ("contacts.get", {"key": "nothex"}),
            ("contacts.list", {"limit": 9999}),
            ("contacts.list", {"limit": 5, "offset": -1}),
            ("contacts.get", {"key": "ab" * 32}),
        ],
        granted_permissions={**READ, "contacts.read": {}},
    )
    assert refusal_codes(report) == ["invalid_params"] * 3


def _fake_host(plugin: Path, **permissions) -> dev.FakeHost:
    """A `FakeHost` driven directly: a plugin cannot send a value over 100 KiB itself."""
    check = dev.CheckReport()
    staged = dev._package_stage(plugin, "third_party", check)
    assert staged is not None
    fixture = {"granted_permissions": {**READ, **permissions}, "events": []}
    return dev.FakeHost(staged[0], fixture, dev._Clock(CLOCK), lambda _line: None)


def _dispatch(host: dev.FakeHost, method: str, params: dict):
    return host.loop.run_until_complete(host.dispatch(method, params))


def test_storage_limits_are_the_servers(plugin):
    from app.plugins.capabilities.base import CapabilityError

    _declare(plugin, storage={})
    host = _fake_host(plugin, storage={})
    for params in (
        {"key": "big", "value": "x" * (256 * 1024 + 1)},
        {"key": "bad\x00key", "value": 1},
        {"key": "k", "value": float("nan")},
        {"key": "k"},
    ):
        with pytest.raises(CapabilityError) as exc:
            _dispatch(host, "storage.set", params)
        assert exc.value.refusal == "invalid_params"
    with pytest.raises(CapabilityError):
        _dispatch(host, "storage.list", {"limit": 0})
    _dispatch(
        host, "storage.set", {"key": "ok", "value": "x" * (256 * 1024 - 2)}
    )  # exactly 256 KiB
    assert _dispatch(host, "storage.get", {"key": "ok"}) == "x" * (256 * 1024 - 2)


def test_storage_quota_is_the_servers(plugin):
    from app.plugins.capabilities.base import CapabilityError

    _declare(plugin, storage={})
    host = _fake_host(plugin, storage={})
    chunk = "x" * (200 * 1024)
    for i in range(25):
        _dispatch(host, "storage.set", {"key": f"k{i}", "value": chunk})
    with pytest.raises(CapabilityError) as exc:
        _dispatch(host, "storage.set", {"key": "k25", "value": chunk})
    assert exc.value.refusal == "quota_exceeded" and exc.value.params == {"limit": 5 * 1024 * 1024}
    _dispatch(host, "storage.set", {"key": "k0", "value": "small"})  # an overwrite frees space


def test_a_reply_to_an_unknown_channel_is_refused(plugin):
    report, _ = replay(plugin, channels=[{"key": "AA" * 16, "name": "other"}], events=[message()])
    assert refusal_codes(report) == ["target_not_allowed"]


def test_notifications_are_traced(plugin):
    (plugin / "plugin.py").write_text(
        "from meshloom_plugin import host\n\n\nasync def on_message(event):\n"
        "    host().notify('log', {'level': 'info', 'message': 'hello log'})\n"
        "    await host().call('status.set', {'summary': 'busy'})\n",
        "utf-8",
    )
    report, lines = replay(plugin, events=[message()])
    assert any("-> log" in x and "hello log" in x for x in lines), lines
    assert any("-> status.set" in x for x in lines)


def test_a_notified_method_other_than_log_is_ignored_like_the_server(plugin):
    (plugin / "plugin.py").write_text(
        "from meshloom_plugin import host\n\n\nasync def on_message(event):\n"
        "    host().notify('messages.send', {'to': 'reply', 'text': 'hi'})\n"
        "    host().notify('status.set', {'summary': 'busy'})\n",
        "utf-8",
    )
    report, lines = replay(plugin, events=[message()])
    assert report.sends == [] and refusal_codes(report) == []
    sent = [c for c in report.calls if c["method"] == "messages.send"]
    assert sent and all(c["notification"] and c["ignored"] for c in sent)
    assert any("notification ignored" in x for x in lines), lines


def test_the_shared_helpers_are_the_servers_functions():
    from app.plugins.capabilities import base, channels, contacts, messages, storage

    assert dev.require_permission is base.require_permission
    assert dev.messages.check_size is messages.check_size
    assert dev.messages.check_region is messages.check_region
    assert dev.messages.reply_context is messages.reply_context
    assert dev.messages.quota_refusal is messages.quota_refusal
    assert dev.channels is channels and dev.contacts is contacts and dev.storage is storage
