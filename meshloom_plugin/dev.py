"""Plugin author tooling: `python -m meshloom_plugin.dev check <dir>` (MSHL-178).

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

Exit status: 0 passed, 1 failed, 2 usage error (a bad path included). `--json` prints one object.
"""

import argparse
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.plugins.attention import scan_source
from app.plugins.errors import PluginIssue, PluginValidationError
from app.plugins.launch import popen_kwargs, prepare_work_dir, runner_command, runner_params
from app.plugins.manifest import Manifest
from app.plugins.package import validate_package
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
    """The directory as the archive `pack` will build: files at the root, mode 0644.

    A symlink is refused (`ValueError`): the server refuses them in an archive and
    zipping would silently follow it. `__pycache__` is left out.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIPPED_DIRS)
            for name in [*dirnames, *sorted(filenames)]:
                full = Path(dirpath) / name
                rel = full.relative_to(root).as_posix()
                if full.is_symlink():
                    raise ValueError(f"{rel} is a symbolic link")
                if full.is_dir():
                    continue
                info = zipfile.ZipInfo(rel)
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zf.writestr(info, full.read_bytes())
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


def _host_reply(message: dict[str, Any]) -> Any:
    """What the dry run answers to a plugin's call. Nothing is sent anywhere."""
    if message["method"] == "messages.send":
        return {"captured": True}
    return None


def _await_response(
    child: _Child, msg_id: int, timeout: float, report: CheckReport
) -> dict[str, Any]:
    """Collect the plugin's calls until the answer to request `msg_id` arrives."""
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
                child.send(rpc.result(message["id"], _host_reply(message)))
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
        work_dir = prepare_work_dir(scratch / "work")
        stderr = tempfile.TemporaryFile()  # noqa: SIM115 - closed in the finally below
        proc = subprocess.Popen(  # noqa: S603
            runner_command(), stderr=stderr, **popen_kwargs(work_dir)
        )
        child = _Child(proc, stderr)
        report.ran = True
        proc.stdin.write(  # type: ignore[union-attr]
            runner_params(root.resolve(), work_dir, "dev-check", "none")
        )
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


# --- entry point --------------------------------------------------------------


def check_directory(
    root: Path, *, origin: str = "third_party", strict: bool = False
) -> CheckReport:
    report = CheckReport()
    if not root.is_dir():
        report.errors.append(Finding("package", "not_a_directory", f"{root} is not a directory"))
        return report
    staged = _package_stage(root, origin, report)
    if staged is None:
        return report
    manifest, py_files = staged
    _scan_stage(root, py_files, strict, report)
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meshloom_plugin.dev")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser(
        "check", help="validate a plugin directory and dry-run it in the sandbox"
    )
    check.add_argument("directory", type=Path)
    check.add_argument("--json", action="store_true", help="print one JSON object (for CI)")
    check.add_argument(
        "--origin",
        choices=["third_party", "bundled", "local"],
        default="third_party",
        help="who submits the plugin: decides the id namespace rule (default third_party)",
    )
    check.add_argument(
        "--strict", action="store_true", help="fail on blocking static-scan findings"
    )
    args = parser.parse_args(argv)
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
