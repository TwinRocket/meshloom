"""Wi-Fi settings of the connected radio, through the companion CLI.

The radio's Wi-Fi interface (companion firmware v1.18+ built with
``ENABLE_WIFI_INTERFACE``) is read and changed with the CLI helpers of
``app/services/companion_cli.py``. This service owns the CLI session: every
public function opens ``companion_cli_session()`` itself, so routers only
validate and map errors.

Rules:

- **Never a password in the database or a log.** The stored password is read
  only to learn whether credentials are stored (value discarded at once), by a
  full check (after each connect, its bounded retries, or a manual retry when no
  check completed) and inside ``store_wifi_credentials()`` (one read before, one
  read-back after). A refresh re-reads SSID, enabled, status and IP only.
- **Ask once per radio.** ``wifi_prompt_state`` (migration 095), keyed by the
  radio public key, records "don't ask again", an open network (which reads the
  same as "nothing stored") and a "store in progress" marker written before the
  first ``set`` and cleared once the store is verified or rolled back.
- **Planned reboots.** A reboot that applies Wi-Fi settings calls
  ``RadioManager.note_expected_reboot("wifi_config")``, so a transport that
  survives the reboot is dropped and setup (and this check) runs again.
- **Cache writes are ordered.** A state is written to the in-memory cache and
  broadcast while the session that computed it still holds the radio lock, only
  for the connection it was computed on, and only if no newer write landed.
- **No prompt during a restore.** While a full backup restore job runs,
  ``prompt_needed`` is held False; the job calls ``reevaluate_prompt()`` when it
  starts and ends, so the prompt closes and comes back (or not) accordingly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from pydantic import SecretStr

from app.auth.ws_events import WsEvent
from app.models import (
    RadioWifiChangeResult,
    RadioWifiCredentialsRequest,
    RadioWifiEnabledRequest,
    RadioWifiState,
)
from app.services.companion_cli import (
    WIFI_DEFAULT_TCP_PORT,
    CliCommandError,
    CliError,
    CliLinkState,
    CliTimeoutError,
    CliUnknownCommandError,
    CliUnsupportedError,
    CompanionCli,
    WifiStoreOutcome,
    companion_cli_session,
    set_wifi_enabled,
    store_wifi_credentials,
    validate_wifi_password,
    validate_wifi_ssid,
    wifi_credentials_stored,
    wifi_enabled,
    wifi_link_status,
    wifi_ssid,
)
from app.services.radio_runtime import radio_runtime

logger = logging.getLogger(__name__)

# Bounded automatic retries of a failed post-connect check (seconds after the
# failure; the first matches the companion CLI probe backoff).
POST_CONNECT_RETRY_DELAYS: tuple[float, ...] = (30.0, 120.0)
# Status/IP re-checks (no password) after a check found Wi-Fi enabled but not
# joined yet: the radio retries joining every 10 s (30 s on RP2040).
LINK_RECHECK_TIMES: tuple[float, ...] = (10.0, 30.0, 60.0)

_CAPABILITY_NO_CLI = frozenset(
    {
        "firmware_unknown",
        "firmware_too_old",
        "proxy_link",
        "untagged",
        "unsupported_cmd",
        "link_unreliable",
    }
)


class _StaleConnection(Exception):
    """A background check outlived the connection it was started for."""


class WifiRequestError(Exception):
    """A request the service refuses before touching the radio. ``code`` is stable."""

    def __init__(self, code: str, status: int = 422) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass
class _CacheEntry:
    state: RadioWifiState
    link: CliLinkState
    generation: int
    # The stored password read non-empty (before the open-network and marker
    # adjustments); None when no full check ran on this connection.
    raw_password_stored: bool | None
    # ``prompt_needed`` before the restore hold (see ``_prompt_held()``), so the
    # prompt can come back when the restore ends.
    prompt_wanted: bool = False


_cache: _CacheEntry | None = None
_version = 0  # bumped by every cache write and clear
_generation = 0  # bumped at every new connection (setup completed) and clear
_full_checks_running = 0  # full checks in flight (a manual retry can overlap a task's)
# Radios dismissed on the current connection. A dismissal runs without the radio
# lock, so instead of invalidating the sessions in flight (their result would then
# be lost), every later write applies it on top of the state it carries. Later
# connections read it from ``wifi_prompt_state``.
_dismissed_keys: set[str] = set()


def _manager() -> Any:
    return radio_runtime.manager


def clear_cache() -> None:
    """Forget the cached state (called on every transport teardown)."""
    global _cache, _version, _generation
    _cache = None
    _version += 1
    _generation += 1
    _dismissed_keys.clear()


def begin_connection() -> None:
    """A new setup completed: earlier checks and their tasks are stale."""
    clear_cache()


def _current_entry() -> _CacheEntry | None:
    entry = _cache
    if entry is None:
        return None
    if entry.link is not _manager().cli_link or entry.generation != _generation:
        return None
    return entry


def last_wifi_state() -> RadioWifiState | None:
    """The cached state of the current connection, if a check or change ran."""
    entry = _current_entry()
    return entry.state.model_copy() if entry is not None else None


@dataclass(frozen=True)
class _WriteToken:
    """Taken when a session starts: its write is refused if the connection
    changed or a newer write landed meanwhile."""

    version: int
    generation: int
    link: CliLinkState


def _token() -> _WriteToken:
    return _WriteToken(version=_version, generation=_generation, link=_manager().cli_link)


def _token_alive(token: _WriteToken) -> bool:
    return token.generation == _generation and token.link is _manager().cli_link


def _broadcast(state: RadioWifiState) -> None:
    from app.websocket import broadcast_event

    # WebSocket clients only: Wi-Fi state is local UI state, never for the radio
    # proxy, fanout modules or Web Push.
    broadcast_event(WsEvent.RADIO_WIFI, state.model_dump(mode="json"), realtime=False)


def _prompt_held() -> bool:
    """A full backup restore job is running: the prompt waits until it ends
    (its Wi-Fi step may store the credentials, and the prompt would sit on top
    of the restore view)."""
    from app.services.backup_restore import restore_job_running

    return restore_job_running()


def _write_cache(
    state: RadioWifiState, token: _WriteToken, *, raw_password_stored: bool | None
) -> bool:
    """Store and broadcast ``state`` if ``token`` is still current. Returns
    whether it was written; ``token`` must not be reused after a write.

    The dismissal and the restore hold are applied to ``state`` itself (the
    caller returns it), written or not."""
    global _cache, _version
    if state.radio_public_key in _dismissed_keys:
        state.prompt_needed = False
    wanted = state.prompt_needed
    if wanted and _prompt_held():
        state.prompt_needed = False
    if not _token_alive(token) or token.version != _version:
        logger.debug("Dropped a stale Wi-Fi state")
        return False
    _version += 1
    _cache = _CacheEntry(
        state=state,
        link=token.link,
        generation=token.generation,
        raw_password_stored=raw_password_stored,
        prompt_wanted=wanted,
    )
    _broadcast(state)
    return True


# -- state computation --------------------------------------------------------


def _now() -> int:
    return int(time.time())


def _normalize_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if len(key) != 64 or set(key) == {"0"}:
        return None
    try:
        bytes.fromhex(key)
    except ValueError:
        return None
    return key


async def connected_radio_public_key() -> str | None:
    """The connected radio's public key (setup completes only once it matches
    the bound identity)."""
    mc = _manager().meshcore
    info = getattr(mc, "self_info", None)
    if isinstance(info, dict):
        key = _normalize_key(info.get("public_key"))
        if key:
            return key
    from app.services.radio_transport import get_transport

    return _normalize_key((await get_transport()).bound_public_key)


def _unsupported_state(reason: str, public_key: str | None) -> RadioWifiState:
    return RadioWifiState(
        check="done",
        supported=False,
        unsupported_reason="no_wifi" if reason == "no_wifi" else "no_cli",
        radio_public_key=public_key,
        checked_at=_now(),
    )


def _failed_state(
    error: str, public_key: str | None, next_retry_at: int | None = None
) -> RadioWifiState:
    return RadioWifiState(
        check="failed",
        check_error=error,  # type: ignore[arg-type]
        next_retry_at=next_retry_at,
        radio_public_key=public_key,
        checked_at=_now(),
    )


async def _prompt_entry(public_key: str | None) -> dict[str, Any]:
    if not public_key:
        return {}
    from app.repository import AppSettingsRepository

    return (await AppSettingsRepository.get_wifi_prompt_state()).get(public_key, {})


async def _compose_state(
    *,
    public_key: str | None,
    ssid: str | None,
    enabled: bool,
    connected: bool,
    ip: str | None,
    raw_password_stored: bool | None,
    reboot_pending: bool,
) -> RadioWifiState:
    entry = await _prompt_entry(public_key)
    open_record = entry.get("open_network_ssid")
    store_incomplete = "store_started_at" in entry
    target = entry.get("store_target_ssid")
    credentials_stored = raw_password_stored
    open_network = False
    if raw_password_stored is False and isinstance(open_record, str) and open_record == ssid:
        credentials_stored = True
        open_network = True
    if store_incomplete:
        # After a dropped link the radio may hold the new SSID with the old
        # password, which reads as stored: never trust it until a verified store.
        credentials_stored = False
        open_network = False
    dismissed = "dismissed_at" in entry
    prompt_needed = bool(
        enabled
        and ssid is not None
        and credentials_stored is not None
        and (not credentials_stored or store_incomplete)
        and not dismissed
    )
    return RadioWifiState(
        check="done",
        supported=True,
        ssid=ssid,
        enabled=enabled,
        connected=connected,
        ip=ip,
        tcp_port=WIFI_DEFAULT_TCP_PORT,
        credentials_stored=credentials_stored,
        open_network=open_network,
        prompt_needed=prompt_needed,
        reboot_pending=reboot_pending,
        store_incomplete=store_incomplete,
        store_target_ssid=target if store_incomplete and isinstance(target, str) else None,
        radio_public_key=public_key,
        checked_at=_now(),
    )


async def _read_wifi_state(
    cli: CompanionCli,
    *,
    public_key: str | None,
    read_password: bool,
    raw_password_stored: bool | None = None,
    reboot_pending: bool = False,
) -> tuple[RadioWifiState, bool | None]:
    """Read the Wi-Fi state. With ``read_password`` the stored password is read
    (and discarded); otherwise ``raw_password_stored`` is reused."""
    link = cli.link
    cached = link.capabilities
    refresh = cached is not None and cached.reason == "probe_failed"
    caps = await cli.capabilities(refresh=refresh)
    if not caps.supported:
        if caps.reason == "probe_failed":
            return _failed_state("probe_failed", public_key), None
        return _unsupported_state("no_cli", public_key), None
    if not caps.wifi:
        return _unsupported_state("no_wifi", public_key), None
    try:
        ssid = await wifi_ssid(cli)
        enabled = await wifi_enabled(cli)
        status = await wifi_link_status(cli)
        if read_password:
            raw_password_stored = await wifi_credentials_stored(cli)
    except CliUnknownCommandError:
        return _unsupported_state("no_wifi", public_key), None
    except CliUnsupportedError:
        return _unsupported_state("no_cli", public_key), None
    state = await _compose_state(
        public_key=public_key,
        ssid=ssid,
        enabled=enabled,
        connected=status.connected,
        ip=status.ip,
        raw_password_stored=raw_password_stored,
        reboot_pending=reboot_pending,
    )
    return state, raw_password_stored


async def _require_wifi(cli: CompanionCli) -> None:
    link = cli.link
    retry = link.capabilities is not None and link.capabilities.reason == "probe_failed"
    caps = await cli.capabilities(refresh=retry)
    if not caps.supported:
        if caps.reason == "probe_failed":
            # The radio did not answer the probe: transient, not "no CLI".
            raise CliTimeoutError("cli_timeout", verb="get", name="wifi.status")
        raise CliUnsupportedError(caps.reason)
    if not caps.wifi:
        raise CliUnknownCommandError("cli_unknown_command", verb="get", name="wifi.status")


def wifi_reboot_required(
    outcome: WifiStoreOutcome,
    *,
    connected: bool,
    open_record_ssid: str | None,
    new_password_empty: bool,
) -> bool:
    """Whether the stored credentials differ from what the radio runs on.

    When credentials were stored (a non-empty password, or an open network
    Meshloom recorded for the previous SSID), old and new values compare. When
    nothing was stored the radio runs on the compile-time credentials, which
    cannot be read: storing the SSID it is joined to right now, with a password
    (the flasher case), needs no reboot; anything else does. Ticking "open
    network" for the running SSID may not match the compiled password, so it
    always counts as a change.
    """
    if outcome.previously_stored or (
        open_record_ssid is not None and open_record_ssid == outcome.previous_ssid
    ):
        return outcome.ssid_changed or outcome.password_changed
    flasher_shortcut = not outcome.ssid_changed and connected and not new_password_empty
    return not flasher_shortcut


async def _send_wifi_reboot() -> None:
    """Reboot the radio to apply Wi-Fi settings; called with the radio lock held."""
    manager = _manager()
    mc = manager.meshcore
    if mc is None:
        raise CliError("setup_incomplete")
    logger.info("Rebooting the radio to apply its Wi-Fi settings")
    await mc.commands.reboot()
    manager.note_expected_reboot("wifi_config")


# -- public API ---------------------------------------------------------------


def _validated_credentials(request: RadioWifiCredentialsRequest) -> tuple[str, str]:
    try:
        ssid = validate_wifi_ssid(request.ssid)
    except CliCommandError:
        raise WifiRequestError("wifi_invalid_ssid") from None
    password = request.password.get_secret_value() if request.password is not None else ""
    if request.open_network == bool(password):
        # Open network with a password, or a protected one without.
        raise WifiRequestError("wifi_invalid_password")
    try:
        password = validate_wifi_password(password, typed=True)
    except CliCommandError:
        raise WifiRequestError("wifi_invalid_password") from None
    return ssid, password


async def apply_wifi_credentials(
    request: RadioWifiCredentialsRequest, *, public_key: str
) -> RadioWifiChangeResult:
    """Store Wi-Fi credentials in the radio (verified, rolled back on failure).

    Raises ``WifiRequestError`` for an invalid request (nothing sent),
    ``CliError`` subclasses for radio failures (``wifi_store_unverified``,
    ``wifi_store_partial``, timeouts, no CLI or no Wi-Fi).
    """
    ssid, password = _validated_credentials(request)
    async with companion_cli_session("wifi_credentials") as cli:
        token = _token()
        await _require_wifi(cli)
        # Read before writing: stored credentials apply only after a reboot, so
        # these do not change, and nothing is left to read once the store is done.
        enabled = await wifi_enabled(cli)
        status = await wifi_link_status(cli)
        open_record = (await _prompt_entry(public_key)).get("open_network_ssid")
        outcome = await _store_with_marker(
            cli,
            token,
            public_key,
            ssid,
            SecretStr(password),
            open_network=request.open_network,
            typed=True,
        )
        raw = bool(password)
        password = ""
        previous = _current_entry()
        state = await _compose_state(
            public_key=public_key,
            ssid=ssid,
            enabled=enabled,
            connected=status.connected,
            ip=status.ip,
            raw_password_stored=raw,
            reboot_pending=previous.state.reboot_pending if previous else False,
        )
        required = wifi_reboot_required(
            outcome,
            connected=status.connected,
            open_record_ssid=open_record if isinstance(open_record, str) else None,
            new_password_empty=request.open_network,
        )
        return await _finish_change(state, raw, token, required=required, reboot=request.reboot)


async def _store_with_marker(
    cli: CompanionCli,
    token: _WriteToken,
    public_key: str | None,
    ssid: str,
    password: SecretStr,
    *,
    open_network: bool,
    typed: bool,
) -> WifiStoreOutcome:
    """``store_wifi_credentials()`` with this service's bookkeeping: the "store
    in progress" marker around it, then the open-network record (set for an
    open network, dropped for a password). Without a known public key nothing
    can be recorded, and the store runs alone."""
    from app.repository import AppSettingsRepository

    if public_key is None:
        return await store_wifi_credentials(cli, ssid, password, typed=typed)
    await AppSettingsRepository.mark_wifi_store_started(public_key, ssid)
    try:
        outcome = await store_wifi_credentials(cli, ssid, password, typed=typed)
    except asyncio.CancelledError:
        # Cancelled at an unknown step: keep the marker (the safe reading).
        raise
    except CliError as exc:
        if exc.code == "wifi_store_partial":
            # Marker kept: the warning survives the link drop that usually
            # causes this, and the cache clear that follows it.
            await _note_store_incomplete(token, public_key, ssid)
        else:
            # Verified rollback, or nothing written at all.
            await AppSettingsRepository.clear_wifi_store_marker(public_key)
        raise
    except Exception:
        # store_wifi_credentials() turns every failure after its first ``set``
        # into a CliError, so anything else (a dropped link during its first
        # reads) means nothing was written.
        await AppSettingsRepository.clear_wifi_store_marker(public_key)
        raise
    await AppSettingsRepository.clear_wifi_store_marker(public_key)
    if open_network:
        await AppSettingsRepository.mark_wifi_open_network(public_key, ssid)
    else:
        await AppSettingsRepository.clear_wifi_open_network(public_key)
    return outcome


async def _finish_change(
    state: RadioWifiState,
    raw: bool | None,
    token: _WriteToken,
    *,
    required: bool,
    reboot: bool,
) -> RadioWifiChangeResult:
    """Write the new state, then reboot when required and confirmed (lock held)."""
    rebooting = False
    if required and reboot:
        state.reboot_pending = False
        _write_cache(state, token, raw_password_stored=raw)
        await _send_wifi_reboot()
        rebooting = True
    else:
        state.reboot_pending = state.reboot_pending or required
        _write_cache(state, token, raw_password_stored=raw)
    return RadioWifiChangeResult(state=state, reboot_required=required, rebooting=rebooting)


async def _note_store_incomplete(token: _WriteToken, public_key: str, ssid: str) -> None:
    """Mark the cached state after a partial store (the link is usually gone)."""
    entry = _current_entry()
    if entry is None:
        return
    state = entry.state.model_copy()
    state.store_incomplete = True
    state.store_target_ssid = ssid
    state.credentials_stored = False
    state.open_network = False
    dismissed = "dismissed_at" in await _prompt_entry(public_key)
    state.prompt_needed = bool(state.enabled and state.ssid is not None and not dismissed)
    _write_cache(state, token, raw_password_stored=entry.raw_password_stored)


async def apply_wifi_enabled(
    request: RadioWifiEnabledRequest, *, public_key: str
) -> RadioWifiChangeResult:
    """Turn the radio's Wi-Fi on or off (applies after a reboot).

    Enabling with no network name stored (``ssid`` None) stores the setting but
    needs no reboot: the firmware starts Wi-Fi only once an SSID is stored.
    """
    async with companion_cli_session("wifi_enabled") as cli:
        token = _token()
        await _require_wifi(cli)
        ssid = await wifi_ssid(cli)
        was_enabled = await wifi_enabled(cli)
        status = await wifi_link_status(cli)
        await set_wifi_enabled(cli, request.enabled)
        previous = _current_entry()
        raw = previous.raw_password_stored if previous else None
        state = await _compose_state(
            public_key=public_key,
            ssid=ssid,
            enabled=request.enabled,
            connected=status.connected,
            ip=status.ip,
            raw_password_stored=raw,
            reboot_pending=previous.state.reboot_pending if previous else False,
        )
        required = was_enabled != request.enabled and ssid is not None
        return await _finish_change(state, raw, token, required=required, reboot=request.reboot)


async def reboot_for_wifi_config() -> RadioWifiChangeResult:
    """Reboot the radio to apply stored Wi-Fi settings (a user-confirmed action).

    Allowed whenever the radio is connected and set up, with or without a
    pending change, so the prompt's follow-up, the panel's "Reboot now" and a
    restore share it. Unlike ``POST /radio/reboot`` it records the planned
    reboot (``wifi_config``).
    """
    manager = _manager()
    if not manager.is_setup_complete:
        raise CliError("setup_incomplete")
    async with radio_runtime.radio_operation("wifi_reboot") as mc:
        if not manager.is_setup_complete or mc is not manager.meshcore:
            raise CliError("setup_incomplete")
        entry = _current_entry()
        state = entry.state.model_copy() if entry is not None else RadioWifiState()
        state.reboot_pending = False
        await _send_wifi_reboot()
    return RadioWifiChangeResult(state=state, reboot_required=False, rebooting=True)


# -- full backup restore (inside the restore's own CLI session) --------------


async def store_restored_credentials(
    cli: CompanionCli, ssid: str, password: SecretStr, *, open_network: bool
) -> bool:
    """Store credentials from a full backup, inside the restore's CLI session,
    with the same bookkeeping as a store from the Wi-Fi panel (marker, then the
    open-network record). Returns whether a reboot is needed to apply them
    (``wifi_reboot_required()``). Raises like ``store_wifi_credentials()``.

    Writes no state: the restore calls ``refresh_after_restore()`` once its
    Wi-Fi step is done (the on/off flag comes after the credentials).
    """
    public_key = await connected_radio_public_key()
    token = _token()
    try:
        connected = (await wifi_link_status(cli)).connected
    except CliError:
        connected = False  # unknown: the reboot rule then asks for one
    open_record = (await _prompt_entry(public_key)).get("open_network_ssid")
    outcome = await _store_with_marker(
        cli, token, public_key, ssid, password, open_network=open_network, typed=False
    )
    return wifi_reboot_required(
        outcome,
        connected=connected,
        open_record_ssid=open_record if isinstance(open_record, str) else None,
        new_password_empty=open_network,
    )


async def refresh_after_restore(
    cli: CompanionCli, *, credentials_stored: bool | None, reboot_pending: bool
) -> RadioWifiState:
    """Re-read the Wi-Fi state after the restore's Wi-Fi step, inside the
    restore's CLI session, then write and broadcast it (the panel and the
    indicator then show the restored values; the prompt stays held until the
    job ends).

    ``credentials_stored`` is the raw "password stored" reading the step knows
    (True for a stored password, False for an open network, None when it stored
    no credentials: the cached reading is kept, or the password is read once
    when none is cached). ``reboot_pending`` says the step stored a change that
    needs a reboot. A read failure writes a ``failed`` state (the panel then
    offers Retry) instead of raising.
    """
    token = _token()
    public_key = await connected_radio_public_key()
    previous = _current_entry()
    raw = credentials_stored
    if raw is None and previous is not None:
        raw = previous.raw_password_stored
    pending = reboot_pending or (previous.state.reboot_pending if previous else False)
    try:
        state, raw = await _read_wifi_state(
            cli,
            public_key=public_key,
            read_password=raw is None,
            raw_password_stored=raw,
            reboot_pending=pending,
        )
    except CliTimeoutError:
        state, raw = _failed_state("cli_timeout", public_key), None
    except CliError:
        state, raw = _failed_state("cli_error", public_key), None
    _write_cache(state, token, raw_password_stored=raw)
    return state


async def dismiss_wifi_prompt(radio_public_key: str) -> RadioWifiState:
    """Never ask again for this radio. Refused (``wifi_radio_changed``) when the
    connected radio is not the one the dialog was shown for."""
    from app.repository import AppSettingsRepository

    shown = _normalize_key(radio_public_key)
    connected = await connected_radio_public_key()
    if shown is None or connected is None or shown != connected:
        raise WifiRequestError("wifi_radio_changed", status=409)
    await AppSettingsRepository.mark_wifi_prompt_dismissed(connected)
    _dismissed_keys.add(connected)
    entry = _current_entry()
    if entry is None:
        return RadioWifiState(radio_public_key=connected)
    # Patched in place without bumping ``_version``: a session in flight (a store
    # in another tab) still writes its result, with the dismissal applied.
    state = entry.state.model_copy()
    state.prompt_needed = False
    entry.state = state
    entry.prompt_wanted = False
    _broadcast(state)
    return state.model_copy()


def reevaluate_prompt() -> None:
    """A restore job started or ended: hold the prompt, or let it come back.

    Patches the cached state in place without bumping ``_version`` (like a
    dismissal): a session in flight still writes its own result, and
    ``_write_cache()`` applies the hold as it stands then. Broadcasts only a
    change, so every tab closes or reopens the prompt."""
    entry = _current_entry()
    if entry is None:
        return
    needed = (
        entry.prompt_wanted
        and entry.state.radio_public_key not in _dismissed_keys
        and not _prompt_held()
    )
    if entry.state.prompt_needed == needed:
        return
    state = entry.state.model_copy()
    state.prompt_needed = needed
    entry.state = state
    _broadcast(state)


async def run_full_wifi_check(
    *,
    blocking: bool = True,
    next_retry_at: int | None = None,
    expect: _WriteToken | None = None,
) -> RadioWifiState:
    """One full check (stored password read included); writes and broadcasts.

    A timeout or another CLI failure gives ``check="failed"`` (with
    ``next_retry_at`` when the caller schedules a retry) instead of raising.
    With ``expect`` (the connection a background task belongs to) nothing is
    written once the connection changed.
    """
    global _full_checks_running
    _full_checks_running += 1
    try:
        async with companion_cli_session("wifi_check", blocking=blocking) as cli:
            token = _token()
            if expect is not None and (
                token.generation != expect.generation or token.link is not expect.link
            ):
                raise _StaleConnection()
            public_key = await connected_radio_public_key()
            previous = _current_entry()
            reboot_pending = previous.state.reboot_pending if previous else False
            raw: bool | None = None
            try:
                state, raw = await _read_wifi_state(
                    cli,
                    public_key=public_key,
                    read_password=True,
                    reboot_pending=reboot_pending,
                )
            except CliTimeoutError:
                state = _failed_state("cli_timeout", public_key)
            except CliError as exc:
                if exc.code == "setup_incomplete":
                    raise
                state = _failed_state("cli_error", public_key)
            if state.check == "failed":
                state.next_retry_at = next_retry_at
            _write_cache(state, token, raw_password_stored=raw)
            return state
    finally:
        _full_checks_running -= 1


async def refresh_wifi_state() -> RadioWifiState:
    """Re-read SSID, enabled, status and IP (no password), keeping the cached
    ``credentials_stored``. Runs a full check when none completed on this
    connection (unless one is running)."""
    entry = _current_entry()
    if entry is None or entry.state.check != "done" or entry.raw_password_stored is None:
        if entry is not None and entry.state.check == "done" and not entry.state.supported:
            return entry.state.model_copy()
        if _full_checks_running:
            return entry.state.model_copy() if entry is not None else RadioWifiState()
        return await run_full_wifi_check()
    async with companion_cli_session("wifi_refresh") as cli:
        token = _token()
        entry = _current_entry()
        raw = entry.raw_password_stored if entry is not None else None
        reboot_pending = entry.state.reboot_pending if entry is not None else False
        public_key = await connected_radio_public_key()
        state, raw = await _read_wifi_state(
            cli,
            public_key=public_key,
            read_password=raw is None,
            raw_password_stored=raw,
            reboot_pending=reboot_pending,
        )
        _write_cache(state, token, raw_password_stored=raw)
        return state


async def get_wifi_state(*, refresh: bool) -> RadioWifiState:
    """GET /api/radio/wifi: the cached state (``pending`` when none yet), or a
    live read with ``refresh``."""
    if refresh:
        return await refresh_wifi_state()
    state = last_wifi_state()
    return state if state is not None else RadioWifiState(check="pending")


async def _merge_link_status(token: _WriteToken, connected: bool, ip: str | None) -> bool:
    """Merge only ``connected``/``ip`` into the cached state (re-checks never
    write a whole snapshot). Returns whether it changed."""
    global _version
    entry = _current_entry()
    if entry is None or not _token_alive(token):
        return False
    if entry.state.connected == connected and entry.state.ip == ip:
        return False
    state = entry.state.model_copy()
    state.connected = connected
    state.ip = ip
    _version += 1
    entry.state = state
    _broadcast(state)
    return True


# -- after each connect -------------------------------------------------------


def start_post_connect_check() -> None:
    """Called once post-connect setup completed: forget the previous
    connection's state and spawn the Wi-Fi check (tracked by ``spawn()``),
    bound to this connection from the moment it is spawned."""
    from app.background_tasks import spawn

    begin_connection()
    spawn(run_wifi_post_connect_check(_token()), name="wifi_post_connect")


def _completed_check() -> RadioWifiState | None:
    """The current connection's state when a full check already completed on it
    (a manual retry, or a store): a pending automatic retry is then moot."""
    entry = _current_entry()
    if entry is None or entry.state.check != "done" or entry.raw_password_stored is None:
        return None
    return entry.state.model_copy()


async def run_wifi_post_connect_check(expect: _WriteToken | None = None) -> None:
    """The full check for a new connection, with bounded retries, then status
    re-checks while Wi-Fi is enabled but not joined yet.

    Retries (``POST_CONNECT_RETRY_DELAYS`` after a failure) and re-checks
    (``LINK_RECHECK_TIMES`` after the check, no password, each in its own short
    non-blocking session, skipped when the radio is busy) live in this one task.
    It stops, without writing or broadcasting, as soon as the connection it was
    started for is gone. A retry is skipped once a full check completed on this
    connection meanwhile. It never stores credentials.

    ``expect`` is the connection the task was spawned for (taken now when omitted).
    """
    from app.radio import RadioDisconnectedError, RadioOperationBusyError

    if expect is None:
        expect = _token()

    def alive() -> bool:
        return _token_alive(expect) and bool(_manager().is_setup_complete)

    delays = list(POST_CONNECT_RETRY_DELAYS)
    state: RadioWifiState | None = None
    while True:
        if not alive():
            return
        next_retry_at = _now() + int(delays[0]) if delays else None
        try:
            state = await run_full_wifi_check(next_retry_at=next_retry_at, expect=expect)
        except (_StaleConnection, CliError, RadioDisconnectedError):
            return
        except Exception:
            logger.exception("Wi-Fi check after connect failed")
            return
        if state.check != "failed":
            break
        if not delays:
            logger.info("Wi-Fi check failed (%s); no automatic retry left", state.check_error)
            return
        delay = delays.pop(0)
        logger.info("Wi-Fi check failed (%s); retrying in %ds", state.check_error, delay)
        await asyncio.sleep(delay)
        if alive() and (completed := _completed_check()) is not None:
            # A manual retry (or a store) completed a check meanwhile: no second
            # password read.
            state = completed
            break

    if not (state.supported and state.enabled and state.ssid is not None and not state.connected):
        return
    loop = asyncio.get_running_loop()
    started = loop.time()
    for at in LINK_RECHECK_TIMES:
        await asyncio.sleep(max(0.0, started + at - loop.time()))
        if not alive():
            return
        try:
            async with companion_cli_session("wifi_link_recheck", blocking=False) as cli:
                token = _token()
                if token.generation != expect.generation or token.link is not expect.link:
                    return
                status = await wifi_link_status(cli)
                await _merge_link_status(token, status.connected, status.ip)
        except (RadioOperationBusyError, CliTimeoutError):
            continue  # busy, or no answer this time: the next re-check may get one
        except (CliError, RadioDisconnectedError):
            return
        except Exception:
            logger.exception("Wi-Fi status re-check failed")
            return
        if status.connected:
            return
