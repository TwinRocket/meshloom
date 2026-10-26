"""Plugin author tooling: `python -m meshloom_plugin.dev check|run|pack <dir>` (MSHL-178, MSHL-208).

Runs from a source checkout, next to `app/`, in the server's interpreter. Unlike
the rest of this package it is not child code (the runner never imports it) and
it may import `app.plugins`; it must stay free of FastAPI and the database
(`tests/test_plugin_dev_check.py` asserts on `sys.modules`).

`check` runs the server's own validators, never a copy of them, in order:

1. package: the directory is zipped in memory and goes through
   `app.plugins.package.validate_package` (manifest, archive rules, config schema),
   so the messages are the server's;
2. static scan: `app.plugins.attention.scan_source` on the plugin's own `.py` files
   (everything outside `vendor/`); findings are warnings, `--strict` fails on them;
3. dry run: the real runner (`isolation: none`) with `plugin.init` and one synthetic
   `event.message` carrying `test: true`; the plugin's calls to the host are
   captured, nothing leaves the machine. Skipped when the package is invalid.

`run <dir> --replay fixture.json` runs the plugin against a fake host fed by a JSON
fixture (no radio): every RPC call and reply is printed, and permissions, quotas and
send guards are enforced by the server's own pure-Python core (`FakeHost`).
`pack <dir> [--out DIR]` runs `check`, then writes a reproducible `plugin.zip` and
`plugin.zip.sha256`.

Exit status: 0 passed, 1 failed, 2 usage error (a bad path included). `--json` prints one object.
"""

import argparse
import asyncio
import hashlib
import io
import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.channel_constants import PUBLIC_CHANNEL_KEY
from app.message_limits import DM_HARD_LIMIT, byte_length, channel_text_allowance
from app.plugins.attention import scan_source
from app.plugins.capabilities import (
    REGISTRY,
    Call,
    CapabilityError,
    invalid_params,
    messages,
    storage,
    timer,
)
from app.plugins.capabilities.grants import (
    effective_permissions,
    hook_allowed,
    is_public_channel,
    message_visible,
)
from app.plugins.errors import HostMethodError, PluginIssue, PluginValidationError
from app.plugins.launch import popen_kwargs, prepare_work_dir, runner_command, runner_params
from app.plugins.manifest import Manifest
from app.plugins.package import validate_package
from app.plugins.quotas import (
    PERIOD_SECONDS,
    GuardRefused,
    QuotaExceeded,
    SendGuard,
    SendQuota,
    effective_send_limits,
)
from app.region_scope import is_unscoped, normalize_region_scope
from meshloom_plugin import rpc

INIT_TIMEOUT = 20.0  # cadrage §4: cold start on a Raspberry Pi 3
EVENT_TIMEOUT = 30.0
SHUTDOWN_TIMEOUT = 5.0
STDERR_TAIL_BYTES = 4000
SKIPPED_DIRS = ("__pycache__",)

# The plugin must not see a real conversation: a synthetic message in a synthetic channel.
TEST_MESSAGE: dict[str, Any] = {
    "sender_name": "Tester",
    "sender_key": "ab" * 32,
    "message_text": "!test hello",
    "is_dm": False,
    "channel_key": "CD" * 16,
    "channel_name": "#test",
    "conversation_key": "CD" * 16,
    "is_public_channel": False,
    "sender_timestamp": 1_700_000_000,
    "received_at": 1_700_000_000,
    "path": "",
    "path_bytes_per_hop": 1,
    "packet_hash": "00" * 8,
    "region": None,
    "scoped": False,
    "is_outgoing": False,
    "test": True,
}


@dataclass
class Finding:
    stage: str  # package | scan | run
    code: str
    message: str
    path: str = ""
    line: int | None = None
    informative: bool = False

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"stage": self.stage, "code": self.code, "message": self.message}
        if self.path:
            out["path"] = self.path
        if self.line is not None:
            out["line"] = self.line
        if self.informative:
            out["informative"] = True
        return out


@dataclass
class CheckReport:
    plugin_id: str | None = None
    version: str | None = None
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    ran: bool = False
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def sends(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c["method"] == "messages.send"]

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "plugin": {"id": self.plugin_id, "version": self.version},
            "errors": [f.to_json() for f in self.errors],
            "warnings": [f.to_json() for f in self.warnings],
            "dry_run": {
                "isolation": "none (development only)",
                "ran": self.ran,
                "calls": self.calls,
                "stderr": self.stderr,
            },
        }


# --- stage 1: package ---------------------------------------------------------


def zip_directory(root: Path) -> bytes:
    """The directory as the archive `pack` builds: files at the root, mode 0644.

    Reproducible: entries sorted by path, the ZIP epoch (1980-01-01) as timestamp, a
    fixed creator system, so the same tree always gives the same bytes (and hash).
    A symlink is refused (`ValueError`): the server refuses them in an archive and
    zipping would silently follow it. `__pycache__` is left out.
    """
    names: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
        for name in [*dirnames, *filenames]:
            full = Path(dirpath) / name
            if full.is_symlink():
                raise ValueError(f"{full.relative_to(root).as_posix()} is a symbolic link")
            if full.is_file():
                names.append(full.relative_to(root).as_posix())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(names):
            info = zipfile.ZipInfo(rel)  # date_time defaults to 1980-01-01 00:00:00
            info.create_system = 3
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, (root / rel).read_bytes())
    return buf.getvalue()


def _package_stage(
    root: Path, origin: str, report: CheckReport
) -> tuple[Manifest, list[str]] | None:
    try:
        data = zip_directory(root)
    except (OSError, ValueError) as exc:
        report.errors.append(Finding("package", "package_unreadable", str(exc)))
        return None
    try:
        result = validate_package(data, origin=origin)  # type: ignore[arg-type]
    except PluginValidationError as exc:
        for issue in exc.issues:
            report.errors.append(_from_issue(issue))
        return None
    report.plugin_id = result.manifest.id
    report.version = result.manifest.version
    return result.manifest, [f for f in result.files if f.endswith(".py")]


def _from_issue(issue: PluginIssue) -> Finding:
    return Finding("package", issue.code, issue.message, path=issue.path)


# --- stage 2: static scan -----------------------------------------------------


def _scan_stage(root: Path, py_files: list[str], strict: bool, report: CheckReport) -> None:
    for rel in py_files:
        if rel.startswith("vendor/"):
            continue
        try:
            source = (root / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.errors.append(
                Finding("scan", "source_unreadable", f"cannot read {rel} as UTF-8: {exc}", rel)
            )
            continue
        for reason in scan_source(source).reasons:
            finding = Finding(
                "scan",
                reason.code,
                reason.code if reason.detail is None else f"{reason.code}: {reason.detail}",
                path=rel,
                line=reason.line or None,
                informative=reason.informative,
            )
            # Informative reasons never fail a check, blocking ones only with --strict.
            if strict and not reason.informative:
                report.errors.append(finding)
            else:
                report.warnings.append(finding)


# --- stage 3: dry run ---------------------------------------------------------


class _Child:
    """One runner process and its RPC stream, read by a thread."""

    def __init__(self, proc: subprocess.Popen[Any], stderr: Any):
        self.proc = proc
        self.stderr = stderr
        self.lines: queue.Queue[bytes | None] = queue.Queue()
        self._decoder = rpc.LineDecoder()
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        try:
            while chunk := os.read(self.proc.stdout.fileno(), 65536):
                for line in self._decoder.feed(chunk):
                    self.lines.put(line)
        except (OSError, rpc.FramingError):
            pass
        self.lines.put(None)

    def send(self, message: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(rpc.encode(message))
        self.proc.stdin.flush()

    def kill(self) -> None:
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            self.proc.kill()
        self.proc.wait()

    def stderr_tail(self) -> str:
        self.stderr.flush()
        self.stderr.seek(0, os.SEEK_END)
        size = self.stderr.tell()
        self.stderr.seek(max(0, size - STDERR_TAIL_BYTES))
        return self.stderr.read().decode("utf-8", "replace").strip()


def _spawn(root: Path, scratch: Path, instance_id: str) -> _Child:
    """Start the real runner (`isolation: none`) on `root` and send it its parameters."""
    work_dir = prepare_work_dir(scratch / "work")
    stderr = tempfile.TemporaryFile()  # noqa: SIM115 - closed by the caller's finally
    proc = subprocess.Popen(  # noqa: S603
        runner_command(), stderr=stderr, **popen_kwargs(work_dir)
    )
    child = _Child(proc, stderr)
    proc.stdin.write(  # type: ignore[union-attr]
        runner_params(root.resolve(), work_dir, instance_id, "none")
    )
    return child


def _host_reply(message: dict[str, Any]) -> Any:
    """What the dry run answers to a plugin's call. Nothing is sent anywhere."""
    if message["method"] == "messages.send":
        return {"captured": True}
    return None


def _await_response(
    child: _Child,
    msg_id: int,
    timeout: float,
    report: CheckReport,
    respond: Callable[[dict[str, Any]], Any] = _host_reply,
) -> dict[str, Any]:
    """Collect the plugin's calls until the answer to request `msg_id` arrives.
    `respond` answers each request of the plugin; a `HostMethodError` it raises
    becomes the JSON-RPC error the server would send."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            line = child.lines.get(timeout=max(0.0, deadline - time.monotonic()))
        except queue.Empty:
            raise TimeoutError(f"no answer within {timeout:g} s") from None
        if line is None:
            raise ConnectionError("the plugin process exited before answering")
        try:
            message = rpc.decode(line)
        except rpc.ProtocolError as exc:
            raise ConnectionError(f"invalid message from the plugin: {exc}") from None
        if "method" in message:
            params = message.get("params")
            report.calls.append(
                {"method": message["method"], "params": params, "notification": "id" not in message}
            )
            if "id" in message:
                try:
                    reply = rpc.result(message["id"], respond(message))
                except HostMethodError as exc:
                    reply = rpc.error(message["id"], exc.code, str(exc), exc.data)
                child.send(reply)
        elif message.get("id") == msg_id:
            return message


def _init_params(manifest: Manifest) -> dict[str, Any]:
    # Provisional until the supervisor (MSHL-179) fixes the real payload.
    return {
        "api": manifest.runtime.api,
        "instance_id": "dev-check",
        "config": {},
        "language": "en",
        "permissions": manifest.permissions.declared(),
        "isolation": "none",
        "test": True,
    }


def _run_error(code: str, message: str, report: CheckReport) -> None:
    report.errors.append(Finding("run", code, message))


def _runner_start_error(stderr: str) -> tuple[str, str] | None:
    for line in stderr.splitlines():
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        if isinstance(obj, dict) and "runner_error" in obj:
            return str(obj["runner_error"]), str(obj.get("message", ""))
    return None


def _dry_run(root: Path, manifest: Manifest, report: CheckReport) -> None:
    scratch = Path(tempfile.mkdtemp(prefix="meshloom-plugin-check-"))
    child: _Child | None = None
    try:
        child = _spawn(root, scratch, "dev-check")
        report.ran = True
        child.send(rpc.request(1, "plugin.init", _init_params(manifest)))
        reply = _await_response(child, 1, INIT_TIMEOUT, report)
        if "error" in reply:
            _run_error("init_failed", _error_text(reply["error"]), report)
            return
        child.send(rpc.request(2, "event.message", {"id": 2, **TEST_MESSAGE}))
        reply = _await_response(child, 2, EVENT_TIMEOUT, report)
        if "error" in reply:
            _run_error("on_message_failed", _error_text(reply["error"]), report)
            return
        child.send(rpc.request(3, "plugin.shutdown"))
        try:
            _await_response(child, 3, SHUTDOWN_TIMEOUT, report)
        except (TimeoutError, ConnectionError):
            pass  # a plugin that is slow to stop has already passed the checks above
    except (TimeoutError, ConnectionError, BrokenPipeError) as exc:
        start_error = _runner_start_error(child.stderr_tail()) if child else None
        if start_error is not None:
            _run_error(start_error[0], start_error[1], report)
        else:
            _run_error("run_failed", str(exc) or type(exc).__name__, report)
    except OSError as exc:
        _run_error("run_failed", f"cannot start the runner: {exc}", report)
    finally:
        if child is not None:
            report.stderr = child.stderr_tail()
            child.kill()
            child.stderr.close()
        shutil.rmtree(scratch, ignore_errors=True)


def _error_text(error: Any) -> str:
    if not isinstance(error, dict):
        return "unknown error"
    return str(error.get("message", "unknown error"))


# --- run --replay: the fake host ----------------------------------------------
#
# The plugin runs in the real runner (`isolation: none`). Its calls are answered by
# `FakeHost`, which applies the server's own rules through the shared pure-Python
# core and keeps every side effect in memory:
#
# - permissions: `grants.effective_permissions` (declared and granted, narrowed) and
#   `base.REGISTRY` (the permission each method needs), then the refusal
#   `permission_denied` exactly as `base.handle` raises it;
# - `messages.send`: the server's parameter model, target rules and size rule
#   (`capabilities.messages`), the per-plugin quotas (`quotas.effective_send_limits`)
#   and the send guards (`quotas.SendGuard`) over in-memory counters;
# - read gates: `grants.message_visible` / `hook_allowed`.
#
# Not simulated: the global 2 s spacing between sends (nothing is transmitted, so
# nothing waits), the egress guard of `http.fetch` (answers come from the fixture,
# never from the network) and the storage quota. Nothing leaves the machine.


class FixtureError(ValueError):
    """The replay fixture is not usable (exit status 2)."""


HOOKS = ("message", "raw_packet", "contact", "telemetry", "health", "timer")
REPLAY_EVENT_TIMEOUT = 30.0
# A message of the Public channel unless the fixture says otherwise.
DEFAULT_MESSAGE: dict[str, Any] = {
    **TEST_MESSAGE,
    "channel_key": PUBLIC_CHANNEL_KEY,
    "channel_name": "Public",
    "test": False,
}


class _Clock:
    def __init__(self, start: float | None) -> None:
        self.fixed = start is not None
        self.start = float(start) if start is not None else time.time()
        self.now = self.start

    def __call__(self) -> float:
        return self.now if self.fixed else time.time()


class _MemoryQuota(SendQuota):
    """`SendQuota` with its two counters in memory instead of `plugin_send_quota`."""

    def __init__(self, clock: Callable[[], float]) -> None:
        super().__init__(clock)
        self._counts: dict[str, tuple[int, int]] = {}

    async def counts(self, instance_id: str) -> dict[Any, int]:
        result: dict[Any, int] = {}
        for period in PERIOD_SECONDS:
            start, count = self._counts.get(period, (None, 0))
            result[period] = count if start == self._window(period)[0] else 0
        return result

    async def record(self, instance_id: str) -> None:
        for period in PERIOD_SECONDS:
            start = self._window(period)[0]
            old_start, count = self._counts.get(period, (None, 0))
            self._counts[period] = (start, count + 1 if old_start == start else 1)


class _MemoryGuardStore:
    """The methods `SendGuard` needs from `PluginSendGuardRepository`."""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], tuple[int, int]] = {}

    async def get(self, scope: str, subject: str) -> tuple[int, int] | None:
        return self.rows.get((scope, subject))

    async def record_send(
        self,
        *,
        window_start: int,
        destination: str,
        instance_id: str,
        now: int,
        sender: str | None,
        keep_cooldown_after: int,
        keep_quiet_after: int,
    ) -> None:
        for key in (("instance", ""), ("destination", destination)):
            start, count = self.rows.get(key, (window_start, 0))
            self.rows[key] = (window_start, count + 1 if start == window_start else 1)
        self.rows[("quiet", f"{instance_id}|{destination}")] = (now, 0)
        if sender is not None:
            self.rows[("cooldown", f"{instance_id}|{sender}")] = (now, 0)

    async def limits(self) -> dict[str, int]:
        return {}


@dataclass
class ReplayEvent:
    """What `messages._reply_target` and `_check_target` read of the server's event."""

    hook: str
    params: dict[str, Any]
    test: bool = False


class FakeHost:
    """The host side of a replay: answers the plugin's calls like the server would."""

    def __init__(
        self,
        manifest: Manifest,
        fixture: dict[str, Any],
        clock: _Clock,
        trace: Callable[[str], None],
    ) -> None:
        declared = manifest.permissions.declared()
        granted = fixture.get("granted_permissions", declared)
        self.permissions = effective_permissions({"permissions": declared}, granted)
        self.fixture = fixture
        self.clock = clock
        self.trace = trace
        self.instance = SimpleNamespace(
            instance_id="dev-replay",
            label=manifest.id,
            permissions=self.permissions,
            scope=fixture.get("scope"),
            quotas=fixture.get("quotas"),
        )
        self.quota = _MemoryQuota(clock)
        self.guard = SendGuard(clock, _MemoryGuardStore())
        self.event: ReplayEvent | None = None
        self.kv: dict[str, Any] = {}
        self.timers = 0
        self.status = ""
        self.sends: list[dict[str, Any]] = []
        self.refusals: list[dict[str, Any]] = []
        self.loop = asyncio.new_event_loop()

    # Plugin calls --------------------------------------------------------------

    def respond(self, message: dict[str, Any]) -> Any:
        """Answer one request (or notification) of the plugin; prints it and the reply."""
        method, params = message["method"], message.get("params")
        self.trace(f"    -> {method} {json.dumps(params, ensure_ascii=False)}")
        try:
            value = self.loop.run_until_complete(self.dispatch(method, params))
        except HostMethodError as exc:
            data = exc.data if isinstance(exc.data, dict) else {}
            code = data.get("code", exc.code)
            self.trace(f"    <- error {code}: {exc}")
            if data.get("params"):
                self.trace(f"       params {json.dumps(data['params'], ensure_ascii=False)}")
            self.refusals.append({"method": method, "code": code})
            raise
        if "id" in message:
            self.trace(f"    <- {json.dumps(value, ensure_ascii=False)}")
        return value

    async def dispatch(self, method: str, params: Any) -> Any:
        entry = REGISTRY.get(method)
        if entry is None:
            raise HostMethodError(rpc.METHOD_NOT_FOUND, f"{method} is not available")
        if entry.permission is not None and entry.permission not in self.permissions:
            raise CapabilityError(
                "permission_denied",
                f"{method} needs the {entry.permission} permission",
                {"permission": entry.permission},
            )
        if not isinstance(params, dict):
            params = {}
        handler = getattr(self, "do_" + method.replace(".", "_"), None)
        if handler is None:
            raise HostMethodError(rpc.METHOD_NOT_FOUND, f"{method} is not replayed")
        return await handler(params)

    # messages ------------------------------------------------------------------

    def _resolve(self, call: Call, params: Any) -> messages.Target:
        if params.to == "reply":
            target = messages._reply_target(call)
            if not target.key:
                raise CapabilityError(
                    "target_not_allowed", "the event has no conversation to reply to"
                )
            assert self.event is not None
            target.row = SimpleNamespace(name=self.event.params.get("channel_name"))
            return target
        if isinstance(params.to, messages._ChannelTo):
            target = messages.Target(False, params.to.channel_key.upper())
            for entry in self.fixture.get("channels", []):
                if str(entry.get("key", "")).upper() == target.key:
                    target.row = SimpleNamespace(name=entry.get("name"))
        else:
            target = messages.Target(True, params.to.contact_key.lower())
            for entry in self.fixture.get("contacts", []):
                if str(entry.get("public_key", "")).lower() == target.key:
                    target.row = SimpleNamespace(name=entry.get("name"))
        return target

    def _check_region(self, params: Any, target: messages.Target) -> None:
        if target.is_dm or "region" not in params.model_fields_set:
            return
        if params.region is None or is_unscoped(params.region):
            return
        known = {normalize_region_scope(r) for r in self.fixture.get("known_regions", [])}
        known.discard("")
        if normalize_region_scope(params.region) not in known:
            raise CapabilityError("region_unknown", "unknown region")

    def _reply_context(self, target: messages.Target) -> tuple[str | None, int | None]:
        # `messages._reply_context`, with this host's guard.
        event = self.event
        if event is not None and event.hook == "message" and not event.params.get("is_outgoing"):
            received = event.params.get("received_at")
            return SendGuard.sender_of(event.params), received if type(received) is int else None
        last = self.guard.last_inbound(
            self.instance.instance_id, is_dm=target.is_dm, key=target.key
        )
        return (last[0], last[1]) if last is not None else (None, None)

    async def do_messages_send(self, raw: dict[str, Any]) -> dict[str, Any]:
        call = Call(self.instance, "messages.send", raw, self.event)  # type: ignore[arg-type]
        params = messages._parse(raw)
        permission = self.permissions["messages.send"]
        target = self._resolve(call, params)
        messages._check_target(call, permission.get("targets", "reply"), target)
        self._check_region(params, target)
        limit = (
            DM_HARD_LIMIT
            if target.is_dm
            else channel_text_allowance(self.fixture.get("radio_name"))
        )
        size = byte_length(params.text)
        if size > limit:
            raise CapabilityError(
                "message_too_long",
                f"the message is {size} bytes, the limit is {limit}",
                {"limit": limit, "bytes": size},
            )
        instance_id = self.instance.instance_id
        limits = effective_send_limits(permission, self.instance.quotas)
        try:
            await self.quota.check(instance_id, limits)
        except QuotaExceeded as exc:
            raise CapabilityError(
                "quota_exceeded",
                f"at most {exc.limit} messages per {exc.period}",
                {
                    "scope": "plugin",
                    "period": exc.period,
                    "limit": exc.limit,
                    "retry_after": exc.retry_after,
                },
            ) from None
        sender, received_at = self._reply_context(target)
        try:
            await self.guard.check(
                instance_id,
                is_dm=target.is_dm,
                key=target.key,
                sender=sender,
                received_at=received_at,
            )
        except GuardRefused as exc:
            raise CapabilityError("quota_exceeded", str(exc), exc.params) from None
        await self.guard.record(instance_id, is_dm=target.is_dm, key=target.key, sender=sender)
        await self.quota.record(instance_id)
        self.sends.append({k: v for k, v in raw.items() if k != "event_id"})
        return {"sent": True, "message_id": None}

    # storage, timer, status, log -----------------------------------------------

    @staticmethod
    def _key(params: dict[str, Any]) -> str:
        key = params.get("key")
        if not isinstance(key, str) or not 1 <= len(key.encode("utf-8")) <= 256:
            raise invalid_params("invalid key", fields=["key"])
        return key

    async def do_storage_get(self, params: dict[str, Any]) -> Any:
        return self.kv.get(self._key(params))

    async def do_storage_set(self, params: dict[str, Any]) -> None:
        key = self._key(params)
        if "value" not in params:
            raise invalid_params("value is required", fields=["value"])
        try:
            json.dumps(params["value"], allow_nan=False)
        except (TypeError, ValueError):
            raise invalid_params("the value is not JSON", fields=["value"]) from None
        self.kv[key] = params["value"]

    async def do_storage_delete(self, params: dict[str, Any]) -> None:
        self.kv.pop(self._key(params), None)

    async def do_storage_list(self, params: dict[str, Any]) -> list[str]:
        prefix, after = params.get("prefix") or "", params.get("after")
        limit = params.get("limit", storage.LIST_MAX)
        if type(limit) is not int or not 1 <= limit <= storage.LIST_MAX:
            raise invalid_params("invalid limit", fields=["limit"])
        keys = sorted(k for k in self.kv if k.startswith(prefix) and (after is None or k > after))
        return keys[:limit]

    async def do_timer_set(self, params: dict[str, Any]) -> dict[str, Any]:
        seconds = params.get("seconds")
        if not isinstance(seconds, int | float) or isinstance(seconds, bool) or seconds != seconds:
            raise invalid_params("seconds must be a number", fields=["seconds"])
        if not timer.TIMER_MIN_SECONDS <= seconds <= timer.TIMER_MAX_SECONDS:
            raise invalid_params(
                f"seconds is {timer.TIMER_MIN_SECONDS} to {timer.TIMER_MAX_SECONDS}",
                fields=["seconds"],
                min=timer.TIMER_MIN_SECONDS,
                max=timer.TIMER_MAX_SECONDS,
            )
        if self.timers >= timer.MAX_PENDING_TIMERS:
            raise CapabilityError(
                "quota_exceeded", "too many pending timers", {"limit": timer.MAX_PENDING_TIMERS}
            )
        self.timers += 1
        return {"timer_id": self.timers}  # never fires by itself: put a timer event in the fixture

    async def do_status_set(self, params: dict[str, Any]) -> None:
        self.status = str(params.get("summary") or "")[:140]

    async def do_log(self, params: dict[str, Any]) -> None:
        return None

    # reads and network, answered from the fixture -------------------------------

    async def do_contacts_list(self, params: dict[str, Any]) -> list[Any]:
        return list(self.fixture.get("contacts", []))

    async def do_contacts_get(self, params: dict[str, Any]) -> Any:
        key = str(params.get("key", "")).lower()
        for entry in self.fixture.get("contacts", []):
            if str(entry.get("public_key", "")).lower() == key:
                return entry
        return None

    async def do_channels_list(self, params: dict[str, Any]) -> list[Any]:
        return list(self.fixture.get("channels", []))

    async def do_http_fetch(self, params: dict[str, Any]) -> Any:
        answer = (self.fixture.get("http") or {}).get(str(params.get("url")))
        if answer is None:
            raise CapabilityError(
                "fetch_failed",
                "no answer for this URL in the fixture (replay never uses the network)",
                {"reason": "replay_no_answer"},
            )
        return {"status": 200, "headers": {}, "body": "", "truncated": False, **answer}

    # events --------------------------------------------------------------------

    def event_for(self, spec: dict[str, Any]) -> tuple[str, ReplayEvent | None, str]:
        """`(method, event, why)`: the event the plugin is handed, None with the reason
        when the server would not deliver it to this instance."""
        hook = spec["hook"]
        method = f"event.{hook}"
        if hook == "message":
            given = spec.get("params", {})
            params = {**DEFAULT_MESSAGE, "received_at": int(self.clock()), **given}
            if params["is_dm"]:
                params["channel_key"] = params["channel_name"] = None
                params["conversation_key"] = given.get("conversation_key", params["sender_key"])
            else:
                params["conversation_key"] = given.get("conversation_key", params["channel_key"])
                # A channel message carries no sender key (the guards tell senders by name).
                params["sender_key"] = given.get("sender_key")
            params["is_public_channel"] = given.get(
                "is_public_channel",
                not params["is_dm"]
                and is_public_channel(str(params["channel_key"]), params["channel_name"]),
            )
            if not message_visible(self.permissions, self.instance.scope, params):
                return method, None, "messages.read or the scope does not cover it"
        else:
            params = dict(spec.get("params", {}))
            if hook == "timer":
                if "timer" not in self.permissions:
                    return method, None, "the timer permission is not effective"
            elif not hook_allowed(self.permissions, self.instance.scope, hook):
                return method, None, "the permission or the scope does not cover it"
        return method, ReplayEvent(hook, params), ""

    def close(self) -> None:
        self.loop.close()


@dataclass
class ReplayReport:
    plugin_id: str | None = None
    version: str | None = None
    errors: list[Finding] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)
    refusals: list[dict[str, Any]] = field(default_factory=list)
    sends: list[dict[str, Any]] = field(default_factory=list)
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "plugin": {"id": self.plugin_id, "version": self.version},
            "errors": [f.to_json() for f in self.errors],
            "events": self.events,
            "calls": self.calls,
            "refusals": self.refusals,
            "sends": self.sends,
            "isolation": "none (development only)",
        }


def load_fixture(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise FixtureError(f"cannot read the fixture {path}: {exc}") from None
    if not isinstance(data, dict):
        raise FixtureError("the fixture must be a JSON object")
    events = data.get("events")
    if not isinstance(events, list) or not events:
        raise FixtureError('the fixture needs a non-empty "events" list')
    for i, spec in enumerate(events, 1):
        if not isinstance(spec, dict) or spec.get("hook") not in HOOKS:
            raise FixtureError(f'event {i}: "hook" must be one of {", ".join(HOOKS)}')
        if not isinstance(spec.get("params", {}), dict):
            raise FixtureError(f'event {i}: "params" must be an object')
        if "at" in spec and type(spec["at"]) not in (int, float):
            raise FixtureError(f'event {i}: "at" must be a number of seconds')
    for name in ("granted_permissions", "scope", "quotas", "config", "http"):
        if name in data and not isinstance(data[name], dict):
            raise FixtureError(f'"{name}" must be an object')
    for name in ("channels", "contacts", "known_regions"):
        if name in data and not isinstance(data[name], list):
            raise FixtureError(f'"{name}" must be a list')
    if "clock" in data and type(data["clock"]) not in (int, float):
        raise FixtureError('"clock" must be a number (Unix seconds)')
    return data


def replay_directory(
    root: Path,
    fixture: dict[str, Any],
    *,
    origin: str = "third_party",
    trace: Callable[[str], None] = lambda _line: None,
) -> ReplayReport:
    """Run the plugin and feed it the fixture's events; see the block comment above."""
    report = ReplayReport()
    check = CheckReport()
    if not root.is_dir():
        report.errors.append(Finding("package", "not_a_directory", f"{root} is not a directory"))
        return report
    staged = _package_stage(root, origin, check)
    report.plugin_id, report.version = check.plugin_id, check.version
    if staged is None:
        report.errors.extend(check.errors)
        return report
    manifest = staged[0]
    clock = _Clock(fixture.get("clock"))
    host = FakeHost(manifest, fixture, clock, trace)
    scratch = Path(tempfile.mkdtemp(prefix="meshloom-plugin-replay-"))
    child: _Child | None = None
    sink = CheckReport()  # _await_response records the plugin's calls there
    try:
        child = _spawn(root, scratch, host.instance.instance_id)
        init = {**_init_params(manifest), "instance_id": host.instance.instance_id}
        init.update(config=fixture.get("config", {}), permissions=host.permissions, test=False)
        trace("plugin.init")
        child.send(rpc.request(1, "plugin.init", init))
        reply = _await_response(child, 1, INIT_TIMEOUT, sink, host.respond)
        if "error" in reply:
            report.errors.append(Finding("run", "init_failed", _error_text(reply["error"])))
            return report
        msg_id = 1
        for number, spec in enumerate(fixture["events"], 1):
            if "at" in spec:
                clock.fixed = True
                clock.now = clock.start + float(spec["at"])
            method, event, why = host.event_for(spec)
            summary = {"n": number, "hook": spec["hook"], "delivered": event is not None}
            if event is None:
                trace(f"event {number} {spec['hook']}: not delivered ({why})")
                report.events.append({**summary, "reason": why})
                continue
            trace(f"event {number} {method} {json.dumps(event.params, ensure_ascii=False)}")
            host.event = event
            if spec["hook"] == "message":
                host.guard.note_inbound(host.instance.instance_id, event.params)
            msg_id += 1
            child.send(rpc.request(msg_id, method, {"id": msg_id, **event.params}))
            reply = _await_response(child, msg_id, REPLAY_EVENT_TIMEOUT, sink, host.respond)
            host.event = None
            if "error" in reply:
                text = _error_text(reply["error"])
                trace(f"  handler failed: {text}")
                report.errors.append(Finding("run", "handler_failed", f"event {number}: {text}"))
                report.events.append({**summary, "error": text})
            else:
                report.events.append(summary)
        child.send(rpc.request(msg_id + 1, "plugin.shutdown"))
        try:
            _await_response(child, msg_id + 1, SHUTDOWN_TIMEOUT, sink, host.respond)
        except (TimeoutError, ConnectionError):
            pass
    except (TimeoutError, ConnectionError, BrokenPipeError) as exc:
        start_error = _runner_start_error(child.stderr_tail()) if child else None
        code, text = start_error or ("run_failed", str(exc) or type(exc).__name__)
        report.errors.append(Finding("run", code, text))
    except OSError as exc:
        report.errors.append(Finding("run", "run_failed", f"cannot start the runner: {exc}"))
    finally:
        report.calls = sink.calls
        report.refusals = host.refusals
        report.sends = host.sends
        if child is not None:
            report.stderr = child.stderr_tail()
            child.kill()
            child.stderr.close()
        host.close()
        shutil.rmtree(scratch, ignore_errors=True)
    return report


# --- pack ---------------------------------------------------------------------

ARCHIVE_NAME = "plugin.zip"


def pack_directory(
    root: Path,
    out_dir: Path,
    *,
    origin: str = "third_party",
    strict: bool = False,
    dry_run: bool = True,
) -> tuple[CheckReport, Path | None, str | None]:
    """`check` first; when it passes, write `plugin.zip` and `plugin.zip.sha256` into
    `out_dir` and return `(report, archive path, sha256)`. Byte-for-byte reproducible:
    the archive is `zip_directory`'s. Nothing is written when the check fails."""
    report = check_directory(root, origin=origin, strict=strict, dry_run=dry_run)
    if not report.ok:
        return report, None, None
    resolved = out_dir.resolve()
    if resolved == root.resolve() or root.resolve() in resolved.parents:
        report.errors.append(
            Finding(
                "package",
                "output_inside_plugin",
                "the output directory must be outside the plugin directory",
            )
        )
        return report, None, None
    data = zip_directory(root)
    digest = hashlib.sha256(data).hexdigest()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / ARCHIVE_NAME
    archive.write_bytes(data)
    (out_dir / f"{ARCHIVE_NAME}.sha256").write_text(f"{digest}  {ARCHIVE_NAME}\n", "utf-8")
    return report, archive, digest


# --- entry point --------------------------------------------------------------


def check_directory(
    root: Path, *, origin: str = "third_party", strict: bool = False, dry_run: bool = True
) -> CheckReport:
    """The three stages. `dry_run=False` stops after the scan: nothing is executed,
    which is what the server's `POST /api/plugins/{id}/check` runs (the dry run
    uses `isolation: none`, development only)."""
    report = CheckReport()
    if not root.is_dir():
        report.errors.append(Finding("package", "not_a_directory", f"{root} is not a directory"))
        return report
    staged = _package_stage(root, origin, report)
    if staged is None:
        return report
    manifest, py_files = staged
    _scan_stage(root, py_files, strict, report)
    if dry_run:
        _dry_run(root, manifest, report)
    return report


def _print_text(report: CheckReport) -> None:
    label = report.plugin_id and f"{report.plugin_id} {report.version}"
    print(f"Checking {label or 'plugin'}")
    for finding in report.errors:
        print(f"  error   [{finding.stage}] {_where(finding)}{finding.code}: {finding.message}")
    for finding in report.warnings:
        tag = "info   " if finding.informative else "warning"
        print(f"  {tag} [{finding.stage}] {_where(finding)}{finding.message}")
    if report.ran:
        print(
            f"  dry run (isolation: none (development only), runner guards but no OS sandbox): {len(report.calls)} call(s) to the host"
        )
        for send in report.sends:
            print(f"    messages.send {json.dumps(send['params'], ensure_ascii=False)}")
        if report.stderr and not report.ok:
            print("  plugin log (tail):")
            for line in report.stderr.splitlines():
                print(f"    {line}")
    print("OK" if report.ok else f"FAILED ({len(report.errors)} error(s))")


def _where(finding: Finding) -> str:
    if not finding.path:
        return ""
    return f"{finding.path}{f':{finding.line}' if finding.line else ''}: "


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("directory", type=Path)
    parser.add_argument(
        "--origin",
        choices=["third_party", "bundled", "local"],
        default="third_party",
        help="who submits the plugin: decides the id namespace rule (default third_party)",
    )


def _print_replay(report: ReplayReport) -> None:
    for finding in report.errors:
        print(f"  error   [{finding.stage}] {_where(finding)}{finding.code}: {finding.message}")
    delivered = sum(1 for e in report.events if e["delivered"])
    print(
        f"  {len(report.events)} event(s), {delivered} delivered; {len(report.sends)} send(s) "
        f"accepted (not transmitted), {len(report.refusals)} refusal(s)"
    )
    if report.stderr and not report.ok:
        print("  plugin log (tail):")
        for line in report.stderr.splitlines():
            print(f"    {line}")
    print("OK" if report.ok else f"FAILED ({len(report.errors)} error(s))")


def _main_run(args: argparse.Namespace) -> int:
    try:
        fixture = load_fixture(args.replay)
    except FixtureError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    label = args.directory.name
    trace = (lambda _line: None) if args.json else print
    if not args.json:
        print(
            f"Replaying {len(fixture['events'])} event(s) on {label} "
            "(isolation: none (development only); nothing is transmitted)"
        )
    report = replay_directory(args.directory, fixture, origin=args.origin, trace=trace)
    if args.json:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    else:
        _print_replay(report)
    if report.errors and all(f.code == "not_a_directory" for f in report.errors):
        return 2
    return 0 if report.ok else 1


def _main_pack(args: argparse.Namespace) -> int:
    report, archive, digest = pack_directory(
        args.directory,
        args.out,
        origin=args.origin,
        strict=args.strict,
        dry_run=not args.skip_run,
    )
    if args.json:
        data = report.to_json()
        data["archive"] = {"path": str(archive), "sha256": digest} if archive else None
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_text(report)
        if archive is not None:
            print(f"Wrote {archive} and {archive.name}.sha256 ({digest})")
    if report.errors and all(f.code == "not_a_directory" for f in report.errors):
        return 2
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meshloom_plugin.dev")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "check", help="validate a plugin directory and dry-run it in the sandbox"
    )
    _add_common(check)
    check.add_argument("--json", action="store_true", help="print one JSON object (for CI)")
    check.add_argument(
        "--strict", action="store_true", help="fail on blocking static-scan findings"
    )
    run = sub.add_parser(
        "run", help="run a plugin against a fake host fed by a JSON fixture (no radio)"
    )
    _add_common(run)
    run.add_argument("--replay", type=Path, required=True, metavar="FIXTURE.json")
    run.add_argument("--json", action="store_true", help="print one JSON object")
    pack = sub.add_parser(
        "pack", help="check a plugin, then build plugin.zip and plugin.zip.sha256"
    )
    _add_common(pack)
    pack.add_argument(
        "--out", type=Path, default=Path("."), help="output directory (default: current)"
    )
    pack.add_argument("--json", action="store_true", help="print one JSON object (for CI)")
    pack.add_argument("--strict", action="store_true", help="fail on blocking static-scan findings")
    pack.add_argument(
        "--skip-run", action="store_true", help="skip the sandbox dry run of the check"
    )
    args = parser.parse_args(argv)
    if args.command == "run":
        return _main_run(args)
    if args.command == "pack":
        return _main_pack(args)
    report = check_directory(args.directory, origin=args.origin, strict=args.strict)
    if args.json:
        print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))
    else:
        _print_text(report)
    if report.errors and all(f.code == "not_a_directory" for f in report.errors):
        return 2  # a bad path is a usage error
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
