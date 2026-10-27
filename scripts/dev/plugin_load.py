"""Load test of the plugin runtime (MSHL-212; docs/dev/plugins-security.md, "Load test").

Not a test and not in the gate: run it by hand on a Linux machine, from the
repository root, with the app's environment (see CONTRIBUTING.md):

    uv run python scripts/dev/plugin_load.py --levels bwrap,setuid

It drives the real supervisor (`PluginManager` of `app/plugins/host.py` with the
default `Budgets`) on a throwaway SQLite database, with the isolation level
forced to each one asked for, one after the other: `bwrap` where its self-test
passes, `setuid` as root with the `meshloom-plugin` account, `none` always.

Steady phase: `--instances` instances of a plugin that reads every direct
message, counts it and, every `--storage-every` messages, writes the count with
`storage.set` (an RPC round trip and a database write). The script dispatches
`--rate` messages per second through `PluginManager.dispatch("message", ...)`,
the fanout entry the radio uses (2 s settle included), so every instance gets
`--rate` events per second. Every `--sample` seconds it records, per instance,
the inbox depth, the events in flight, the drop and restart counters and the
memory of the child's process tree (RSS and PSS from `/proc`, bwrap's own
processes included), plus the server's RSS and the latency from enqueue to
answer (p50, p95, p99 and max over the window).

Overload phase (`--overload-seconds`, 0 to skip): one instance whose handler
sleeps `--overload-handler` seconds, at the same rate. It finishes at most
`policy.MAX_CONCURRENT_HANDLERS` events per handler time, so its inbox fills to
`Budgets.inbox_size` and the oldest events are dropped and counted.

Both phases check the accounting (every accepted event is answered, failed,
dropped, queued or in flight), the inbox bound and that nothing restarted.
Prints one JSON document on stdout, progress on stderr; removes its temporary
directories. Exit status 1 when a level is unavailable or a check fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import shutil
import statistics
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

# The app reads its database path at import: point it at a throwaway file first.
_TMP = Path(tempfile.mkdtemp(prefix="meshloom-load-"))
(_TMP / "data").mkdir()
os.environ["MESHCORE_DATABASE_PATH"] = str(_TMP / "data" / "meshcore.db")

from app.database import db  # noqa: E402
from app.plugins.host import Budgets, PluginInstance, PluginManager  # noqa: E402
from app.plugins.isolation import selection  # noqa: E402
from app.repository.plugins import PluginRepository  # noqa: E402
from meshloom_plugin import policy  # noqa: E402
from tests.plugin_isolation_support import available_backends, shared_tmp_dir  # noqa: E402

PLUGIN = """
import asyncio

import meshloom_plugin

COUNT = 0


async def on_message(params):
    global COUNT
    COUNT += 1
    if SLEEP:
        await asyncio.sleep(SLEEP)
    if STORAGE_EVERY and COUNT % STORAGE_EVERY == 0:
        await meshloom_plugin.host().call("storage.set", {"key": "count", "value": COUNT})
    return len(params.get("message_text") or "")
"""

PERMISSIONS: dict[str, Any] = {
    "messages.read": {"channels": "all", "direct": True, "outgoing": True},
    "storage": {},
}


# --- /proc -------------------------------------------------------------------------


def _kib(path: str, key: str) -> int:
    try:
        for line in Path(path).read_text().splitlines():
            if line.startswith(key + ":"):
                return int(line.split()[1])
    except OSError:
        pass
    return 0


def _tree(pid: int) -> list[int]:
    pids = [pid]
    try:
        for task in Path(f"/proc/{pid}/task").iterdir():
            for child in (task / "children").read_text().split():
                pids += _tree(int(child))
    except OSError:
        pass
    return pids


def _tree_memory(pid: int | None) -> dict[str, int]:
    if pid is None:
        return {"rss_kib": 0, "pss_kib": 0, "processes": 0}
    pids = _tree(pid)
    return {
        "rss_kib": sum(_kib(f"/proc/{p}/status", "VmRSS") for p in pids),
        "pss_kib": sum(_kib(f"/proc/{p}/smaps_rollup", "Pss") for p in pids),
        "processes": len(pids),
    }


# --- instrumentation ---------------------------------------------------------------


class Counters:
    """What the script saw of each instance, beside the host's own counters."""

    def __init__(self) -> None:
        self.accepted: dict[str, int] = {}
        self.answered: dict[str, int] = {}
        self.failed: dict[str, int] = {}
        self.latencies: list[float] = []
        # The deepest inbox seen right after an enqueue (samples only see snapshots).
        self.queued_peak = 0

    def install(self) -> None:
        counters = self
        enqueue = PluginInstance.enqueue
        deliver = PluginInstance._deliver
        finish = PluginInstance._finish
        drop = PluginInstance._drop

        def _enqueue(self: PluginInstance, event: Any) -> bool:
            event.load_t0 = time.monotonic()
            ok = enqueue(self, event)
            if ok:
                key = self.instance_id
                counters.accepted[key] = counters.accepted.get(key, 0) + 1
                counters.queued_peak = max(counters.queued_peak, self.queued)
            return ok

        async def _deliver(self: PluginInstance, process: Any, event: Any) -> None:
            await deliver(self, process, event)
            if getattr(event, "load_dropped", False):
                return
            key = self.instance_id
            if getattr(event, "load_error", False):
                counters.failed[key] = counters.failed.get(key, 0) + 1
            else:
                counters.answered[key] = counters.answered.get(key, 0) + 1
                counters.latencies.append(time.monotonic() - event.load_t0)

        def _finish(self: PluginInstance, event: Any, *, value: Any = None, error: Any = None):
            if error is not None:
                event.load_error = True
            return finish(self, event, value=value, error=error)

        def _drop(self: PluginInstance, events: list[Any], why: str) -> None:
            for event in events:
                event.load_dropped = True
            return drop(self, events, why)

        PluginInstance.enqueue = _enqueue  # type: ignore[method-assign]
        PluginInstance._deliver = _deliver  # type: ignore[method-assign]
        PluginInstance._finish = _finish  # type: ignore[method-assign]
        PluginInstance._drop = _drop  # type: ignore[method-assign]

    def window(self) -> dict[str, float]:
        values, self.latencies = sorted(self.latencies), []
        if not values:
            return {"answers": 0}

        def pct(p: float) -> float:
            return round(values[min(len(values) - 1, int(p * len(values)))] * 1000, 1)

        return {
            "answers": len(values),
            "p50_ms": pct(0.50),
            "p95_ms": pct(0.95),
            "p99_ms": pct(0.99),
            "max_ms": round(values[-1] * 1000, 1),
        }


def _message(n: int) -> dict[str, Any]:
    return {
        "type": "PRIV",
        "conversation_key": f"{n % 251:02x}" * 32,
        "sender_name": f"load-{n % 251}",
        "text": f"load message {n} " + "x" * 80,
    }


def _write_plugin(root: Path, plugin_id: str, sleep: float, storage_every: int) -> None:
    directory = root / plugin_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text('{"entry": "plugin.py"}', encoding="utf-8")
    source = f"SLEEP = {sleep!r}\nSTORAGE_EVERY = {storage_every!r}\n" + textwrap.dedent(PLUGIN)
    (directory / "plugin.py").write_text(source, encoding="utf-8")
    os.chmod(directory, 0o755)
    for path in directory.iterdir():
        os.chmod(path, 0o644)


async def _create(plugin_id: str, label: str) -> str:
    row = await PluginRepository.create(
        plugin_id=plugin_id,
        label=label,
        kind="store",
        version="1.0.0",
        manifest={"entry": "plugin.py", "hooks": ["message"], "permissions": PERMISSIONS},
        granted_permissions=PERMISSIONS,
        enabled=True,
    )
    return row["instance_id"]


async def _dispatcher(manager: PluginManager, rate: float, stop: asyncio.Event) -> int:
    """`rate` messages per second on an absolute schedule; returns how many were sent."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    n = 0
    while not stop.is_set():
        manager.dispatch("message", _message(n))
        n += 1
        delay = start + n / rate - loop.time()
        if delay > 0:
            try:
                await asyncio.wait_for(stop.wait(), delay)
            except TimeoutError:
                pass
    return n


def _sample(manager: PluginManager, ids: list[str], counters: Counters, t: float) -> dict:
    instances = []
    for instance_id in ids:
        inst = manager.get(instance_id)
        if inst is None:
            continue
        instances.append(
            {
                "status": inst.status,
                "queued": inst.queued,
                "in_flight": len(inst._in_flight),
                "dropped": inst.dropped_events,
                "restarts": inst.restarts,
                **_tree_memory(inst.pid),
            }
        )
    return {
        "t": round(t, 1),
        "server_rss_kib": _kib("/proc/self/status", "VmRSS"),
        "children_pss_kib": sum(i["pss_kib"] for i in instances),
        "children_rss_kib": sum(i["rss_kib"] for i in instances),
        "queued_max": max((i["queued"] for i in instances), default=0),
        "in_flight_max": max((i["in_flight"] for i in instances), default=0),
        "dropped": sum(i["dropped"] for i in instances),
        "restarts": sum(i["restarts"] for i in instances),
        "not_running": sum(1 for i in instances if i["status"] != "running"),
        "latency": counters.window(),
        "instances": instances,
    }


def _slope_kib_per_min(samples: list[dict], key: str) -> float:
    if len(samples) < 2:
        return 0.0
    xs = [s["t"] / 60 for s in samples]
    ys = [float(s[key]) for s in samples]
    return round(statistics.linear_regression(xs, ys).slope, 1)


async def _wait_running(manager: PluginManager, ids: list[str], timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while True:
        states = [getattr(manager.get(i), "status", None) for i in ids]
        if all(s == "running" for s in states):
            return
        if time.monotonic() > deadline:
            raise RuntimeError(f"instances not running: {states}")
        await asyncio.sleep(0.2)


async def _phase(
    manager: PluginManager,
    ids: list[str],
    counters: Counters,
    *,
    rate: float,
    duration: float,
    sample_every: float,
    label: str,
) -> tuple[list[dict], int]:
    for instance_id in ids:
        await manager.start(instance_id)
    await _wait_running(manager, ids)
    counters.latencies.clear()
    counters.queued_peak = 0
    stop = asyncio.Event()
    dispatcher = asyncio.create_task(_dispatcher(manager, rate, stop))
    samples: list[dict] = []
    t0 = time.monotonic()
    try:
        while (elapsed := time.monotonic() - t0) < duration:
            await asyncio.sleep(min(sample_every, duration - elapsed))
            sample = _sample(manager, ids, counters, time.monotonic() - t0)
            samples.append(sample)
            print(
                f"[{label}] t={sample['t']:>6}s queued_max={sample['queued_max']} "
                f"in_flight_max={sample['in_flight_max']} dropped={sample['dropped']} "
                f"restarts={sample['restarts']} children_pss={sample['children_pss_kib']}KiB "
                f"server_rss={sample['server_rss_kib']}KiB latency={sample['latency']}",
                file=sys.stderr,
                flush=True,
            )
    finally:
        stop.set()
        dispatched = await dispatcher
    # Let the settle and the events in flight finish before reading the counters.
    await asyncio.sleep(manager.budgets.message_settle + 3)
    return samples, dispatched


def _accounting(manager: PluginManager, ids: list[str], counters: Counters) -> list[dict]:
    rows = []
    for instance_id in ids:
        inst = manager.get(instance_id)
        assert inst is not None
        accepted = counters.accepted.get(instance_id, 0)
        answered = counters.answered.get(instance_id, 0)
        failed = counters.failed.get(instance_id, 0)
        rest = inst.queued + len(inst._in_flight)
        rows.append(
            {
                "accepted": accepted,
                "answered": answered,
                "failed": failed,
                "dropped": inst.dropped_events,
                "queued_or_in_flight": rest,
                "balanced": accepted == answered + failed + inst.dropped_events + rest,
                "restarts": inst.restarts,
                "status": inst.status,
            }
        )
    return rows


def _summary(sample: dict) -> dict:
    return {k: v for k, v in sample.items() if k != "instances"}


async def run_level(level: str, args: argparse.Namespace, counters: Counters) -> dict[str, Any]:
    backend = available_backends()[level]
    selection._current = selection.Selection(backend)
    backend.prepare_data_dir(_TMP / "data")
    backend.prepare_uid_leases()
    root = shared_tmp_dir("meshloom-load-plugins-")
    manager = PluginManager(Budgets(), plugins_root=root)
    result: dict[str, Any] = {"level": level}
    checks: list[str] = []
    try:
        _write_plugin(root, "load.steady", 0.0, args.storage_every)
        ids = [await _create("load.steady", f"steady-{i}") for i in range(args.instances)]
        samples, dispatched = await _phase(
            manager,
            ids,
            counters,
            rate=args.rate,
            duration=args.duration,
            sample_every=args.sample,
            label=f"{level} steady",
        )
        accounting = _accounting(manager, ids, counters)
        warm = [s for s in samples if s["t"] >= args.warmup] or samples
        windows = [s["latency"] for s in samples if s["latency"].get("answers")]
        steady = {
            "instances": args.instances,
            "rate_per_second": args.rate,
            "duration_seconds": args.duration,
            "dispatched": dispatched,
            "accepted": sum(a["accepted"] for a in accounting),
            "answered": sum(a["answered"] for a in accounting),
            "failed": sum(a["failed"] for a in accounting),
            "dropped": sum(a["dropped"] for a in accounting),
            "restarts": sum(a["restarts"] for a in accounting),
            "queued_peak": counters.queued_peak,
            "in_flight_max_sampled": max(s["in_flight_max"] for s in samples),
            "after_warmup_first": _summary(warm[0]),
            "last": _summary(warm[-1]),
            "children_pss_slope_kib_per_min": _slope_kib_per_min(warm, "children_pss_kib"),
            "server_rss_slope_kib_per_min": _slope_kib_per_min(warm, "server_rss_kib"),
            "latency_p50_ms_median": statistics.median(w["p50_ms"] for w in windows),
            "latency_p99_ms_worst_window": max(w["p99_ms"] for w in windows),
            "latency_max_ms": max(w["max_ms"] for w in windows),
            "accounting_balanced": all(a["balanced"] for a in accounting),
            "samples": samples if args.keep_samples else None,
        }
        result["steady"] = steady
        if not steady["accounting_balanced"]:
            checks.append("steady: accounting does not balance")
        if steady["restarts"]:
            checks.append("steady: an instance restarted")
        if steady["queued_peak"] > manager.budgets.inbox_size:
            checks.append("steady: inbox past its bound")
        for instance_id in ids:
            await manager.remove(instance_id, purge=True)

        if args.overload_seconds > 0:
            _write_plugin(root, "load.slow", args.overload_handler, 0)
            slow = [await _create("load.slow", "slow")]
            o_samples, o_dispatched = await _phase(
                manager,
                slow,
                counters,
                rate=args.rate,
                duration=args.overload_seconds,
                sample_every=args.sample,
                label=f"{level} overload",
            )
            (row,) = _accounting(manager, slow, counters)
            overload = {
                "handler_seconds": args.overload_handler,
                "capacity_per_second": policy.MAX_CONCURRENT_HANDLERS / args.overload_handler,
                "rate_per_second": args.rate,
                "duration_seconds": args.overload_seconds,
                "dispatched": o_dispatched,
                "queued_peak": counters.queued_peak,
                "in_flight_max_sampled": max(s["in_flight_max"] for s in o_samples),
                "inbox_size": manager.budgets.inbox_size,
                **row,
            }
            result["overload"] = overload
            if not row["balanced"]:
                checks.append("overload: accounting does not balance")
            if overload["queued_peak"] > manager.budgets.inbox_size:
                checks.append("overload: inbox past its bound")
            if overload["capacity_per_second"] < args.rate and row["dropped"] == 0:
                checks.append("overload: nothing dropped although the rate exceeds capacity")
            if row["restarts"]:
                checks.append("overload: the instance restarted")
            await manager.remove(slow[0], purge=True)
    finally:
        await manager.stop_all(10.0)
        shutil.rmtree(root, ignore_errors=True)
    result["checks_failed"] = checks
    return result


async def main_async(args: argparse.Namespace) -> int:
    await db.connect()
    counters = Counters()
    counters.install()
    try:
        offered = available_backends()
        levels = [level for level in args.levels.split(",") if level]
        missing = [level for level in levels if level not in offered]
        report: dict[str, Any] = {
            "machine": platform.machine(),
            "kernel": platform.release(),
            "python": platform.python_version(),
            "euid": os.geteuid(),
            "cpus": os.cpu_count(),
            "mem_total_kib": _kib("/proc/meminfo", "MemTotal"),
            "levels_unavailable": missing,
            "results": [],
        }
        for level in levels:
            if level in offered:
                report["results"].append(await run_level(level, args, counters))
        print(json.dumps(report, indent=2))
        failed = missing or any(r["checks_failed"] for r in report["results"])
        return 1 if failed else 0
    finally:
        await db.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser(description="Load test of the plugin runtime (MSHL-212).")
    parser.add_argument("--levels", default="bwrap,setuid", help="comma-separated")
    parser.add_argument("--instances", type=int, default=10)
    parser.add_argument("--rate", type=float, default=20.0, help="messages per second")
    parser.add_argument("--duration", type=float, default=600.0, help="steady phase, seconds")
    parser.add_argument("--sample", type=float, default=10.0, help="seconds between samples")
    parser.add_argument("--warmup", type=float, default=60.0, help="left out of the slopes")
    parser.add_argument("--storage-every", type=int, default=10)
    parser.add_argument("--overload-seconds", type=float, default=60.0)
    parser.add_argument("--overload-handler", type=float, default=1.0, help="seconds")
    parser.add_argument("--keep-samples", action="store_true", help="every sample in the JSON")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        print("Linux only: memory is read from /proc", file=sys.stderr)
        return 2
    try:
        return asyncio.run(main_async(args))
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
