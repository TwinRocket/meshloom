"""Start a Meshloom-only upgrade via the privileged helper. Never apt-upgrade the OS."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Literal, cast

logger = logging.getLogger(__name__)

JobState = Literal["idle", "applying", "succeeded", "failed"]
JobPhase = Literal["preparing", "downloading", "installing", "restarting", "done"]

DEFAULT_DATA_DIR = Path("/var/lib/meshloom")
DEFAULT_JOB_PATH = DEFAULT_DATA_DIR / "update-job.json"
AUTO_UPDATE_BACKOFF_SECONDS = 6 * 3600
APPLYING_TTL_SECONDS = 20 * 60
APPLY_TIMEOUT_ERROR = "apply timed out"
_PUBLIC_JOB_KEYS = ("state", "phase", "percent", "error", "started_at")
_PHASES = {"preparing", "downloading", "installing", "restarting", "done"}

_apply_lock = asyncio.Lock()


class UpdateApplyBusy(Exception):
    """A helper job is already applying."""


def data_dir() -> Path:
    """Job files live next to the database so Docker ./data and the package path both work."""
    db = (os.environ.get("MESHCORE_DATABASE_PATH") or "").strip()
    if not db:
        return DEFAULT_DATA_DIR
    path = Path(db).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.parent


def job_path() -> Path:
    override = (os.environ.get("MESHLOOM_UPDATE_JOB_PATH") or "").strip()
    return Path(override) if override else data_dir() / "update-job.json"


def request_path() -> Path:
    """Compose helper request file: ``request-update`` next to the job file."""
    return job_path().with_name("request-update")


def idle_job() -> dict[str, Any]:
    return {
        "state": "idle",
        "phase": None,
        "percent": None,
        "error": None,
        "started_at": None,
    }


def public_job(job: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET/POST body job object — no last_attempt / target."""
    current = job if job is not None else read_job()
    return {key: current.get(key) for key in _PUBLIC_JOB_KEYS}


def read_job() -> dict[str, Any]:
    path = job_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return idle_job()
    if not isinstance(data, dict):
        return idle_job()
    merged = idle_job()
    for key in _PUBLIC_JOB_KEYS:
        if key in data:
            merged[key] = data[key]
    if data.get("state") not in {"idle", "applying", "succeeded", "failed"}:
        merged["state"] = "idle"
    if data.get("last_attempt") is not None:
        merged["last_attempt"] = data["last_attempt"]
    if data.get("target") is not None:
        merged["target"] = data["target"]
    return merged


def write_job(
    *,
    state: JobState,
    phase: JobPhase | None = None,
    percent: int | None = None,
    error: str | None = None,
    started_at: int | None = None,
    last_attempt: int | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    now = int(time.time())
    payload: dict[str, Any] = {
        "state": state,
        "phase": phase,
        "percent": percent,
        "error": error,
        "started_at": started_at if started_at is not None else now,
        "last_attempt": last_attempt if last_attempt is not None else now,
    }
    if target is not None:
        payload["target"] = target
    path = job_path()
    _write_job_file(path, json.dumps(payload))
    return payload


def _write_job_file(path: Path, text: str) -> None:
    """Replace the job file even when a root helper left it unwritable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        try:
            tmp.replace(path)
        except PermissionError:
            _remove_job_file(path)
            tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _remove_job_file(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        path.chmod(path.stat().st_mode | 0o200)
        path.unlink(missing_ok=True)


def _as_phase(value: Any) -> JobPhase | None:
    if value in _PHASES:
        return cast(JobPhase, value)
    return None


def expire_stale_applying_job(*, now: int | None = None) -> dict[str, Any]:
    """Turn a helper job that never finished into failed so apply is not 409 forever."""
    current = read_job()
    if current.get("state") != "applying":
        return current
    stamp = current.get("started_at")
    if not isinstance(stamp, int):
        return current
    current_now = now if now is not None else int(time.time())
    if current_now - stamp < APPLYING_TTL_SECONDS:
        return current
    last = current.get("last_attempt")
    target = current.get("target")
    percent = current.get("percent")
    expired = {
        "state": "failed",
        "phase": _as_phase(current.get("phase")),
        "percent": percent if isinstance(percent, int) else None,
        "error": APPLY_TIMEOUT_ERROR,
        "started_at": stamp,
        "last_attempt": last if isinstance(last, int) else stamp,
    }
    if isinstance(target, str):
        expired["target"] = target
    try:
        return write_job(
            state="failed",
            phase=_as_phase(current.get("phase")),
            percent=percent if isinstance(percent, int) else None,
            error=APPLY_TIMEOUT_ERROR,
            started_at=stamp,
            last_attempt=last if isinstance(last, int) else stamp,
            target=target if isinstance(target, str) else None,
        )
    except OSError:
        logger.warning("Could not persist expired apply job at %s", job_path())
        return expired


def job_is_applying(job: dict[str, Any] | None = None) -> bool:
    current = job if job is not None else read_job()
    return current.get("state") == "applying"


def last_attempt_recent(job: dict[str, Any], *, now: int | None = None) -> bool:
    stamp = job.get("last_attempt")
    if not isinstance(stamp, int):
        stamp = job.get("started_at")
    if not isinstance(stamp, int):
        return False
    current = now if now is not None else int(time.time())
    return current - stamp < AUTO_UPDATE_BACKOFF_SECONDS


async def start_package_helper() -> None:
    """``systemctl start meshloom-update.service`` (pkg/nfpm helper)."""
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        "start",
        "meshloom-update.service",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        detail = (stderr or b"").decode("utf-8", errors="replace").strip() or (
            "systemctl start failed"
        )
        raise RuntimeError(detail)


def start_compose_helper() -> None:
    path = request_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # PathExists only fires on absent → present. Drop a leftover request first.
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    path.write_text("1\n", encoding="utf-8")


async def start_apply(kind: str, *, target: str | None = None) -> dict[str, Any]:
    async with _apply_lock:
        expire_stale_applying_job()
        if job_is_applying():
            raise UpdateApplyBusy()
        job = write_job(state="applying", phase="preparing", target=target)
        try:
            if kind == "package":
                await start_package_helper()
            elif kind == "compose":
                start_compose_helper()
            else:
                raise RuntimeError(f"apply is not supported for {kind}")
        except Exception as exc:
            logger.exception("Failed to start Meshloom apply helper")
            return write_job(
                state="failed",
                phase="preparing",
                error=str(exc),
                started_at=job.get("started_at"),
                last_attempt=job.get("last_attempt"),
                target=target,
            )
        return read_job()
