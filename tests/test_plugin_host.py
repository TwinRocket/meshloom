"""The plugin host supervisor (`app/plugins/host.py`) against real child processes.

Every instance runs the real runner (`meshloom_plugin.runner`) on a fixture
plugin written here (echo, sleep, ignore-cancel, crash, cpu-burn...). Budgets
are shortened through `Budgets`; the CPU reader is injected where a controlled
value is enough, and the real `/proc` reader runs on Linux.
"""

import asyncio
import logging
import os
import signal
import sys
import textwrap
import time
from pathlib import Path
from typing import Any

import pytest

from app.plugins import host as host_module
from app.plugins import lifecycle
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

PLUGINS = {
    "echo": """
        import sys

        def on_message(params):
            print("got", params.get("n"), file=sys.stderr, flush=True)
            return {"echo": params.get("text")}
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
            print("got", params.get("n"), file=sys.stderr, flush=True)
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
    "sender": """
        import meshloom_plugin

        async def on_message(params):
            await meshloom_plugin.host().call(
                "messages.send", {"event_id": params["event_id"], "to": "reply", "text": "pong"}
            )
            return "sent"
    """,
}


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
        manifest={"entry": "plugin.py", "hooks": list(hooks)},
        enabled=True,
        **kwargs,
    )
    return row["instance_id"]


async def wait_until(predicate, timeout: float = 15.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError(f"timed out waiting for {what}")
        await asyncio.sleep(0.02)


def pid_gone(pid: int) -> bool:
    """True once `pid` is reaped: a zombie still answers signal 0."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


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
    await wait_until(lambda: instance.status == "running", what="running")
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

    manager.dispatch("message", {"n": 1})
    manager.dispatch("message", {"n": 2})
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
        manager.dispatch("message", {"n": n})
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

    await wait_until(lambda: instance.status == "crash_loop", timeout=30, what="crash_loop")
    assert instance.consecutive_failures == 5
    assert instance.restarts == 4
    assert "entry_load_failed" in (instance.last_error or "")
    assert not instance.active
    stored = await PluginRepository.get(instance_id)
    assert stored is not None and stored["status"] == "crash_loop"

    # Fixed plugin, manual restart: a new supervisor with its count at zero.
    write_plugin(root, "test.crash-start", "echo")
    await manager.restart(instance_id)
    restarted = manager.get(instance_id)
    assert restarted is not None and restarted is not instance
    await wait_until(lambda: restarted.status == "running")
    assert restarted.consecutive_failures == 0


async def test_crash_on_event_counts_and_restarts(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "crash_event")
    instance = await running(manager, instance_id)
    manager.dispatch("message", {})
    await wait_until(lambda: instance.restarts == 1 and instance.status == "running")
    assert "exited unexpectedly (code 7)" in (instance.last_error or "")
    assert instance.dropped_events == 1


async def test_a_child_killed_from_outside_restarts_with_backoff(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "echo")
    instance = await running(manager, instance_id)
    pid = instance.pid
    assert pid is not None

    os.kill(pid, signal.SIGKILL)
    await wait_until(lambda: instance.status == "error", what="error")
    assert instance.last_error == "killed by SIGKILL"
    await wait_until(lambda: instance.status == "running" and instance.pid != pid)
    assert instance.restarts == 1
    assert pid_gone(pid)


def test_backoff_doubles_up_to_the_cap():
    budgets = Budgets()
    assert [budgets.backoff(n) for n in range(1, 9)] == [1, 2, 4, 8, 16, 32, 60, 60]


# --- CPU --------------------------------------------------------------------------


async def test_rolling_cpu_budget_kills_the_child(test_db, tmp_path, statuses):
    ticks = iter(range(0, 10_000, 3))
    mgr = PluginManager(
        Budgets(**{**FAST.__dict__, "cpu_budget": 5.0, "cpu_sample_interval": 0.02}),
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
        Budgets(
            **{
                **FAST.__dict__,
                "event_timeout": 30.0,
                "cpu_budget": 0.5,
                "cpu_window": 5.0,
                "cpu_sample_interval": 0.1,
            }
        ),
        plugins_root=tmp_path / "plugins",
        mem_total=lambda: 8 * 1024**3,
    )
    try:
        instance_id = await create_instance(tmp_path / "plugins", "cpu_burn")
        instance = await running(mgr, instance_id)
        mgr.dispatch("message", {})
        await wait_until(lambda: "CPU budget exceeded" in (instance.last_error or ""))
    finally:
        await mgr.stop_all(5.0)


@pytest.mark.skipif(not LINUX, reason="/proc is Linux only")
def test_proc_reader_reads_this_process():
    value = read_proc_cpu_seconds(os.getpid())
    assert value is not None and value >= 0
    assert read_proc_cpu_seconds(2**22 + 12345) is None


async def test_rlimit_cpu_is_set_in_the_child(test_db, tmp_path, statuses):
    mgr = PluginManager(
        Budgets(**{**FAST.__dict__, "event_timeout": 60.0, "runner_limits": {"cpu_seconds": 1}}),
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    try:
        instance_id = await create_instance(tmp_path / "plugins", "cpu_burn")
        instance = await running(mgr, instance_id)
        mgr.dispatch("message", {})
        await wait_until(lambda: instance.status == "error", timeout=20)
        assert instance.last_error in (
            "CPU time limit reached (RLIMIT_CPU)",
            "killed by SIGKILL",
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
    manager.dispatch("message", {"n": 1})
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


async def test_events_reach_only_subscribed_hooks(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "echo", hooks=("message",))
    instance = await running(manager, instance_id)

    manager.dispatch("raw_packet", {"n": 0})
    manager.dispatch("message", {"n": 1})
    await wait_until(lambda: any(line == "got 1" for line in instance.log.lines()))
    assert "got 0" not in instance.log.lines()


async def test_message_settle_delays_delivery(test_db, tmp_path, statuses):
    mgr = PluginManager(
        Budgets(**{**FAST.__dict__, "message_settle": 0.4}),
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    try:
        instance_id = await create_instance(tmp_path / "plugins", "echo")
        instance = await running(mgr, instance_id)
        mgr.dispatch("message", {"n": 5})
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
    outcome = await manager.dispatch_test(instance_id, "message", {"text": "ping"})
    assert outcome == {"result": "sent", "sends": [{"to": "reply", "text": "pong"}]}


async def test_unknown_host_method_is_refused_to_the_child(manager, tmp_path):
    instance_id = await create_instance(tmp_path / "plugins", "sender")
    instance = await running(manager, instance_id)
    manager.dispatch("message", {})  # not a test event: no capability yet
    await wait_until(lambda: "messages.send is not available" in (instance.last_error or ""))
    assert instance.status == "running"


async def test_status_changes_are_broadcast_stored_and_scrubbed(manager, tmp_path, statuses):
    secret = "tok-9f8e7d6c5b4a"
    root = tmp_path / "plugins"
    write_plugin(root, "test.leak", "echo")
    (root / "test.leak" / "plugin.py").write_text(
        "def on_init(params):\n    pass\n\n"
        "def on_message(params):\n"
        "    raise ValueError('bad key ' + params['key'])\n",
        encoding="utf-8",
    )
    row = await PluginRepository.create(
        plugin_id="test.leak",
        label="leak",
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": ["message"]},
        config_schema={
            "type": "object",
            "properties": {"api_key": {"type": "string", "writeOnly": True}},
        },
        config={"api_key": secret},
        enabled=True,
    )
    instance = await running(manager, row["instance_id"])
    manager.dispatch("message", {"key": secret})
    await wait_until(lambda: "bad key" in (instance.last_error or ""))

    assert secret not in (instance.last_error or "")
    assert [s["status"] for s in statuses][:2] == ["starting", "running"]
    assert all(secret not in str(s) for s in statuses)
    stored = await PluginRepository.get(row["instance_id"])
    assert stored is not None and stored["status"] == "running"
    assert "bad key ****" in (stored["last_error"] or "")

    fields = lifecycle.health_fields()
    assert fields["plugins_isolation"] == "none"
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
        manifest={"entry": "bot.py", "hooks": ["message"]},
        source="def on_message(params):\n    return 'local'\n",
        enabled=True,
    )
    await running(manager, row["instance_id"])
    outcome = await manager.dispatch_test(row["instance_id"], "message", {})
    assert outcome["result"] == "local"


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
