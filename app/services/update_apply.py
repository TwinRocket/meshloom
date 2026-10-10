"""Start a Meshloom-only upgrade via the privileged helper. Never apt-upgrade the OS.

Trust boundary (app/AGENTS.md, "Updates"): the app only *triggers* the root
helper by writing ``request-update``. The helper picks its target from a signed
source and publishes progress in a root-owned ``status.json`` that the app only
reads. What the app itself remembers (requested target, last attempt, app-side
failures) lives in ``update-attempt.json`` in its own data directory.
``read_job()`` merges the two. ``update-job.json`` is the pre-4.18 helper file:
read-only, for display, while an old compose helper is still installed.
"""

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
PACKAGE_REQUEST_PATH = DEFAULT_DATA_DIR / "request-update"
# Root-owned (StateDirectory=meshloom-update); written only by apply-update.
PACKAGE_STATUS_PATH = Path("/var/lib/meshloom-update/status.json")
ATTEMPT_FILE_NAME = "update-attempt.json"
STATUS_MAX_BYTES = 64 * 1024
# The helper starts after the request; allow for coarse clocks only.
STATUS_CLOCK_SKEW_SECONDS = 2
UPDATE_PATH_UNIT = Path("/usr/lib/systemd/system/meshloom-update.path")
AUTO_UPDATE_BACKOFF_SECONDS = 6 * 3600
APPLYING_TTL_SECONDS = 20 * 60
APPLY_TIMEOUT_ERROR = "apply timed out"
COOLDOWN_ERROR = "update requested too soon; try again in a few minutes"
# How long the polkit fallback watches the oneshot leave its initial state.
HELPER_START_CHECK_SECONDS = 5.0
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


def attempt_path() -> Path:
    """App-owned record of the last apply request (never read by root)."""
    return job_path().with_name(ATTEMPT_FILE_NAME)


def status_path() -> Path:
    """Root-written helper status: compose mounts it read-only, package uses StateDirectory."""
    override = (os.environ.get("MESHLOOM_UPDATE_STATUS_PATH") or "").strip()
    return Path(override) if override else PACKAGE_STATUS_PATH


def secure_compose_helper() -> bool:
    """True when the 4.18+ compose helper mounted its status directory here."""
    override = (os.environ.get("MESHLOOM_UPDATE_STATUS_PATH") or "").strip()
    return bool(override) and Path(override).parent.is_dir()


def write_request_file(path: Path) -> None:
    """Drop a leftover first: PathExists is level-triggered if the file is already there."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    path.write_text("1\n", encoding="utf-8")


def package_path_unit_present() -> bool:
    return UPDATE_PATH_UNIT.exists()


async def package_path_watcher_active() -> bool:
    """True only when the packaged .path unit is loaded and actually watching."""
    if not package_path_unit_present():
        return False
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl",
            "is-active",
            "--quiet",
            "meshloom-update.path",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return False
    await proc.communicate()
    return proc.returncode == 0


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


def normalize_update_version(value: str | None) -> str:
    if not value:
        return ""
    text = value.strip()
    if len(text) > 1 and text[0] in {"v", "V"} and text[1].isdigit():
        return text[1:]
    return text


def versions_match(left: str | None, right: str | None) -> bool:
    left_norm = normalize_update_version(left)
    right_norm = normalize_update_version(right)
    return bool(left_norm) and left_norm == right_norm


def public_job_for_client(
    job: dict[str, Any] | None = None,
    *,
    current: str | None = None,
    latest: str | None = None,
) -> dict[str, Any]:
    """Hide a leftover failed job when this process is already on the target."""
    raw = job if job is not None else read_job()
    public = public_job(raw)
    if public.get("state") != "failed":
        return public
    target = raw.get("target") if isinstance(raw.get("target"), str) else None
    if versions_match(current, target) or versions_match(current, latest):
        return {**public, "state": "succeeded", "phase": "done", "error": None}
    return public


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        with path.open("rb") as handle:
            raw = handle.read(STATUS_MAX_BYTES + 1)
    except OSError:
        return None
    if len(raw) > STATUS_MAX_BYTES:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _normalize_record(
    data: dict[str, Any] | None, *, allow_cooldown: bool = False
) -> dict[str, Any] | None:
    """Strictly typed job record, or None. Unknown keys and bad types are dropped.

    ``cooldown`` exists only in helper status: the helper refused a request that
    came too soon and left the previous outcome alone.
    """
    if data is None:
        return None
    state = data.get("state")
    allowed = {"idle", "applying", "succeeded", "failed"}
    if allow_cooldown:
        allowed.add("cooldown")
    if state not in allowed:
        return None
    percent = _int_or_none(data.get("percent"))
    if percent is not None and not 0 <= percent <= 100:
        percent = None
    error = data.get("error")
    record: dict[str, Any] = {
        "state": state,
        "phase": _as_phase(data.get("phase")),
        "percent": percent,
        "error": error[:500] if isinstance(error, str) else None,
        "started_at": _int_or_none(data.get("started_at")),
    }
    last = _int_or_none(data.get("last_attempt"))
    if last is not None:
        record["last_attempt"] = last
    target = data.get("target")
    if isinstance(target, str) and len(target) <= 64:
        record["target"] = target
    return record


def read_helper_status() -> dict[str, Any] | None:
    """What the root helper last published: status.json, else the legacy job file."""
    status = _normalize_record(_read_json_object(status_path()), allow_cooldown=True)
    if status is not None:
        return status
    # Pre-4.18 helpers (compose installs not yet migrated) write update-job.json.
    return _normalize_record(_read_json_object(job_path()))


def read_attempt() -> dict[str, Any] | None:
    return _normalize_record(_read_json_object(attempt_path()))


def read_job() -> dict[str, Any]:
    """Merge the app's attempt with the helper's status.

    The helper's record wins once it started after the request; until then (or
    when the app itself gave up) the attempt is what the UI shows.
    """
    attempt = read_attempt()
    helper = read_helper_status()
    merged = idle_job()
    if attempt is None:
        if helper is not None and helper.get("state") != "cooldown":
            merged.update(helper)
            if helper.get("started_at") is not None:
                merged["last_attempt"] = helper["started_at"]
        return merged
    merged.update(attempt)
    requested = attempt.get("started_at")
    helper_started = helper.get("started_at") if helper else None
    if (
        helper is not None
        and attempt.get("state") == "applying"
        and isinstance(requested, int)
        and isinstance(helper_started, int)
        and helper_started >= requested - STATUS_CLOCK_SKEW_SECONDS
    ):
        for key in _PUBLIC_JOB_KEYS:
            merged[key] = helper.get(key)
        if helper.get("state") == "cooldown":
            merged["state"] = "failed"
            merged["error"] = helper.get("error") or COOLDOWN_ERROR
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
    _write_job_file(attempt_path(), json.dumps(payload))
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
        logger.warning("Could not persist expired apply job at %s", attempt_path())
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
    """Enqueue the packaged oneshot via the path unit, or ``systemctl --no-block``."""
    if await package_path_watcher_active():
        write_request_file(PACKAGE_REQUEST_PATH)
        return
    before = (await _unit_properties()).get("ExecMainStartTimestampMonotonic", "")
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        "start",
        "--no-block",
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
    # --no-block returns 0 even when systemd refuses the start (start limit,
    # failed condition). Check that the oneshot really ran or is running so
    # the UI does not sit on "applying" until the 20 min expiry.
    await _check_helper_started(before)


async def _unit_properties() -> dict[str, str]:
    proc = await asyncio.create_subprocess_exec(
        "systemctl",
        "show",
        "--property=ActiveState,Result,ExecMainStartTimestampMonotonic",
        "meshloom-update.service",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    return dict(
        line.split("=", 1)
        for line in (out or b"").decode("utf-8", "replace").splitlines()
        if "=" in line
    )


async def _check_helper_started(before: str) -> None:
    """Raise unless the oneshot started after ``before`` (its previous start stamp)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + HELPER_START_CHECK_SECONDS
    props: dict[str, str] = {}
    while loop.time() < deadline:
        props = await _unit_properties()
        if props.get("ExecMainStartTimestampMonotonic", before) != before:
            return
        if props.get("ActiveState") in {"activating", "active"}:
            return
        await asyncio.sleep(0.5)
    reason = props.get("Result") or props.get("ActiveState") or "unknown"
    raise RuntimeError(f"meshloom-update.service did not start ({reason})")


def start_compose_helper() -> None:
    if not secure_compose_helper():
        # Pre-4.18 compose helper: it reads `target` back from update-job.json
        # and would re-apply a stale one. Without the file it resolves the
        # latest release itself. The file is in the app's own data directory.
        job_path().unlink(missing_ok=True)
    write_request_file(request_path())


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
