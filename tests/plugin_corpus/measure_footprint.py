"""Measure what plugin children cost on this machine (docs/dev/plugins-security.md, "Measurements").

Not a test: run it by hand on the target (a Raspberry Pi 3, or any Linux box),
from the repository root, with the app's environment:

    uv run python tests/plugin_corpus/measure_footprint.py --instances 8 --duration 3600

For each isolation level available here (`none`, `bwrap`, `setuid` as root with
the `meshloom-plugin` account) it starts the children the way the supervisor
does (`backend.launch()`, the real runner), times the start until the first
answer, and reads the memory of each child's process tree (RSS and PSS from
`/proc`, bwrap's own processes included). It then keeps the children busy with
one event every `--interval` seconds for `--duration` seconds and reads the
memory again. Prints one JSON document; nothing is written anywhere else.
"""

import argparse
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.plugin_isolation_support import available_backends, shared_tmp_dir  # noqa: E402
from tests.test_plugin_runner import Runner  # noqa: E402

PLUGIN = """
import json


def on_message(params):
    return len(json.dumps(params))
"""


def _tree(pid: int) -> list[int]:
    pids = [pid]
    for task in Path(f"/proc/{pid}/task").iterdir():
        children = (task / "children").read_text().split()
        for child in children:
            pids += _tree(int(child))
    return pids


def _kib(path: str, key: str) -> int:
    for line in Path(path).read_text().splitlines():
        if line.startswith(key + ":"):
            return int(line.split()[1])
    return 0


def _memory(pid: int) -> dict[str, int]:
    rss = pss = 0
    for one in _tree(pid):
        rss += _kib(f"/proc/{one}/status", "VmRSS")
        pss += _kib(f"/proc/{one}/smaps_rollup", "Pss")
    return {"rss_kib": rss, "pss_kib": pss, "processes": len(_tree(pid))}


def _available_kib() -> int:
    return _kib("/proc/meminfo", "MemAvailable")


def measure(level: str, instances: int, duration: float, interval: float) -> dict[str, Any]:
    backend = available_backends()[level]
    root = shared_tmp_dir("meshloom-footprint-")
    work = Path(tempfile.mkdtemp(prefix="meshloom-footprint-work-")).resolve()
    plugin = root / "plugin"
    plugin.mkdir()
    (plugin / "manifest.json").write_text('{"entry": "plugin.py"}')
    (plugin / "plugin.py").write_text(PLUGIN)
    before = _available_kib()
    runners: list[Runner] = []
    starts: list[float] = []
    try:
        for _ in range(instances):
            t0 = time.monotonic()
            runner = Runner(plugin, work, backend=backend)
            assert runner.request("event.health", {}, timeout=60)["result"] is None
            starts.append(time.monotonic() - t0)
            runners.append(runner)
        at_start = [_memory(r.proc.pid) for r in runners]
        started_available = _available_kib()
        deadline = time.monotonic() + duration
        events = 0
        while time.monotonic() < deadline:
            for runner in runners:
                assert "result" in runner.event({"text": "x" * 100, "n": events})
            events += 1
            time.sleep(max(0.0, min(interval, deadline - time.monotonic())))
        at_end = [_memory(r.proc.pid) for r in runners]
        return {
            "level": level,
            "instances": instances,
            "start_seconds": {"min": min(starts), "max": max(starts), "first": starts[0]},
            "per_child_at_start": at_start[0],
            "per_child_at_end": at_end[0],
            "all_children_pss_kib_at_start": sum(m["pss_kib"] for m in at_start),
            "all_children_pss_kib_at_end": sum(m["pss_kib"] for m in at_end),
            "mem_available_drop_kib": before - started_available,
            "duration_seconds": duration,
            "event_rounds": events,
        }
    finally:
        for runner in runners:
            runner.close()
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--instances", type=int, default=8)
    parser.add_argument("--duration", type=float, default=3600.0)
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--levels", default="none,bwrap,setuid")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        print("Linux only: memory is read from /proc", file=sys.stderr)
        return 2
    levels = [lv for lv in args.levels.split(",") if lv in available_backends()]
    report = {
        "machine": platform.machine(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "cpus": os.cpu_count(),
        "mem_total_kib": _kib("/proc/meminfo", "MemTotal"),
        "results": [measure(lv, args.instances, args.duration, args.interval) for lv in levels],
    }
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
