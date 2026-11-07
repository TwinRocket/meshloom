"""Poll Community for the latest Meshloom release and cache the badge state.

Community (``GET /v1/meshloom/latest``) is the only source: public, no JWT, asked
by every instance once every 6 h, joined or not. It can also say not to auto-apply
across a version (``auto_update_hold``): the update stays available by hand.

No answer means no update information. The last known status is kept, but nothing
is auto-applied on it: a hold published since would go unseen, and the root helpers
resolve their target on their own (``pkg/nfpm/apply-update``,
``scripts/setup/helpers/compose-update``), so ``latest`` is only what the app shows
and compares, never what gets installed.

Runs from app lifespan independently of radio connect / SKIP_POST_CONNECT_SYNC.
The frontend reads GET /api/updates; this is never stuffed into the WS health
event (that frame already fires every 60s for radio stats).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Any

from packaging.version import InvalidVersion, Version

from app.services.meshloom_community import fetch_meshloom_latest
from app.services.update_window import in_window, next_window_start
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

# Community caches the release for 24 h: polling faster would learn nothing.
UPDATE_POLL_INTERVAL_SECONDS = 6 * 3600
RELEASES_REPO_URL = "https://github.com/TwinRocket/meshloom"
MAX_HOLD_REASON_CHARS = 300

_poll_task: asyncio.Task | None = None
_window_timer_task: asyncio.Task | None = None
_latest_payload: dict[str, Any] | None = None
_checked_at: int | None = None
_last_check_failed = False
_warned_unparsable_hold = False


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


async def fetch_latest_release() -> dict[str, Any] | None:
    """The latest release from Community. None without a usable answer.

    An answer with no version (Community has not read the release yet) tells
    nothing, so it counts as no answer and never replaces what is known.
    """
    payload = await fetch_meshloom_latest()
    if payload is None or _payload_version(payload) is None:
        return None
    return {**payload, "source": "community"}


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
    if not isinstance(value, str):
        return None
    # The UI opens this link: only a release page of the public repository.
    url = value.strip()
    return url if url.startswith(f"{RELEASES_REPO_URL}/releases/") else None


def _payload_source(payload: dict[str, Any] | None) -> str | None:
    if not payload:
        return None
    value = payload.get("source")
    return value if value == "community" else None


def _hold_reason(hold: Any) -> dict[str, str] | None:
    """``{fr, en}`` when both are short non-blank strings, else None. Shown as plain text."""
    raw = hold.get("reason") if isinstance(hold, dict) else None
    if not isinstance(raw, dict):
        return None
    reason: dict[str, str] = {}
    for locale in ("fr", "en"):
        text = raw.get(locale)
        if not isinstance(text, str) or not text.strip():
            return None
        text = text.strip()
        if len(text) > MAX_HOLD_REASON_CHARS:
            return None
        reason[locale] = text
    return reason


def _release(version: Version) -> Version:
    """The version without pre/post/dev parts. Not ``.release``: (5, 0) must equal (5, 0, 0)."""
    return Version(version.base_version)


def _parse_hold_below(hold: Any) -> Version | None:
    below = hold.get("below") if isinstance(hold, dict) else None
    if not isinstance(below, str):
        return None
    try:
        return Version(_strip_leading_v(below))
    except InvalidVersion:
        return None


def _resolve_hold(
    payload: dict[str, Any] | None, current: str, latest: str
) -> tuple[bool, dict[str, str] | None]:
    """Whether ``auto_update_hold`` stops auto-apply from ``current`` to ``latest``.

    It applies when ``current < below <= latest``, comparing releases without their
    pre-release part: a 5.0.0-rc.3 instance is not below 5.0.0, and a 5.0.0-rc.1
    ``latest`` does reach it. A hold whose ``below`` cannot be read fails closed: it
    applies to any update that crosses a major version. Absent or null means no hold
    (Community may not send the field yet).
    """
    global _warned_unparsable_hold
    hold = payload.get("auto_update_hold") if payload else None
    if hold is None:
        return False, None
    try:
        current_version = Version(_strip_leading_v(current))
        latest_version = Version(_strip_leading_v(latest))
    except InvalidVersion:
        return False, None
    below = _parse_hold_below(hold)
    if below is None:
        if not _warned_unparsable_hold:
            _warned_unparsable_hold = True
            logger.warning("Unreadable auto_update_hold from Community: %.120r", hold)
        applies = latest_version.major > current_version.major
    else:
        applies = _release(current_version) < _release(below) <= _release(latest_version)
    return (True, _hold_reason(hold)) if applies else (False, None)


def _env_latest_override() -> tuple[str | None, str | None]:
    """Private spare-node pin. Never shipped in meshloom.env."""
    latest = (os.environ.get("MESHLOOM_UPDATE_LATEST") or "").strip()
    if not latest:
        return None, None
    html = (os.environ.get("MESHLOOM_UPDATE_HTML_URL") or "").strip() or None
    return latest, html


def _current_task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:  # no running loop (reset_oss_update_cache from a sync fixture)
        return None


def _cancel_window_timer() -> None:
    global _window_timer_task
    task = _window_timer_task
    _window_timer_task = None
    # The timer itself lands here through _maybe_auto_apply: cancelling the running
    # task would abort the apply it is about to start, at its first await.
    if task is not None and not task.done() and task is not _current_task():
        task.cancel()


async def _await_cancelled_window_timer() -> None:
    global _window_timer_task
    task = _window_timer_task
    _window_timer_task = None
    if task is None:
        return
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _schedule_window_apply(when: datetime) -> None:
    """Replace any pending one-shot timer so short windows are not missed."""
    global _window_timer_task
    _cancel_window_timer()
    delay = max(0.0, (when - datetime.now().astimezone()).total_seconds())

    async def _fire() -> None:
        try:
            await asyncio.sleep(delay)
            # The last check can be hours old: read any hold published since.
            await refresh_oss_update_cache()
            await _maybe_auto_apply()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled auto-apply failed")

    _window_timer_task = asyncio.create_task(_fire())


def reset_oss_update_cache() -> None:
    """Drop the in-memory catalogue (tests)."""
    global _latest_payload, _checked_at, _last_check_failed, _warned_unparsable_hold
    _latest_payload = None
    _checked_at = None
    _last_check_failed = False
    _warned_unparsable_hold = False
    _cancel_window_timer()


async def _maybe_notify_oss_update() -> None:
    """Web-Push a new catalogue version once. Independent of auto-apply."""
    status = get_update_status()
    if not status["update_available"]:
        return
    latest = status["latest"]
    if not isinstance(latest, str) or not latest:
        return

    from app.repository import AppSettingsRepository

    last = await AppSettingsRepository.get_last_notified_update_version()
    if last == latest:
        return

    from app.push.manager import push_manager

    sent = await push_manager.dispatch_oss_update(status["current"], latest)
    if sent:
        await AppSettingsRepository.set_last_notified_update_version(latest)


async def refresh_oss_update_cache() -> dict[str, Any] | None:
    """Ask Community and cache the answer. Without one, the last status stays."""
    global _latest_payload, _checked_at, _last_check_failed
    try:
        payload = await fetch_latest_release()
    except Exception:
        logger.exception("Latest release lookup failed")
        payload = None
    _last_check_failed = payload is None
    if payload is not None:
        _latest_payload = payload
        _checked_at = int(time.time())
        try:
            await _maybe_notify_oss_update()
        except Exception:
            logger.debug("OSS update push notify failed", exc_info=True)
    return payload


def get_update_status() -> dict[str, Any]:
    current = get_app_build_info().version
    env_latest, env_html = _env_latest_override()
    latest = env_latest or _payload_version(_latest_payload)
    html_url = env_html or _payload_html_url(_latest_payload)
    update_available = (
        latest is not None
        and not is_unknown_local_version(current)
        and is_newer_release(latest, current)
    )
    held, hold_reason = (
        _resolve_hold(_latest_payload, current, latest)
        if update_available and latest is not None
        else (False, None)
    )
    return {
        "current": current,
        "latest": latest,
        "update_available": update_available,
        "html_url": html_url,
        "checked_at": _checked_at,
        "source": "env" if env_latest else _payload_source(_latest_payload),
        "auto_update_held": held,
        "auto_update_hold_reason": hold_reason,
        # The operator's pin does not depend on Community answering.
        "check_stale": _last_check_failed and not env_latest,
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
    if status["auto_update_held"] or status["check_stale"]:
        _cancel_window_timer()
        return
    kind, supported = detect_install_kind()
    if not supported:
        return
    try:
        settings = await AppSettingsRepository.get()
    except Exception:
        return
    if not settings.auto_update:
        _cancel_window_timer()
        return

    now = datetime.now().astimezone()
    if not in_window(
        now,
        settings.auto_update_window_start,
        settings.auto_update_window_end,
        settings.auto_update_weekdays,
    ):
        nxt = next_window_start(
            now,
            settings.auto_update_window_start,
            settings.auto_update_window_end,
            settings.auto_update_weekdays,
        )
        if nxt is not None:
            _schedule_window_apply(nxt)
        else:
            _cancel_window_timer()
        return
    _cancel_window_timer()

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
        await asyncio.sleep(UPDATE_POLL_INTERVAL_SECONDS)


async def start_oss_update_polling() -> None:
    """Start the catalogue poll (every 6 h). Fetches once on start."""
    global _poll_task
    if _poll_task is not None and not _poll_task.done():
        return
    _poll_task = asyncio.create_task(_oss_update_loop())


async def stop_oss_update_polling() -> None:
    """Stop the catalogue poll."""
    global _poll_task
    await _await_cancelled_window_timer()
    if _poll_task is None:
        return
    if not _poll_task.done():
        _poll_task.cancel()
        try:
            await _poll_task
        except asyncio.CancelledError:
            pass
    _poll_task = None
