"""Poll Meshloom Stats for the latest OSS release and cache the badge state.

Runs from app lifespan independently of radio connect / SKIP_POST_CONNECT_SYNC.
The frontend reads GET /api/updates; this is never stuffed into the WS health
event (that frame already fires every 60s for radio stats).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from packaging.version import InvalidVersion, Version

from app.services.meshloom_community import fetch_meshloom_latest
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

UPDATE_POLL_INTERVAL_SECONDS = 300

_poll_task: asyncio.Task | None = None
_latest_payload: dict[str, Any] | None = None


def _strip_leading_v(raw: str) -> str:
    text = raw.strip()
    if len(text) > 1 and text[0] in {"v", "V"} and text[1].isdigit():
        return text[1:]
    return text


def is_unknown_local_version(version: str) -> bool:
    """Dev fallback ``0.0.0`` never advertises an update."""
    return _strip_leading_v(version) == "0.0.0"


def is_newer_release(latest: str, current: str) -> bool:
    """True when ``latest`` is a SemVer greater than ``current``."""
    try:
        return Version(_strip_leading_v(latest)) > Version(_strip_leading_v(current))
    except InvalidVersion:
        return False


def _payload_version(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    value = payload.get("version")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _payload_html_url(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    value = payload.get("html_url")
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def reset_oss_update_cache() -> None:
    """Drop the in-memory catalogue (tests)."""
    global _latest_payload
    _latest_payload = None


async def refresh_oss_update_cache() -> dict[str, Any] | None:
    """Fetch Stats catalogue and replace the cache on success."""
    global _latest_payload
    payload = await fetch_meshloom_latest()
    if payload is not None:
        _latest_payload = payload
    return payload


def get_update_status() -> dict[str, Any]:
    current = get_app_build_info().version
    latest = _payload_version(_latest_payload)
    html_url = _payload_html_url(_latest_payload)
    update_available = (
        latest is not None
        and not is_unknown_local_version(current)
        and is_newer_release(latest, current)
    )
    return {
        "current": current,
        "latest": latest,
        "update_available": update_available,
        "html_url": html_url,
    }


async def _maybe_auto_apply() -> None:
    """Apply through the helper when the operator opted in. Meshloom only."""
    from app.repository import AppSettingsRepository
    from app.services.install_kind import detect_install_kind
    from app.services.update_apply import (
        UpdateApplyBusy,
        expire_stale_applying_job,
        job_is_applying,
        last_attempt_recent,
        start_apply,
    )

    status = get_update_status()
    if not status["update_available"]:
        return
    kind, supported = detect_install_kind()
    if not supported:
        return
    try:
        if not (await AppSettingsRepository.get()).auto_update:
            return
    except Exception:
        return
    job = expire_stale_applying_job()
    if job_is_applying(job):
        return
    if job.get("state") == "succeeded":
        if job.get("target") == status["latest"]:
            return
        if last_attempt_recent(job):
            return
    if job.get("state") == "failed" and last_attempt_recent(job):
        return
    try:
        await start_apply(kind, target=status["latest"])
    except UpdateApplyBusy:
        return


async def _oss_update_loop() -> None:
    while True:
        try:
            await refresh_oss_update_cache()
            await _maybe_auto_apply()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("OSS update poll failed")
        try:
            await asyncio.sleep(UPDATE_POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise


async def start_oss_update_polling() -> None:
    """Start the 300s catalogue poll. Fetches once immediately on start."""
    global _poll_task
    if _poll_task is not None and not _poll_task.done():
        return
    _poll_task = asyncio.create_task(_oss_update_loop())


async def stop_oss_update_polling() -> None:
    """Stop the catalogue poll."""
    global _poll_task
    if _poll_task is None:
        return
    if not _poll_task.done():
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    _poll_task = None
