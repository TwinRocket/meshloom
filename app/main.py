import logging
import sys

# ---------------------------------------------------------------------------
# Windows event-loop advisory for MQTT fanout
# ---------------------------------------------------------------------------
# On Windows, uvicorn's default event loop (ProactorEventLoop) does not
# implement add_reader()/add_writer(), which paho-mqtt (via aiomqtt) requires.
# We cannot fix this from inside the app — the loop is already created by the
# time this module is imported.  Log a prominent warning so Windows operators
# who want MQTT know to add ``--loop none`` to their uvicorn command.
# ---------------------------------------------------------------------------
if sys.platform == "win32":
    import asyncio as _asyncio

    try:
        _loop = _asyncio.get_event_loop()
    except RuntimeError:
        _loop = None
    _is_proactor = _loop is not None and type(_loop).__name__ == "ProactorEventLoop"
    if _is_proactor:
        print(
            "\n" + "!" * 78 + "\n"
            "  NOTE FOR WINDOWS USERS\n" + "!" * 78 + "\n"
            "\n"
            "  The running event loop is ProactorEventLoop, which is not\n"
            "  compatible with MQTT fanout (aiomqtt / paho-mqtt).\n"
            "\n"
            "  If you use MQTT integrations, restart with --loop none:\n"
            "\n"
            "    uv run uvicorn app.main:app \033[1m--loop none\033[0m"
            " [... other options ...]\n"
            "\n"
            "  Everything else works fine as-is.\n"
            "\n" + "!" * 78 + "\n",
            file=sys.stderr,
            flush=True,
        )
    del _loop, _is_proactor

import asyncio
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from app.api_docs import API_DESCRIPTION, API_TAGS_METADATA, register_api_docs_routes
from app.auth.middleware import AuthMiddleware
from app.auth.permissions import enforce_declaration
from app.config import settings as server_settings
from app.config import setup_logging
from app.cors import AllowedOriginsCORSMiddleware
from app.database import db
from app.frontend_static import (
    register_first_available_frontend_static_routes,
    register_frontend_missing_fallback,
)
from app.middleware import proxy_headers
from app.middleware.host_guard import HostGuardMiddleware
from app.middleware.proxy_headers import TrustedProxyHeadersMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.radio import RadioDisconnectedError
from app.radio_sync import (
    stop_background_contact_reconciliation,
    stop_message_polling,
    stop_periodic_advert,
    stop_periodic_sync,
    stop_telemetry_collect,
)
from app.routers import (
    auth,
    auth_totp,
    auth_webauthn,
    channels,
    community,
    contact_groups,
    contacts,
    debug,
    directory,
    fanout,
    health,
    locate,
    messages,
    notifications,
    packets,
    plugins,
    push,
    radio,
    radio_wifi,
    read_state,
    repeaters,
    rooms,
    settings,
    share,
    statistics,
    tools,
    updates,
    ws,
)
from app.services.hardening import set_not_dumpable
from app.services.hashtag_catalogue import (
    start_hashtag_catalogue_polling,
    stop_hashtag_catalogue_polling,
)
from app.services.oss_updates import start_oss_update_polling, stop_oss_update_polling
from app.services.radio_lifecycle import expected_reboot_reason, reboot_purpose
from app.services.radio_runtime import radio_runtime as radio_manager
from app.services.radio_stats import start_radio_stats_sampling, stop_radio_stats_sampling
from app.services.stale_contacts import start_stale_contact_purge, stop_stale_contact_purge
from app.version_info import get_app_build_info

setup_logging()
logger = logging.getLogger(__name__)


async def _startup_radio_connect_and_setup() -> None:
    """Connect/setup the radio in the background so HTTP serving can start immediately."""
    started = time.monotonic()
    connected = False
    try:
        connected = await _connect_radio_on_startup()
    except asyncio.CancelledError:
        # Shutting down: the start never completed, nothing to announce.
        raise
    except Exception:
        logger.exception("Failed to connect to radio on startup")
    # Startup is complete (radio connected or not): tell the user once.
    from app.notifications.radio_watch import radio_watch

    radio_watch.note_startup_complete(
        version=get_app_build_info().version,
        radio_connected=connected,
        connect_s=time.monotonic() - started if connected else None,
    )


async def _connect_radio_on_startup() -> bool:
    if not radio_manager.connection_desired:
        logger.info("Radio connection not desired; skipping startup connect")
        return False
    from app.services.radio_transport import get_transport

    snapshot = await get_transport()
    if not snapshot.configured:
        logger.info("Radio transport is not configured; skipping startup connect")
        return False
    connected = await radio_manager.reconnect_and_prepare(broadcast_on_success=True)
    if connected:
        logger.info("Connected to radio")
    elif (reason := expected_reboot_reason(radio_manager)) is not None:
        logger.info(
            "Radio is rebooting %s; the connection monitor will reconnect it and finish setup",
            reboot_purpose(reason),
        )
    else:
        logger.warning("Failed to connect to radio on startup")
    return bool(connected)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database and radio connection lifecycle."""
    set_not_dumpable()
    db_path = Path(server_settings.database_path)
    file_existed = db_path.is_file() and db_path.stat().st_size > 0

    await db.connect()
    logger.info("Database connected")

    # A SQLite snapshot put back in place: the restore hooks run once.
    from app.services.backup_sections import run_sqlite_restore_hooks

    try:
        await run_sqlite_restore_hooks(db)
    except Exception:
        logger.exception("Failed to run the restore hooks of a restored database snapshot")

    from app.services.radio_transport import (
        database_has_mesh_history,
        get_transport,
        maybe_import_legacy_env,
    )

    existing_database = file_existed
    if not existing_database:
        current = await get_transport()
        if not current.env_imported and await database_has_mesh_history():
            existing_database = True

    snapshot = await maybe_import_legacy_env(existing_database=existing_database)
    radio_manager.connection_desired = snapshot.configured

    from app.services.legacy_env_settings import import_legacy_community_iata

    try:
        await import_legacy_community_iata(existing_database=existing_database)
    except Exception:
        logger.exception("Failed to import the retired MESHLOOM_COMMUNITY_IATA")
    # Waiting for a position, as last known before the restart: no packet and
    # no Community call for a radio known to have none (migration 092).
    from app.services.meshloom_community import load_position_state

    try:
        await load_position_state()
    except Exception:
        logger.exception("Failed to load the Community waiting state")
    if not snapshot.configured:
        logger.info("Radio transport is not configured; connection attempts stay paused")

    # Retired MESHCORE_* settings (VAPID contact, radio workarounds): copied into
    # app_settings once, then only a warning while they (or a retired
    # MESHLOOM_COMMUNITY* variable) are still set.
    from app import origin_policy, radio_workarounds
    from app.services.legacy_env_settings import import_legacy_env_settings

    try:
        await import_legacy_env_settings()
    except Exception:
        logger.exception("Failed to import retired MESHCORE_* settings from the environment")
    try:
        await radio_workarounds.load()
    except Exception:
        # The workarounds stay off (their defaults) rather than block startup.
        logger.exception("Failed to load the radio workarounds from app_settings")
    try:
        await origin_policy.load()
    except Exception:
        # Same-origin only (the default) rather than block startup.
        logger.exception("Failed to load the allowed origins from app_settings")
    try:
        await proxy_headers.load()
    except Exception:
        # No trusted proxy (the default): forwarded headers stay ignored.
        logger.exception("Failed to load the trusted proxies from auth_settings")

    # Accounts: import the retired Basic credentials, or issue the installation
    # code on an existing database with no account yet (app/auth/setup_code.py).
    from app.auth.setup_code import prepare_first_run

    try:
        await prepare_first_run(
            existing_database=existing_database,
            basic_username=server_settings.basic_auth_username,
            basic_password=server_settings.basic_auth_password,
        )
    except Exception:
        # Fail closed: setup answers 503 until a retry from the setup route succeeds
        # (app/auth/setup_code.py), and every other route still needs an account.
        logger.exception("Failed to prepare the first-run account setup")

    # Initialize VAPID keys for Web Push (generates on first run)
    from app.push.vapid import ensure_vapid_keys

    try:
        await ensure_vapid_keys()
    except Exception:
        logger.warning("Failed to initialize VAPID keys for Web Push", exc_info=True)

    # Ensure default channels exist in the database even before the radio
    # connects. Without this, a fresh or disconnected instance would return
    # zero channels from GET /channels until the first successful radio sync.
    from app.radio_sync import ensure_default_channels

    await ensure_default_channels()
    await start_radio_stats_sampling()
    await start_oss_update_polling()
    start_hashtag_catalogue_polling()
    start_stale_contact_purge()

    # Always start connection monitor (even if initial connection failed)
    await radio_manager.start_connection_monitor()

    # A retained Community status left behind: an opt-out that could not clear
    # it, or a topic that changed while the app was stopped (IATA, a retired
    # custom broker, or Community turned off). Resume or start its single bounded clear
    # (services/community_status_clear), before the publisher records the
    # current topic over the old one.
    from app.services.community_status_clear import start_status_clear_worker

    try:
        await start_status_clear_worker()
    except Exception:
        logger.exception("Failed to resume the pending Community status clear")

    # Start fanout modules (MQTT, etc.) from database configs
    from app.fanout.manager import fanout_manager

    try:
        await fanout_manager.load_from_db()
    except Exception:
        logger.exception("Failed to start fanout modules")

    # One supervised child per enabled plugin instance (staggered, in the background).
    from app.plugins import lifecycle as plugin_lifecycle

    await plugin_lifecycle.start()

    from app.radio_proxy.manager import radio_proxy_manager

    try:
        await radio_proxy_manager.start_from_db()
    except Exception:
        logger.exception("Failed to start radio proxy")

    startup_radio_task = asyncio.create_task(_startup_radio_connect_and_setup())
    app.state.startup_radio_task = startup_radio_task

    yield

    logger.info("Shutting down")
    try:
        await _shutdown(startup_radio_task, fanout_manager, radio_proxy_manager)
    finally:
        # Always close the DB, even if an earlier step raised or was cancelled,
        # so the WAL is checkpointed and the file is left consistent.
        try:
            await db.disconnect()
        except Exception:
            logger.exception("Shutdown step failed: database disconnect")


# Upper bound for steps that talk to the radio (stopping auto-fetch,
# disconnecting). A wedged serial/BLE link must not hang process exit.
SHUTDOWN_RADIO_TIMEOUT_SECONDS = 10.0


async def _shutdown_step(
    label: str, step: Callable[[], Awaitable[Any]], timeout: float | None = None
) -> None:
    """Run one teardown step; log and continue on failure or timeout."""
    try:
        if timeout is None:
            await step()
        else:
            await asyncio.wait_for(step(), timeout=timeout)
        logger.debug("Shutdown step done: %s", label)
    except TimeoutError:
        logger.error("Shutdown step timed out after %.0fs: %s", timeout, label)
    except Exception:
        logger.exception("Shutdown step failed: %s", label)


async def _cancel_task(task: asyncio.Task | None) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        # Swallow only the cancellation we requested. If the shutdown itself
        # is being cancelled (pending cancel request on the current task), or
        # the CancelledError did not come from ``task``, propagate it.
        current = asyncio.current_task()
        if not task.cancelled() or (current is not None and current.cancelling()):
            raise


async def _shutdown(startup_radio_task, fanout_manager, radio_proxy_manager) -> None:
    from app.background_tasks import drain_background_tasks
    from app.plugins import lifecycle as plugin_lifecycle
    from app.services.community_live import shutdown_community_live
    from app.services.community_status_clear import stop_status_clear_worker

    # Callables, not coroutine objects: a step is only created when it runs,
    # inside the guard (so a synchronous raise is caught too).
    steps = [
        ("startup radio task", lambda: _cancel_task(startup_radio_task)),
        ("community live", shutdown_community_live),
        ("community status clear", stop_status_clear_worker),
        ("plugins", plugin_lifecycle.stop),  # bounded: never over 10 s
        ("fanout modules", fanout_manager.stop_all),
        ("radio proxy", radio_proxy_manager.stop),
        ("connection monitor", radio_manager.stop_connection_monitor),
        ("background contact reconciliation", stop_background_contact_reconciliation),
        ("message polling", stop_message_polling),
        ("radio stats sampling", stop_radio_stats_sampling),
        ("OSS update polling", stop_oss_update_polling),
        ("hashtag catalogue polling", stop_hashtag_catalogue_polling),
        ("periodic advert", stop_periodic_advert),
        ("periodic sync", stop_periodic_sync),
        ("telemetry collect", stop_telemetry_collect),
        ("housekeeping loop", stop_stale_contact_purge),
    ]
    for label, step in steps:
        await _shutdown_step(label, step)

    meshcore = radio_manager.meshcore
    if meshcore:
        await _shutdown_step(
            "radio auto message fetching",
            meshcore.stop_auto_message_fetching,
            SHUTDOWN_RADIO_TIMEOUT_SECONDS,
        )
    await _shutdown_step(
        "radio disconnect", radio_manager.disconnect, SHUTDOWN_RADIO_TIMEOUT_SECONDS
    )
    await _shutdown_step("background tasks", drain_background_tasks)


app = FastAPI(
    title="Meshloom API",
    description=API_DESCRIPTION,
    version=get_app_build_info().version,
    openapi_tags=API_TAGS_METADATA,
    docs_url=None,
    lifespan=lifespan,
    # Default deny: an /api route that declares no permission is admin-only
    # (app/auth/permissions.py).
    dependencies=[Depends(enforce_declaration)],
)

register_api_docs_routes(app)
# Innermost: every route needs a principal except the public ones (app/auth/middleware.py).
app.add_middleware(AuthMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=500)
# Same-origin by default; Settings lists the other sites allowed (app/cors.py).
app.add_middleware(AllowedOriginsCORSMiddleware)
# HSTS (https only, never ingress), no-store on auth routes, Permissions-Policy.
app.add_middleware(SecurityHeadersMiddleware)
# DNS-rebinding guard (observation mode by default): inside the proxy-headers
# middleware, which records the TCP peer the ingress exemption reads.
app.add_middleware(HostGuardMiddleware)
# Outside the gate and CORS: the one interpreter of X-Forwarded-For/-Proto, from
# the trusted proxies setting (app/middleware/proxy_headers.py).
app.add_middleware(TrustedProxyHeadersMiddleware)


@app.exception_handler(RadioDisconnectedError)
async def radio_disconnected_handler(request: Request, exc: RadioDisconnectedError):
    """Return 423 when a radio disconnect race occurs during an operation."""
    return JSONResponse(
        status_code=423,
        content={"detail": {"code": "radio_not_connected", "message": "Radio not connected"}},
    )


@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError):
    """FastAPI's 422 shape without each error's ``input`` and ``ctx``.

    The default body echoes the rejected value, even for a ``SecretStr`` field
    and with ``hide_input_in_errors``: a mistyped password or key would come back
    in the response. The frontend reads only ``type``, ``loc`` and ``msg``.
    """
    detail = [
        {"type": error.get("type"), "loc": list(error.get("loc", ())), "msg": error.get("msg")}
        for error in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": detail})


@app.middleware("http")
async def log_server_errors(request: Request, call_next):
    """Capture 5xx errors and unhandled exceptions into the log ring buffer.

    Starlette writes unhandled-exception tracebacks to stderr, bypassing
    Python logging, so they never reach the debug dump.  This middleware
    catches them and logs via ``logger.exception()`` so the full traceback
    is preserved in the ring buffer for the ``GET /api/debug`` snapshot.
    """
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        raise
    if response.status_code >= 500:
        logger.error("HTTP %d on %s %s", response.status_code, request.method, request.url.path)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    # OSM raster tiles require a Referer; no-referrer makes tile.openstreetmap.org
    # return the "Access blocked" placeholder image. Origin-only on cross-origin
    # still hides local paths.
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    # Refusing to be framed is right when the address is reached directly: it is
    # what stops another site from wrapping this one. Home Assistant's ingress,
    # though, *is* a frame — on its own origin, behind its own authentication — so
    # under it the same rule leaves a blank panel and a 200 in the log, with the
    # browser refusing to render before it asks for a single asset.
    #
    # Same-origin rather than a named parent: the ingress path is served from the
    # Home Assistant origin itself, so "self" is exactly the relation that holds,
    # and nothing else gains the right to frame this.
    if server_settings.embeddable_same_origin:
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
    else:
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response


# API routes - all prefixed with /api for production compatibility
app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api")
app.include_router(auth_totp.router, prefix="/api")
app.include_router(auth_webauthn.router, prefix="/api")
app.include_router(debug.router, prefix="/api")
app.include_router(fanout.router, prefix="/api")
app.include_router(community.router, prefix="/api")
app.include_router(radio.router, prefix="/api")
app.include_router(radio_wifi.router, prefix="/api")
app.include_router(contacts.router, prefix="/api")
app.include_router(contact_groups.router, prefix="/api")
app.include_router(directory.router, prefix="/api")
app.include_router(locate.router, prefix="/api")
app.include_router(repeaters.router, prefix="/api")
app.include_router(rooms.router, prefix="/api")
app.include_router(channels.router, prefix="/api")
app.include_router(messages.router, prefix="/api")
app.include_router(notifications.router, prefix="/api")
app.include_router(packets.router, prefix="/api")
app.include_router(plugins.router, prefix="/api")
app.include_router(read_state.router, prefix="/api")
app.include_router(settings.router, prefix="/api")
app.include_router(share.router, prefix="/api")
app.include_router(statistics.router, prefix="/api")
app.include_router(tools.router, prefix="/api")
app.include_router(updates.router, prefix="/api")
app.include_router(push.router, prefix="/api")
app.include_router(ws.router, prefix="/api")

# Serve frontend static files in production
FRONTEND_DIST_DIR = Path(__file__).parent.parent / "frontend" / "dist"
FRONTEND_PREBUILT_DIR = Path(__file__).parent.parent / "frontend" / "prebuilt"
if not register_first_available_frontend_static_routes(
    app, [FRONTEND_DIST_DIR, FRONTEND_PREBUILT_DIR]
):
    register_frontend_missing_fallback(app)
