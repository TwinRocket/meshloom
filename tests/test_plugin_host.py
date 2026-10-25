"""The plugin host supervisor (`app/plugins/host.py`) against real child processes.

Every instance runs the real runner (`meshloom_plugin.runner`) on a fixture
plugin written here (echo, sleep, ignore-cancel, crash, cpu-burn...). Budgets
are shortened through `Budgets`; the CPU reader is injected where a controlled
value is enough, and the real `/proc` reader runs on Linux.
"""

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.plugins import host as host_module
from app.plugins import isolation, lifecycle
from app.plugins.host import (
    Budgets,
    LogRing,
    PluginCapacityError,
    PluginHostError,
    PluginManager,
    instance_cap,
    read_proc_cpu_seconds,
)
from app.repository.plugins import PluginRepository
from tests.plugin_isolation_support import available_backends, shared_tmp_dir

LINUX = sys.platform.startswith("linux")
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

FAST = Budgets(
    init_timeout=10.0,
    event_timeout=0.6,
    cancel_grace=0.6,
    shutdown_grace=1.0,
    backoff_initial=0.05,
    backoff_max=0.2,
    stagger=0.05,
    message_settle=0.0,
)

# What a migrated bot holds (the supervisor tests are not about the filters, which
# tests/test_plugin_capabilities.py covers): every message, sends anywhere.
BOT_PERMISSIONS: dict[str, Any] = {
    "messages.read": {"channels": "all", "direct": True, "outgoing": True},
    "messages.send": {"targets": "any"},
}

PLUGINS = {
    "echo": """
        import sys

        def on_message(params):
            print("got", params.get("message_text"), file=sys.stderr, flush=True)
            return {"echo": params.get("message_text")}
    """,
    "sleep": """
        import asyncio

        async def on_message(params):
            await asyncio.sleep(60)
    """,
    "ignore_cancel": """
        import asyncio
        import sys

        async def on_message(params):
            print("got", params.get("message_text"), file=sys.stderr, flush=True)
            while True:
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    pass
    """,
    "slow": """
        import asyncio

        async def on_message(params):
            await asyncio.sleep(30)
    """,
    "crash_start": """
        raise RuntimeError("broken at import")
    """,
    "crash_event": """
        import os

        def on_message(params):
            os._exit(7)
    """,
    "cpu_burn": """
        def on_message(params):
            while True:
                pass
    """,
    "ignore_shutdown": """
        import asyncio

        async def on_shutdown(params):
            while True:
                try:
                    await asyncio.sleep(60)
                except asyncio.CancelledError:
                    pass
    """,
    "flood": """
        import asyncio
        import meshloom_plugin
        from meshloom_plugin.errors import HostError

        async def on_message(params):
            calls = [meshloom_plugin.host().call("test.slow", {}) for _ in range(40)]
            results = await asyncio.gather(*calls, return_exceptions=True)
            return sorted(
                r.error["code"] if isinstance(r, HostError) else r for r in results
            )
    """,
    "deaf": """
        import time

        def on_message(params):
            time.sleep(120)
    """,
    "spammer": """
        import meshloom_plugin

        async def on_message(params):
            for n in range(100):
                try:
                    await meshloom_plugin.host().call(
                        "messages.send",
                        {"event_id": params["event_id"], "to": "reply", "text": "x" * 150},
                    )
                except Exception:
                    return n
    """,
    "sender": """
        import meshloom_plugin

        async def on_message(params):
            await meshloom_plugin.host().call(
                "messages.send", {"event_id": params["event_id"], "to": "reply", "text": "pong"}
            )
            return "sent"
    """,
}


def msg(text: object) -> dict[str, Any]:
    """A fanout DM dict (no repository lookup: the sender name is given)."""
    return {"type": "PRIV", "conversation_key": "ab" * 32, "sender_name": "Ann", "text": str(text)}


def write_plugin(root: Path, plugin_id: str, kind: str) -> None:
    directory = root / plugin_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text('{"entry": "plugin.py"}', encoding="utf-8")
    (directory / "plugin.py").write_text(textwrap.dedent(PLUGINS[kind]), encoding="utf-8")


async def create_instance(
    root: Path, kind: str, *, hooks: tuple[str, ...] = ("message",), **kwargs: Any
) -> str:
    plugin_id = f"test.{kind.replace('_', '-')}"
    write_plugin(root, plugin_id, kind)
    row = await PluginRepository.create(
        plugin_id=plugin_id,
        label=kind,
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": list(hooks), "permissions": BOT_PERMISSIONS},
        granted_permissions=BOT_PERMISSIONS,
        enabled=True,
        **kwargs,
    )
    return row["instance_id"]


def describe(instance) -> str:
    return (
        f"status={instance.status} last_error={instance.last_error!r} "
        f"failures={instance.consecutive_failures} restarts={instance.restarts} "
        f"log={instance.log.lines()[-5:]!r}"
    )


async def wait_until(
    predicate, timeout: float = 15.0, what: str = "condition", instance=None
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            detail = f": {describe(instance)}" if instance is not None else ""
            raise AssertionError(f"timed out waiting for {what}{detail}")
        await asyncio.sleep(0.02)


def pid_gone(pid: int) -> bool:
    """True once `pid` is reaped: a zombie still answers signal 0."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:  # alive under another uid (setuid level, no CAP_KILL)
        return False
    return False


@pytest.fixture
def tmp_path(tmp_path: Path):
    """Where the selected level is setuid (root, or CAP_SETUID and CAP_SETGID), the
    plugin uid cannot traverse pytest's 0700 base temp dir: the runner would refuse
    `plugin_dir` (MSHL-324). Those get a traversable directory instead."""
    if "setuid" not in available_backends():
        yield tmp_path
        return
    path = shared_tmp_dir("meshloom-host-test-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def statuses(monkeypatch):
    seen: list[dict[str, Any]] = []
    monkeypatch.setattr(host_module, "_broadcast_status", lambda payload: seen.append(payload))
    return seen


@pytest.fixture
async def manager(test_db, tmp_path, statuses):
    mgr = PluginManager(
        FAST,
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    yield mgr
    await mgr.stop_all(5.0)


async def running(mgr: PluginManager, instance_id: str):
    await mgr.start(instance_id)
    instance = mgr.get(instance_id)
    assert instance is not None
    await wait_until(lambda: instance.status == "running", what="running", instance=instance)
    return instance


# --- budgets ----------------------------------------------------------------------


async def test_cooperative_cancel_keeps_the_process(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "sleep")
    instance = await running(manager, instance_id)
    pid = instance.pid

    with pytest.raises(PluginHostError) as exc:
        await manager.dispatch_test(instance_id, "message", {})
    assert exc.value.code == "event_timeout"
    assert instance.status == "running"
    assert instance.pid == pid
    assert "timed out after 0.6 s" in (instance.last_error or "")


async def test_ignored_cancel_kills_restarts_and_drops_without_replay(manager, tmp_path, statuses):
    instance_id = await create_instance(tmp_path / "plugins", "ignore_cancel")
    instance = await running(manager, instance_id)
    first_pid = instance.pid

    manager.dispatch("message", msg(1))
    manager.dispatch("message", msg(2))
    await wait_until(lambda: instance.restarts == 1 and instance.status == "running")

    assert instance.pid != first_pid
    assert pid_gone(first_pid)
    assert "ignored event.cancel" in (instance.last_error or "")
    assert instance.dropped_events == 2
    error_statuses = [s for s in statuses if s.get("status") == "error"]
    assert error_statuses and "ignored event.cancel" in error_statuses[0]["last_error"]
    # Neither event reached the new process.
    await asyncio.sleep(0.5)
    assert sum(1 for line in instance.log.lines() if line.startswith("got")) == 2


async def test_inbox_is_bounded_and_drops_are_logged(manager, tmp_path, caplog):
    instance_id = await create_instance(tmp_path / "plugins", "slow")
    instance = await running(manager, instance_id)
    caplog.set_level(logging.WARNING, logger="app.plugins.host")

    for n in range(150):
        manager.dispatch("message", msg(n))
        if n == 10:
            await asyncio.sleep(0.1)  # the first ones go in flight
    await asyncio.sleep(0.1)

    in_flight = len(instance._in_flight)
    assert in_flight == FAST.max_in_flight
    assert instance.queued == 100
    assert instance.dropped_events == 150 - 100 - in_flight
    assert any(
        "dropped" in r.getMessage() and "queue full" in r.getMessage() for r in caplog.records
    )


async def test_five_consecutive_crashes_is_a_crash_loop_until_manual_restart(manager, tmp_path):
    root = tmp_path / "plugins"
    instance_id = await create_instance(root, "crash_start")
    await manager.start(instance_id)
    instance = manager.get(instance_id)
    assert instance is not None

    await wait_until(
        lambda: instance.status == "crash_loop", timeout=30, what="crash_loop", instance=instance
    )
    assert instance.consecutive_failures == 5, describe(instance)
    assert instance.restarts == 4, describe(instance)
    assert "entry_load_failed" in (instance.last_error or ""), describe(instance)
    # The status is published just before the supervision task returns.
    await wait_until(lambda: not instance.active, timeout=5, what="stopped", instance=instance)
    stored = await PluginRepository.get(instance_id)
    assert stored is not None and stored["status"] == "crash_loop"

    # Fixed plugin, manual restart: a new supervisor with its count at zero.
    write_plugin(root, "test.crash-start", "echo")
    await manager.restart(instance_id)
    restarted = manager.get(instance_id)
    assert restarted is not None and restarted is not instance
    await wait_until(lambda: restarted.status == "running", instance=restarted)
    assert restarted.consecutive_failures == 0


async def test_crash_on_event_counts_and_restarts(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "crash_event")
    instance = await running(manager, instance_id)
    manager.dispatch("message", msg(0))
    await wait_until(lambda: instance.restarts == 1 and instance.status == "running")
    assert "exited unexpectedly (code 7)" in (instance.last_error or "")
    assert instance.dropped_events == 1


async def test_a_child_killed_from_outside_restarts_with_backoff(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "echo")
    instance = await running(manager, instance_id)
    pid = instance.pid
    assert pid is not None

    # Through the backend: a non-root server holds no CAP_KILL (MSHL-339).
    instance.backend.signal_group(pid, signal.SIGKILL, wait=True)
    await wait_until(lambda: instance.status == "error", what="error")
    assert instance.last_error == "killed: killed by SIGKILL"
    await wait_until(lambda: instance.status == "running" and instance.pid != pid)
    assert instance.restarts == 1
    assert pid_gone(pid)


def test_message_bytes_is_the_channel_allowance(monkeypatch):
    from types import SimpleNamespace

    from app.services.radio_runtime import radio_runtime

    monkeypatch.setattr(type(radio_runtime), "meshcore", property(lambda self: None), raising=False)
    assert host_module.message_bytes_limit() == 144
    radio = SimpleNamespace(self_info={"name": "Node"})
    monkeypatch.setattr(
        type(radio_runtime), "meshcore", property(lambda self: radio), raising=False
    )
    assert host_module.message_bytes_limit() == 156 - 4 - 2


def test_backoff_doubles_up_to_the_cap():
    budgets = Budgets()
    assert [budgets.backoff(n) for n in range(1, 9)] == [1, 2, 4, 8, 16, 32, 60, 60]


# --- CPU --------------------------------------------------------------------------


async def test_rolling_cpu_budget_kills_the_child(test_db, tmp_path, statuses):
    ticks = iter(range(0, 10_000, 3))
    mgr = PluginManager(
        replace(FAST, cpu_budget=5.0, cpu_sample_interval=0.02),
        plugins_root=tmp_path / "plugins",
        cpu_reader=lambda pid: float(next(ticks)),
        mem_total=lambda: 8 * 1024**3,
    )
    try:
        instance_id = await create_instance(tmp_path / "plugins", "echo")
        await mgr.start(instance_id)
        instance = mgr.get(instance_id)
        assert instance is not None
        await wait_until(lambda: "CPU budget exceeded" in (instance.last_error or ""))
    finally:
        await mgr.stop_all(5.0)


@pytest.mark.skipif(not LINUX, reason="/proc CPU accounting is Linux only")
async def test_rolling_cpu_budget_with_the_proc_reader(test_db, tmp_path, statuses):
    mgr = PluginManager(
        replace(
            FAST,
            event_timeout=30.0,
            cpu_budget=0.5,
            cpu_window=5.0,
            cpu_sample_interval=0.1,
        ),
        plugins_root=tmp_path / "plugins",
        mem_total=lambda: 8 * 1024**3,
    )
    try:
        instance_id = await create_instance(tmp_path / "plugins", "cpu_burn")
        instance = await running(mgr, instance_id)
        mgr.dispatch("message", msg(0))
        await wait_until(lambda: "CPU budget exceeded" in (instance.last_error or ""))
    finally:
        await mgr.stop_all(5.0)


@pytest.mark.skipif(not LINUX, reason="/proc is Linux only")
def test_proc_reader_reads_this_process():
    value = read_proc_cpu_seconds(os.getpid())
    assert value is not None and value >= 0
    assert read_proc_cpu_seconds(2**22 + 12345) is None


@pytest.mark.skipif(not LINUX, reason="/proc is Linux only")
def test_tree_reader_counts_the_descendants():
    # Under bwrap the supervised pid is bwrap; the runner burns CPU two levels down.
    burn = "import time\nt = time.process_time()\nwhile time.process_time() - t < 0.5: pass\ntime.sleep(30)"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import subprocess, sys; subprocess.run([sys.executable, '-c', {burn!r}])",
        ],
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        total = 0.0
        while time.monotonic() < deadline:
            total = host_module.read_proc_tree_cpu_seconds(child.pid) or 0.0
            if total >= 0.4:
                break
            time.sleep(0.1)
        assert total >= 0.4
        assert (read_proc_cpu_seconds(child.pid) or 0.0) < 0.4
    finally:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()
    assert host_module.read_proc_tree_cpu_seconds(2**22 + 12345) is None


async def test_rlimit_cpu_is_set_in_the_child(test_db, tmp_path, statuses):
    mgr = PluginManager(
        replace(FAST, event_timeout=60.0, runner_limits={"cpu_seconds": 1}),
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    try:
        instance_id = await create_instance(tmp_path / "plugins", "cpu_burn")
        instance = await running(mgr, instance_id)
        mgr.dispatch("message", msg(0))
        await wait_until(lambda: instance.status == "error", timeout=20)
        assert instance.last_error in (
            "cpu_rlimit: CPU time limit reached (RLIMIT_CPU)",
            "killed: killed by SIGKILL",
        )
    finally:
        await mgr.stop_all(5.0)


# --- capacity, shutdown -------------------------------------------------------------


@pytest.mark.parametrize(
    ("mem", "cap"),
    [(None, 3), (256 * 1024**2, 3), (1024**3, 8), (int(0.9 * 1024**3), 7), (64 * 1024**3, 20)],
)
def test_instance_cap_from_memory(mem, cap):
    assert instance_cap(mem) == cap


async def test_the_cap_refuses_one_more_instance(test_db, tmp_path, statuses):
    mgr = PluginManager(
        FAST, plugins_root=tmp_path / "plugins", cpu_reader=None, mem_total=lambda: 0
    )
    try:
        ids = [await create_instance(tmp_path / "plugins", "echo") for _ in range(4)]
        for instance_id in ids[:3]:
            await mgr.start(instance_id)
        with pytest.raises(PluginCapacityError) as exc:
            await mgr.start(ids[3])
        assert exc.value.code == "instance_cap_reached"
        assert exc.value.cap == 3
    finally:
        await mgr.stop_all(5.0)


async def test_shutdown_ends_every_child_within_the_bound(manager, tmp_path):
    root = tmp_path / "plugins"
    ids = [
        await create_instance(root, "echo"),
        await create_instance(root, "ignore_shutdown"),
        await create_instance(root, "ignore_cancel"),
    ]
    instances = [await running(manager, i) for i in ids]
    manager.dispatch("message", msg(1))
    pids = [i.pid for i in instances]
    assert all(pids)

    started = time.monotonic()
    await manager.stop_all(3.0)
    assert time.monotonic() - started < 3.5
    for pid in pids:
        assert pid is not None and pid_gone(pid)
    assert not manager.statuses() or all(not i.active for i in instances)


async def test_load_from_db_staggers_and_skips_sticky_statuses(manager, tmp_path):
    root = tmp_path / "plugins"
    first = await create_instance(root, "echo")
    second = await create_instance(root, "echo")
    held = await create_instance(root, "echo", status="crash_loop")
    off = await create_instance(root, "echo")
    await PluginRepository.update(off, enabled=False)

    await manager.load_from_db()
    await wait_until(lambda: manager.get(second) is not None)
    assert manager.get(first) is not None
    assert manager.get(held) is None and manager.get(off) is None
    statuses = manager.statuses()
    assert statuses[held]["status"] == "crash_loop"
    assert statuses[off]["status"] == "disabled"


# --- gates, dispatch, statuses ------------------------------------------------------


async def test_host_lock_and_platform_gate(manager, tmp_path, monkeypatch):
    from app.config import settings

    instance_id = await create_instance(tmp_path / "plugins", "echo")
    monkeypatch.setattr(settings, "disable_plugins", True)
    with pytest.raises(PluginHostError) as exc:
        await manager.start(instance_id)
    assert exc.value.code == "plugins_disabled"
    assert manager.disabled_source() == "env"

    monkeypatch.setattr(settings, "disable_plugins", False)
    monkeypatch.setattr(host_module, "platform_supported", lambda: False)
    assert manager.disabled_source() == "platform"
    fields = lifecycle.health_fields()
    assert fields["plugins_disabled_source"] == "platform"
    assert fields["plugins_isolation"] is None


async def test_isolation_setup_failure_fails_closed(monkeypatch):
    # A selected backend whose file model could not be applied must start nothing.
    manager = lifecycle.plugin_manager
    monkeypatch.setattr(manager, "_isolation_failed", False)
    loaded: list[bool] = []

    async def load_from_db() -> None:
        loaded.append(True)

    def startup(_data_dir):
        raise PermissionError("scandir refused")

    monkeypatch.setattr(manager, "load_from_db", load_from_db)
    monkeypatch.setattr(lifecycle.isolation, "startup", startup)
    await lifecycle.start()
    assert loaded == []
    assert manager.disabled_source() == "isolation"
    fields = lifecycle.health_fields()
    assert fields["plugins_disabled_source"] == "isolation"
    assert fields["plugins_isolation"] is None
    with pytest.raises(PluginHostError) as exc:
        await manager.start("any-instance")
    assert exc.value.code == "plugins_disabled"


async def test_events_reach_only_subscribed_hooks(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "echo", hooks=("message",))
    instance = await running(manager, instance_id)

    manager.dispatch("raw_packet", {"n": 0})
    manager.dispatch("message", msg(1))
    await wait_until(lambda: any(line == "got 1" for line in instance.log.lines()))
    assert instance.log.lines() == ["got 1"]


async def test_message_settle_delays_delivery(test_db, tmp_path, statuses):
    mgr = PluginManager(
        replace(FAST, message_settle=0.4),
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    try:
        instance_id = await create_instance(tmp_path / "plugins", "echo")
        instance = await running(mgr, instance_id)
        mgr.dispatch("message", msg(5))
        await asyncio.sleep(0.2)
        assert "got 5" not in instance.log.lines()
        await wait_until(lambda: "got 5" in instance.log.lines())
    finally:
        await mgr.stop_all(5.0)


async def test_fanout_broadcasts_reach_the_plugin_host(monkeypatch):
    from app.fanout.manager import FanoutManager

    seen: list[str] = []
    monkeypatch.setattr(
        host_module.plugin_manager, "dispatch", lambda hook, data: seen.append(hook)
    )
    fanout = FanoutManager()
    await fanout.broadcast_message({"type": "PRIV", "conversation_key": "ab"})
    await fanout.broadcast_raw({})
    await fanout.broadcast_contact({})
    await fanout.broadcast_telemetry({})
    await fanout.broadcast_health_fanout({})
    assert seen == ["message", "raw_packet", "contact", "telemetry", "health"]


async def test_test_events_capture_sends(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "sender")
    await running(manager, instance_id)
    outcome = await manager.dispatch_test(instance_id, "message", dm_params("ping"))
    assert outcome == {
        "result": "sent",
        "sends": [{"to": "reply", "text": "pong"}],
        "truncated": False,
    }


async def test_test_event_sends_without_event_id_are_captured(manager, tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "test.sdk-sender", "sender")
    (root / "test.sdk-sender" / "plugin.py").write_text(
        "import meshloom_plugin\n\n"
        "async def on_message(params):\n"
        "    await meshloom_plugin.host().call('messages.send', {'to': 'reply', 'text': 'hi'})\n",
        encoding="utf-8",
    )
    row = await PluginRepository.create(
        plugin_id="test.sdk-sender",
        label="sdk sender",
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": ["message"], "permissions": BOT_PERMISSIONS},
        granted_permissions=BOT_PERMISSIONS,
        enabled=True,
    )
    await running(manager, row["instance_id"])
    outcome = await manager.dispatch_test(row["instance_id"], "message", dm_params("ping"))
    assert outcome["sends"] == [{"to": "reply", "text": "hi"}]


async def test_sdk_replies_of_a_test_event_are_captured_beside_a_busy_event(manager, tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "test.sdk-reply", "echo")
    (root / "test.sdk-reply" / "manifest.json").write_text(
        '{"entry": "plugin.py:Bot"}', encoding="utf-8"
    )
    (root / "test.sdk-reply" / "plugin.py").write_text(
        "import asyncio\n"
        "from meshloom_plugin import Message, Plugin\n\n"
        "class Bot(Plugin):\n"
        "    async def on_message(self, msg: Message) -> None:\n"
        "        if msg.text == 'wait':\n"
        "            await asyncio.sleep(30)\n"
        "        await self.reply(msg, 'pong')\n",
        encoding="utf-8",
    )
    row = await PluginRepository.create(
        plugin_id="test.sdk-reply",
        label="sdk reply",
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py:Bot", "hooks": ["message"], "permissions": BOT_PERMISSIONS},
        granted_permissions=BOT_PERMISSIONS,
        enabled=True,
    )
    instance = await running(manager, row["instance_id"])
    manager.dispatch("message", msg("wait"))  # a real event stays in flight
    await wait_until(lambda: len(instance._in_flight) == 1)
    outcome = await manager.dispatch_test(row["instance_id"], "message", dm_params("ping"))
    assert outcome["sends"] == [{"to": {"contact_key": "ab" * 32}, "text": "pong"}]


async def test_unknown_host_method_is_refused_to_the_child(manager, tmp_path):
    root = tmp_path / "plugins"
    write_plugin(root, "test.unknown", "echo")
    (root / "test.unknown" / "plugin.py").write_text(
        "import meshloom_plugin\n"
        "from meshloom_plugin.errors import HostError\n\n"
        "async def on_message(params):\n"
        "    try:\n"
        "        await meshloom_plugin.host().call('no.such.method', {})\n"
        "    except HostError as exc:\n"
        "        return exc.error['code']\n",
        encoding="utf-8",
    )
    row = await PluginRepository.create(
        plugin_id="test.unknown",
        label="unknown",
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": ["message"], "permissions": BOT_PERMISSIONS},
        granted_permissions=BOT_PERMISSIONS,
        enabled=True,
    )
    instance = await running(manager, row["instance_id"])
    outcome = await manager.dispatch_test(row["instance_id"], "message", dm_params("x"))
    assert outcome["result"] == -32601
    assert instance.status == "running"


async def test_a_child_cannot_flood_the_host_with_requests(manager, tmp_path):
    async def slow(_instance, _params):
        await asyncio.sleep(0.3)
        return 1

    manager.host_methods["test.slow"] = slow
    instance_id = await create_instance(tmp_path / "plugins", "flood")
    await running(manager, instance_id)
    outcome = await manager.dispatch_test(instance_id, "message", {}, timeout=10)
    assert outcome["result"] == [-32001] * 8 + [1] * 32


async def test_stop_during_backoff_is_immediate(test_db, tmp_path, statuses):
    mgr = PluginManager(
        replace(FAST, backoff_initial=30.0, backoff_max=30.0),
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    instance_id = await create_instance(tmp_path / "plugins", "crash_start")
    await mgr.start(instance_id)
    instance = mgr.get(instance_id)
    assert instance is not None
    await wait_until(lambda: instance.status == "error")
    started = time.monotonic()
    await mgr.stop(instance_id)
    assert time.monotonic() - started < 1.0
    assert not instance.active


async def test_status_changes_are_broadcast_stored_and_scrubbed(
    manager, tmp_path, statuses, caplog
):
    caplog.set_level(logging.WARNING, logger="app.plugins.host")
    secret = "tok-9f8e7d6c5b4a"
    root = tmp_path / "plugins"
    write_plugin(root, "test.leak", "echo")
    (root / "test.leak" / "plugin.py").write_text(
        "def on_init(params):\n    pass\n\n"
        "def on_message(params):\n"
        "    raise ValueError('bad key ' + params['message_text'])\n",
        encoding="utf-8",
    )
    row = await PluginRepository.create(
        plugin_id="test.leak",
        label="leak",
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": ["message"], "permissions": BOT_PERMISSIONS},
        granted_permissions=BOT_PERMISSIONS,
        config_schema={
            "type": "object",
            "properties": {"api_key": {"type": "string", "writeOnly": True}},
        },
        config={"api_key": secret},
        enabled=True,
    )
    instance = await running(manager, row["instance_id"])
    manager.dispatch("message", msg(secret))
    await wait_until(lambda: instance.last_error is not None)

    # The child's text is never in last_error: only a code and the exception type.
    assert instance.last_error == "handler_error: event.message raised ValueError"
    assert [s["status"] for s in statuses][:2] == ["starting", "running"]
    assert all(secret not in str(s) for s in statuses)
    stored = await PluginRepository.get(row["instance_id"])
    assert stored is not None and stored["status"] == "running"
    assert stored["last_error"] == instance.last_error
    # The detail reaches the server log, scrubbed of the instance's secrets.
    logged = [r.getMessage() for r in caplog.records if "raised ValueError" in r.getMessage()]
    assert logged and "bad key ****" in logged[0]
    assert all(secret not in r.getMessage() for r in caplog.records)

    fields = lifecycle.health_fields()
    assert fields["plugins_isolation"] == isolation.current().level
    assert fields["plugins_disabled_source"] is None


def test_plugin_status_reaches_websocket_and_health(monkeypatch):
    import app.websocket as websocket

    events: list[tuple[str, dict, bool]] = []
    health: list[bool] = []
    monkeypatch.setattr(
        websocket,
        "broadcast_event",
        lambda event, data, realtime=True: events.append((event, data, realtime)),
    )
    monkeypatch.setattr(websocket, "broadcast_health", lambda *args: health.append(True))
    host_module._broadcast_status({"instance_id": "x", "status": "error", "last_error": "boom"})
    assert events == [
        ("plugin_status", {"instance_id": "x", "status": "error", "last_error": "boom"}, False)
    ]
    assert health == [True]


async def test_local_plugins_run_from_their_source(manager, tmp_path):
    row = await PluginRepository.create(
        plugin_id="local.echo",
        label="local echo",
        kind="local",
        version="0.0.1",
        manifest={"entry": "bot.py", "hooks": ["message"], "permissions": BOT_PERMISSIONS},
        granted_permissions=BOT_PERMISSIONS,
        source="def on_message(params):\n    return 'local'\n",
        enabled=True,
    )
    await running(manager, row["instance_id"])
    outcome = await manager.dispatch_test(row["instance_id"], "message", {})
    assert outcome["result"] == "local"


async def test_message_event_params_follow_the_bot_fields(test_db):
    from app.channel_constants import PUBLIC_CHANNEL_KEY
    from app.plugins.events import message_event_params

    channel = await message_event_params(
        {
            "type": "CHAN",
            "conversation_key": PUBLIC_CHANNEL_KEY,
            "channel_name": "Public",
            "sender_name": "Ann",
            "text": "Ann: hello",
            "paths": [{"path": "aabb", "path_len": 2}],
            "transport_code": 7,
            "received_at": 100,
        }
    )
    assert channel["message_text"] == "hello"
    assert channel["is_public_channel"] is True
    assert channel["is_dm"] is False and channel["sender_key"] is None
    assert (channel["path"], channel["path_bytes_per_hop"], channel["scoped"]) == ("aabb", 1, True)
    assert channel["received_at"] == 100

    dm = await message_event_params(msg("hi Ann: x"))
    assert dm["is_dm"] is True and dm["message_text"] == "hi Ann: x"
    assert dm["sender_key"] == "ab" * 32 and dm["channel_key"] is None
    assert dm["is_public_channel"] is False


# --- stderr ring ---------------------------------------------------------------------


def test_log_ring_truncates_lines_and_bounds_bytes():
    ring = LogRing(max_bytes=100, line_bytes=10)
    ring.feed(b"short\n" + b"x" * 25 + b"\npart")
    ring.feed(b"ial\n")
    assert ring.lines() == ["short", "xxxxxxxxxx [truncated]", "partial"]
    for n in range(50):
        ring.feed(f"line {n}\n".encode())
    assert sum(len(line) for line in ring.lines()) <= 100
    assert ring.lines()[-1] == "line 49"


def test_log_ring_keeps_the_runner_error():
    ring = LogRing(1024, 2048)
    ring.feed(b'{"runner_error": "entry_invalid", "message": "nope"}\n')
    assert ring.runner_error == {"code": "entry_invalid", "message": "nope"}


async def test_health_carries_the_plugin_fields(test_db, monkeypatch):
    import app.repository.plugins as plugins_repo
    from app.routers.health import HealthResponse, build_health_data

    # The repository's row cache is module-wide: other tests of this worker fill it.
    monkeypatch.setattr(plugins_repo, "_instances_cache", {})
    data = await build_health_data(False, None)
    health = HealthResponse(**data)
    assert health.plugins_isolation == isolation.current().level
    assert health.plugins_disabled_source is None
    assert health.plugin_statuses == {}


def test_the_lifespan_stops_plugins_before_the_fanout_modules():
    import inspect

    from app import main

    source = inspect.getsource(main._shutdown)
    assert source.index('("plugins", plugin_lifecycle.stop)') < source.index('("fanout modules"')
    assert "await plugin_lifecycle.start()" in inspect.getsource(main.lifespan)


# --- children that do not read stdin, bounded captures, sanitized errors ---------


def dm_params(text: object) -> dict[str, Any]:
    """`event.message` params of a direct message (what a test event carries)."""
    key = "ab" * 32
    return {"is_dm": True, "sender_key": key, "conversation_key": key, "message_text": str(text)}


def big_msg(n: int) -> dict[str, Any]:
    """A message large enough that a few of them fill a pipe the child never reads."""
    return msg(f"{n}" + "x" * 90_000)


async def test_a_child_that_never_reads_stdin_is_stopped_and_reaped(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "deaf")
    instance = await running(manager, instance_id)
    pid = instance.pid
    assert pid is not None
    for n in range(8):
        manager.dispatch("message", big_msg(n))
    await asyncio.sleep(0.3)

    started = time.monotonic()
    await manager.stop(instance_id)
    assert time.monotonic() - started < FAST.stop_timeout
    assert pid_gone(pid)
    assert not instance.active
    assert manager.get(instance_id) is None


async def test_stop_all_reaps_a_child_that_never_reads_stdin(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "deaf")
    instance = await running(manager, instance_id)
    pid = instance.pid
    assert pid is not None
    for n in range(8):
        manager.dispatch("message", big_msg(n))
    await asyncio.sleep(0.3)

    started = time.monotonic()
    await manager.stop_all(3.0)
    assert time.monotonic() - started < 3.5
    assert pid_gone(pid)
    assert not instance.active


async def test_a_full_stdin_uses_up_the_event_budget(test_db, tmp_path, statuses):
    mgr = PluginManager(
        replace(FAST, event_timeout=0.6, cancel_grace=0.3),
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    try:
        instance_id = await create_instance(tmp_path / "plugins", "deaf")
        instance = await running(mgr, instance_id)
        pid = instance.pid
        for n in range(8):
            mgr.dispatch("message", big_msg(n))
        await wait_until(lambda: instance.restarts >= 1, timeout=10, instance=instance)
        assert pid is not None and pid_gone(pid)
        assert instance.last_error is not None
        assert instance.last_error.split(":")[0] in ("stdin_blocked", "event_cancel_ignored")
    finally:
        await mgr.stop_all(5.0)


async def test_test_event_captures_are_bounded(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "spammer")
    await running(manager, instance_id)
    outcome = await manager.dispatch_test(instance_id, "message", dm_params(0), timeout=10)
    assert outcome["truncated"] is True
    assert len(outcome["sends"]) == outcome["result"] <= host_module.MAX_TEST_SENDS
    size = sum(len(json.dumps(s).encode()) for s in outcome["sends"])
    assert size <= host_module.MAX_TEST_SEND_BYTES


def test_sanitize_error_drops_paths_controls_and_length():
    text = "start_failed: open /srv/meshloom/data/x.db failed\x1b[31m " + "y" * 500
    clean = host_module.sanitize_error(text)
    assert clean is not None
    assert "/srv" not in clean and "\x1b" not in clean
    assert "<path>" in clean
    assert len(clean) <= host_module.LAST_ERROR_MAX_CHARS


async def test_a_start_failure_names_no_path(manager, tmp_path, monkeypatch, caplog):
    instance_id = await create_instance(tmp_path / "plugins", "echo")

    def broken(_work_dir):
        raise OSError(f"cannot open {tmp_path}/secret/place")

    caplog.set_level(logging.WARNING, logger="app.plugins.host")
    monkeypatch.setattr(host_module.PluginInstance, "_params_line", lambda self, w: broken(w))
    await manager.start(instance_id)
    instance = manager.get(instance_id)
    assert instance is not None
    await wait_until(lambda: instance.last_error is not None, instance=instance)
    assert instance.last_error == "start_failed: cannot start the plugin process (OSError)"
    assert any(str(tmp_path) in r.getMessage() for r in caplog.records)


def test_health_hides_plugin_errors_below_admin():
    guard = {"total": 2, "by_scope": {"instance": 2}, "last": {"scope": "instance", "at": 1}}
    data = {
        "plugin_statuses": {
            "a": {"instance_id": "a", "status": "error", "last_error": "x", "send_guard": guard}
        }
    }
    hidden = lifecycle.without_plugin_errors(data)["plugin_statuses"]["a"]
    assert hidden["last_error"] is None
    assert hidden["send_guard"] == {}
    assert data["plugin_statuses"]["a"]["send_guard"] == guard
    assert data["plugin_statuses"]["a"]["last_error"] == "x"
    from app.auth.permissions import ROLE_PERMISSIONS
    from app.auth.ws_events import WsEvent

    permission = WsEvent.PLUGIN_STATUS.permission
    assert permission == lifecycle.PLUGIN_ERROR_PERMISSION
    assert permission not in ROLE_PERMISSIONS["kiosk"]
    assert permission not in ROLE_PERMISSIONS["viewer"]
    assert permission not in ROLE_PERMISSIONS["member"]
    assert permission in ROLE_PERMISSIONS["admin"]


async def test_websocket_health_hides_plugin_errors_below_admin():
    from types import SimpleNamespace

    from app.websocket import WebSocketManager

    manager = WebSocketManager()
    sent: dict[str, list[str]] = {}

    class FakeClient:
        def __init__(self, role):
            self.principal = SimpleNamespace(role=role, scopes=frozenset())

    clients = {
        "viewer": FakeClient("viewer"),
        "member": FakeClient("member"),
        "admin": FakeClient("admin"),
    }
    manager.active_connections = clients  # type: ignore[assignment]
    manager._enqueue = lambda client, message, event_type=None: sent.setdefault(  # type: ignore[method-assign]
        client.principal.role, []
    ).append(message)
    payload = {
        "plugin_statuses": {"a": {"instance_id": "a", "status": "error", "last_error": "boom"}}
    }
    manager.broadcast_nowait("health", payload)
    assert "boom" in sent["admin"][0]
    assert "boom" not in sent["member"][0]
    assert "boom" not in sent["viewer"][0]
    manager.broadcast_nowait("plugin_status", {"instance_id": "a", "last_error": "boom"})
    assert len(sent["viewer"]) == 1 and len(sent["member"]) == 1 and len(sent["admin"]) == 2
