"""REST API of the plugin instances (``/api/plugins``; docs/dev/plugins.md, "API").

Reads need ``send`` (member and up, never ``kiosk`` or ``viewer``; ``config``,
``source`` and errors are admin-only fields). Everything that installs, changes, runs
or reads the logs of an instance is ``admin``, and every such change writes an audit
row (``app/auth/audit.py``: actor, ``plugin:<instance_id>``, the plugin id and the
names of the changed fields, never a value or the source). The host lock
(``MESHCORE_DISABLE_PLUGINS``) refuses every write but the deletion and the global
pause with 403 ``plugins_disabled``. The global pause (``/plugins/pause``,
``app/plugins/pause.py``) holds every instance without touching ``enabled``. Secrets of a plugin config are write-only: a
read shows ``********`` (``app/repository/plugins.py``), the logs, the diagnostic and
the captured sends are scrubbed of their values, and no route returns
``config_for_init()``. The supervisor is ``app/plugins/host.py``; each change of status
reaches the admins as a ``plugin_status`` event.
"""

import ast
import asyncio
import logging
import platform
import re
import shutil
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.auth import audit
from app.auth.permissions import has_permission, require
from app.auth.principal import get_principal
from app.log_redaction import redact_text
from app.plugins import installer
from app.plugins import pause as plugin_pause
from app.plugins.attention import scan_source
from app.plugins.capabilities.grants import effective_permissions
from app.plugins.config_schema import secret_fields, validate_config_values
from app.plugins.errors import PluginIssue, PluginValidationError
from app.plugins.host import (
    LANGUAGES,
    PluginCapacityError,
    PluginHostError,
    isolation_level,
    plugin_manager,
)
from app.plugins.manifest import parse_manifest
from app.plugins.permissions import MESSAGES_SEND_MAX_PER_DAY, MESSAGES_SEND_MAX_PER_HOUR
from app.repository.plugins import PluginRepository, PluginStoreError
from app.secret_fields import REDACTED, scrub_error
from app.version_info import get_app_build_info

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])

LOG_DEFAULT_LIMIT = 200
LOG_MAX_LIMIT = 500
LOG_LINE_CHARS = 1000
DIAGNOSTIC_LOG_LINES = 50
MAX_SOURCE_BYTES = 1024 * 1024
MAX_TEST_TEXT_BYTES = 1024
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]+")

Reader = Depends(require("send"))
Admin = Depends(require("admin"))


# --- errors ----------------------------------------------------------------------


def _error(status: int, code: str, message: str, **params: Any) -> HTTPException:
    detail: dict[str, Any] = {"code": code, "message": message}
    if params:
        detail["params"] = params
    return HTTPException(status_code=status, detail=detail)


def _issues(issues: list[PluginIssue]) -> list[dict[str, str]]:
    """Issues for a response: code and path (a field name), never a value."""
    return [{"code": i.code, "path": i.path} for i in issues][:50]


def _unlocked() -> None:
    """Dependency of the write routes: 403 while the host lock or the platform turns
    the plugin system off."""
    source = plugin_manager.disabled_source()
    if source is not None:
        raise _error(403, "plugins_disabled", "Plugins are disabled on this server", source=source)


Writable = Depends(_unlocked)


def _uuid(instance_id: str) -> str:
    """The canonical form of an instance id (a uuid), 404 for anything else."""
    try:
        return str(uuid.UUID(instance_id))
    except ValueError:
        raise _error(404, "plugin_not_found", "No such plugin instance") from None


async def _row(instance_id: str) -> dict[str, Any]:
    row = await PluginRepository.get(_uuid(instance_id))
    if row is None:
        raise _error(404, "plugin_not_found", "No such plugin instance")
    return row


def _target(instance_id: str) -> str:
    return f"plugin:{instance_id}"


def _audit_details(row: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """What an audit row says of an instance: never a config value or the source."""
    return {"plugin_id": row["plugin_id"], "kind": row["kind"], **extra}


# PATCH fields that change what an instance may see, do or runs with.
_CONFIG_FIELDS = frozenset({"config", "scope", "granted_permissions", "quotas", "hooks"})


def _paused_error() -> HTTPException:
    return _error(409, "plugins_paused", "All plugins are paused")


def _store_error(exc: PluginStoreError) -> HTTPException:
    return _error(400, exc.code, "The plugin store refused the change", fields=exc.fields)


# --- models ----------------------------------------------------------------------


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginInstanceView(BaseModel):
    instance_id: str
    plugin_id: str
    label: str
    kind: Literal["bundled", "store", "custom", "local"]
    version: str
    enabled: bool
    manifest: dict[str, Any]
    config: dict[str, Any] = Field(description="Secret fields read ``********``; admin only.")
    config_schema: dict[str, Any]
    secret_fields: list[str]
    scope: dict[str, Any]
    granted_permissions: dict[str, Any]
    quotas: dict[str, Any]
    auto_update: bool
    source: str | None = Field(description="A local plugin's code; admin only.")
    installed_at: int
    updated_at: int
    status: str
    last_error: str | None = Field(description="Admin only.")
    attention: dict[str, Any] | None
    sort_order: int
    summary: str | None = None
    restarts: int = 0
    dropped_events: int = 0


class BundledPluginView(BaseModel):
    plugin_id: str
    version: str
    name: dict[str, str]
    summary: dict[str, str]
    icon: str | None
    categories: list[str]
    hooks: list[str]
    permissions: dict[str, Any]
    privacy: dict[str, Any]
    config_schema: dict[str, Any]
    instances: int


class InstallRequest(_Body):
    kind: Literal["bundled"]
    plugin_id: str = Field(max_length=64)
    label: str | None = Field(default=None, min_length=1, max_length=80)
    granted_permissions: dict[str, Any]
    config: dict[str, Any] = Field(default_factory=dict)


class CreateLocalRequest(_Body):
    label: str = Field(min_length=1, max_length=80)
    hooks: list[Literal["message", "raw_packet", "contact", "telemetry", "health", "timer"]] = (
        Field(default_factory=lambda: ["message"], max_length=6)
    )
    permissions: dict[str, Any] = Field(default_factory=dict)
    template: Literal["blank", "echo"] = "echo"


class PatchRequest(_Body):
    enabled: bool | None = None
    label: str | None = Field(default=None, min_length=1, max_length=80)
    config: dict[str, Any] | None = Field(
        default=None,
        description="Replaces the plain fields; a secret field that is omitted, null or "
        '``********`` keeps its value, ``""`` clears it.',
    )
    scope: dict[str, Any] | None = None
    granted_permissions: dict[str, Any] | None = None
    quotas: dict[str, Any] | None = None
    auto_update: bool | None = None
    sort_order: int | None = Field(default=None, ge=0, le=100_000)
    hooks: (
        list[Literal["message", "raw_packet", "contact", "telemetry", "health", "timer"]] | None
    ) = Field(default=None, max_length=6, description="A local plugin's subscriptions.")


class SourceRequest(_Body):
    source: str = Field(max_length=MAX_SOURCE_BYTES)


class SourceResult(BaseModel):
    instance: PluginInstanceView
    scan: list[dict[str, Any]]
    http_hosts: list[str] = Field(
        default_factory=list, description="``https://`` hosts found in the code (to accept)."
    )


class TestRequest(_Body):
    text: str = Field(min_length=1, max_length=MAX_TEST_TEXT_BYTES)
    channel: str | None = Field(default=None, description="A channel key; default: a test channel.")
    contact: str | None = Field(
        default=None, description="A contact public key (a direct message)."
    )


class TestResult(BaseModel):
    result: Any
    sends: list[dict[str, Any]]
    truncated: bool
    logs: list[str]


class CheckFinding(BaseModel):
    stage: str
    code: str
    message: str
    path: str = ""
    line: int | None = None
    informative: bool = False


class CheckResult(BaseModel):
    ok: bool
    errors: list[CheckFinding]
    warnings: list[CheckFinding]
    http_hosts: list[str] = Field(
        default_factory=list,
        description="A local plugin: ``https://`` hosts found in the code, to accept.",
    )


class LogsResult(BaseModel):
    lines: list[str]
    truncated: bool


class DiagnosticResult(BaseModel):
    plugin_id: str
    plugin_version: str
    kind: str
    meshloom_version: str
    python_version: str
    isolation: str
    status: str
    last_error: str | None
    restarts: int
    dropped_events: int
    granted_permissions: list[str]
    logs: list[str]


class PauseView(BaseModel):
    paused: bool
    since: int | None = Field(description="When the pause started (unix seconds).")
    by: str | None = Field(description="The username of the admin who paused.")


class PauseRequest(_Body):
    paused: bool


# --- helpers ---------------------------------------------------------------------


def _live(instance_id: str) -> dict[str, Any]:
    return plugin_manager.statuses().get(instance_id) or {}


async def _view(row: dict[str, Any], admin: bool) -> PluginInstanceView:
    live = _live(row["instance_id"])
    status = live.get("status") or row["status"]
    secrets = await _clear_secrets(row["instance_id"]) if row["secret_fields"] else []
    last_error = (live.get("last_error") if live else row["last_error"]) if admin else None
    summary = live.get("summary")
    return PluginInstanceView(
        instance_id=row["instance_id"],
        plugin_id=row["plugin_id"],
        label=row["label"],
        kind=row["kind"],
        version=row["version"],
        enabled=row["enabled"],
        manifest=row["manifest"],
        config=row["config"] if admin else {},
        config_schema=row["config_schema"],
        secret_fields=row["secret_fields"] if admin else [],
        scope=row["scope"] if admin else {},
        granted_permissions=row["granted_permissions"],
        quotas=row["quotas"] if admin else {},
        auto_update=row["auto_update"],
        source=row["source"] if admin else None,
        installed_at=row["installed_at"],
        updated_at=row["updated_at"],
        status=status,
        last_error=_clean_line(last_error, secrets) if last_error else None,
        attention=row["attention"] if admin else None,
        sort_order=row["sort_order"],
        summary=_clean_line(summary, secrets) if summary else None,
        restarts=live.get("restarts", 0),
        dropped_events=live.get("dropped_events", 0),
    )


def _is_admin(request: Request) -> bool:
    return has_permission(get_principal(request), "admin")


async def _clear_secrets(instance_id: str) -> list[str]:
    """The instance's secret values in clear, for scrubbing output. Never returned."""
    config = await PluginRepository.config_for_init(instance_id) or {}
    row = await PluginRepository.get(instance_id)
    names = set((row or {}).get("secret_fields") or [])
    return [str(v) for k, v in config.items() if k in names and v]


def _clean_line(line: str, secrets: list[str]) -> str:
    text = scrub_error(line, secrets) or ""
    text = redact_text(text, unanchored=True)
    text = _CONTROL_RE.sub(" ", text)
    return text if len(text) <= LOG_LINE_CHARS else text[:LOG_LINE_CHARS] + " [truncated]"


def _scrub_value(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        return _clean_line(value, secrets)
    if isinstance(value, list):
        return [_scrub_value(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: _scrub_value(v, secrets) for k, v in value.items()}
    return value


async def _log_tail(instance_id: str, limit: int) -> tuple[list[str], bool]:
    secrets = await _clear_secrets(instance_id)
    lines = plugin_manager.logs(instance_id)
    tail = lines[-limit:]
    return [_clean_line(line, secrets) for line in tail], len(lines) > limit


def _check_config(
    schema: dict[str, Any], provided: dict[str, Any], stored_secrets: frozenset[str]
) -> tuple[list[PluginIssue], list[str]]:
    """``(invalid, missing)`` for a config against its schema. A secret kept by omission
    or the mask counts as present; ``language`` is a standard field of every instance."""
    props = schema.get("properties", {})
    secrets = secret_fields(schema)
    invalid: list[PluginIssue] = []
    values: dict[str, Any] = {}
    for key, value in provided.items():
        if key == "language" and key not in props:
            if value not in LANGUAGES:
                invalid.append(PluginIssue("config_value_invalid", key, "unsupported language"))
        elif key not in props:
            invalid.append(PluginIssue("config_field_unknown", key, "unknown field"))
        elif key in secrets:
            if value is None or value == REDACTED:
                if key in stored_secrets:
                    values[key] = REDACTED
            elif value != "":
                values[key] = value
        else:
            values[key] = value
    for key in stored_secrets & secrets:
        if key not in provided:
            values[key] = REDACTED
    issues = validate_config_values(schema, values)
    invalid.extend(i for i in issues if i.code == "config_value_invalid")
    missing = [name for name in schema.get("required", []) if name not in values]
    return invalid, missing


def _config_invalid(invalid: list[PluginIssue]) -> HTTPException:
    return _error(
        400,
        "config_invalid",
        "The config does not match the plugin's schema",
        issues=_issues(invalid),
    )


def _check_scope(scope: dict[str, Any]) -> bool:
    def filter_ok(value: Any) -> bool:
        if value in ("all", "none"):
            return True
        if isinstance(value, list):
            return len(value) <= 500 and all(isinstance(k, str) for k in value)
        if isinstance(value, dict):
            other = value.get("except")
            return set(value) == {"except"} and filter_ok(other) and isinstance(other, list)
        return False

    if not scope:
        return True
    if set(scope) != {"messages"}:
        return False
    messages = scope["messages"]
    if messages in ("all", "none"):
        return True
    return (
        isinstance(messages, dict)
        and set(messages) <= {"channels", "contacts"}
        and all(filter_ok(v) for v in messages.values())
    )


def _check_quotas(quotas: dict[str, Any]) -> list[str]:
    """The offending paths of a ``quotas`` value (never above the caps)."""
    caps = {"per_hour": MESSAGES_SEND_MAX_PER_HOUR, "per_day": MESSAGES_SEND_MAX_PER_DAY}
    bad: list[str] = []
    for name, value in quotas.items():
        if name != "messages.send" or not isinstance(value, dict):
            bad.append(name)
            continue
        for key, limit in value.items():
            if key not in caps or type(limit) is not int or not 1 <= limit <= caps[key]:
                bad.append(f"{name}.{key}")
    return bad


def _capacity_error(exc: PluginCapacityError) -> HTTPException:
    return _error(409, exc.code, "Too many plugin instances are running", cap=exc.cap)


def _status_block(row: dict[str, Any], *, acknowledged: bool = False) -> HTTPException | None:
    """Why a start is refused by the row's status (cadrage 4.2): ``revoked`` and
    ``incompatible`` never run; ``needs_attention`` for a reason other than
    ``config_required`` (``review_permissions``, ``consent_required``, ...) needs the
    admin to acknowledge it by sending ``granted_permissions`` again; ``reinstall`` (an
    instance restored from a backup without its code) never starts."""
    status = row["status"]
    if status in ("revoked", "incompatible"):
        return _error(409, f"plugin_{status}", f"The instance is {status} and cannot start")
    attention = row["attention"] or {}
    kind = attention.get("type")
    if status == "needs_attention" and kind == "reinstall":
        # Restored from a backup without its code (services/backup_plugins.py): no
        # acknowledgement can start it, only a reinstall or an uninstall.
        return _error(
            409, "plugin_reinstall_required", "The instance must be reinstalled before it starts"
        )
    if status == "needs_attention" and kind not in (None, "config_required") and not acknowledged:
        return _error(
            409, "attention_required", "The instance needs a review before it starts", type=kind
        )
    return None


def _status_changes(
    row: dict[str, Any],
    *,
    missing: list[str],
    will_run: bool,
    acknowledged: bool,
    config_changed: bool,
) -> dict[str, Any]:
    """The one place a PATCH may move ``status``/``attention`` (the supervisor owns the
    rest). ``revoked`` and ``incompatible`` are frozen: only an update (M2) leaves them."""
    status = row["status"]
    if status in ("revoked", "incompatible"):
        return {}
    cleared = {"attention": None, "status": "installed"}
    if (row["attention"] or {}).get("type") == "config_required" and not missing:
        return cleared
    if will_run and acknowledged and status == "needs_attention":
        return cleared
    if not will_run and missing and config_changed and status != "needs_attention":
        # Cleared a required field on a stopped instance: it waits for its config.
        return {
            "attention": {"type": "config_required", "fields": missing},
            "status": "needs_attention",
        }
    return {}


def _stored_missing(row: dict[str, Any]) -> list[str]:
    """The required config fields the stored config lacks (checked at every start)."""
    return _check_config(row["config_schema"], row["config"], frozenset(row["secret_fields"]))[1]


async def _apply_runtime(instance_id: str, row: dict[str, Any], *, restart: bool) -> None:
    """Make the process match the row: stop when disabled, (re)start when enabled and
    asked to. Raises ``HTTPException`` on a refusal of the host."""
    if row["enabled"]:
        blocked = _status_block(row)
        if blocked is not None:
            raise blocked
        missing = _stored_missing(row)
        if missing:
            raise _error(
                409, "config_required", "Required config fields are missing", fields=missing
            )
    try:
        if not row["enabled"]:
            await plugin_manager.stop(instance_id)
        elif restart or plugin_manager.get(instance_id) is None:
            await plugin_manager.restart(instance_id)
    except PluginCapacityError as exc:
        raise _capacity_error(exc) from None
    except PluginHostError as exc:
        raise _error(409, exc.code, "The plugin host refused to start the instance") from None


# --- reads -----------------------------------------------------------------------


@router.get("", response_model=list[PluginInstanceView], dependencies=[Reader])
async def list_plugins(request: Request) -> list[PluginInstanceView]:
    admin = _is_admin(request)
    return [await _view(row, admin) for row in await PluginRepository.get_all()]


@router.get("/bundled", response_model=list[BundledPluginView], dependencies=[Reader])
async def list_bundled() -> list[BundledPluginView]:
    """The plugins shipped with this release, installable offline."""
    counts: dict[str, int] = {}
    for row in await PluginRepository.get_all():
        counts[row["plugin_id"]] = counts.get(row["plugin_id"], 0) + 1
    result = []
    for plugin_id, bundled in (await asyncio.to_thread(installer.list_bundled)).items():
        manifest = bundled.package.manifest
        result.append(
            BundledPluginView(
                plugin_id=plugin_id,
                version=manifest.version,
                name=manifest.name,
                summary=manifest.summary,
                icon=manifest.icon,
                categories=list(manifest.categories),
                hooks=list(manifest.hooks),
                permissions=manifest.permissions.declared(),
                privacy=manifest.privacy.model_dump(mode="json"),
                config_schema=bundled.config_schema,
                instances=counts.get(plugin_id, 0),
            )
        )
    return result


# --- global pause ----------------------------------------------------------------


def _pause_view() -> PauseView:
    state = plugin_pause.state()
    return PauseView(paused=state.paused, since=state.since, by=state.by)


@router.get("/pause", response_model=PauseView, dependencies=[Reader])
async def get_pause() -> PauseView:
    """The global pause: whether every plugin is held, since when and by whom."""
    return _pause_view()


@router.put("/pause", response_model=PauseView, dependencies=[Admin])
async def put_pause(
    body: PauseRequest, request: Request, actor: audit.RequestActor = audit.SYSTEM_ACTOR
) -> PauseView:
    """Pause or resume every plugin (idempotent). Pausing stops the running instances
    cleanly and starts none until resumed; the per-instance ``enabled`` flags stay.
    Allowed under the host lock, which keeps precedence."""
    principal = get_principal(request)
    by = principal.user.username if principal is not None else None
    _, changed = await plugin_pause.set_paused(body.paused, by, plugin_manager)
    if changed:
        await audit.record(
            "plugins.paused" if body.paused else "plugins.resumed", "success", actor=actor
        )
        from app.services.radio_runtime import radio_runtime
        from app.websocket import broadcast_health

        broadcast_health(radio_runtime.is_connected, radio_runtime.connection_info)
    return _pause_view()


# --- install and create ----------------------------------------------------------


@router.post(
    "/install",
    status_code=201,
    response_model=PluginInstanceView,
    dependencies=[Admin, Writable],
)
async def install_plugin(
    body: InstallRequest, actor: audit.RequestActor = audit.SYSTEM_ACTOR
) -> PluginInstanceView:
    """Install a bundled plugin. ``granted_permissions`` must cover the manifest
    (400 ``consent_required`` lists what is missing); a required config field that is
    missing creates the instance disabled with ``attention.type = "config_required"``.
    While the global pause holds, the instance is created but does not start."""
    async with audit.audited(
        actor, "plugin.installed", details={"plugin_id": body.plugin_id, "kind": "bundled"}
    ) as scope:
        view = await _install(body)
        scope.target = _target(view.instance_id)
        scope.details["enabled"] = view.enabled
    return view


async def _install(body: InstallRequest) -> PluginInstanceView:
    bundled = (await asyncio.to_thread(installer.list_bundled)).get(body.plugin_id)
    if bundled is None:
        raise _error(404, "plugin_not_bundled", "No such bundled plugin")
    manifest = bundled.package.manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    manifest["permissions"] = bundled.package.manifest.permissions.declared()
    missing_consent = installer.consent_missing(manifest, body.granted_permissions)
    if missing_consent:
        raise _error(
            400, "consent_required", "Consent does not cover the manifest", missing=missing_consent
        )
    invalid, missing = _check_config(bundled.config_schema, body.config, frozenset())
    if invalid:
        raise _config_invalid(invalid)
    root = plugin_manager.plugins_root()
    try:
        await asyncio.to_thread(installer.install_bundled, bundled, root)
    except PluginValidationError as exc:
        raise _error(
            400, "package_invalid", "The bundled package is invalid", issues=_issues(exc.issues)
        ) from None
    except OSError:
        logger.exception("Installing bundled plugin %s failed", body.plugin_id)
        raise _error(500, "install_failed", "The plugin could not be installed") from None
    needs_config = bool(missing)
    try:
        row = await PluginRepository.create(
            plugin_id=body.plugin_id,
            label=body.label or manifest["name"]["en"],
            kind="bundled",
            version=manifest["version"],
            manifest=manifest,
            config_schema=bundled.config_schema,
            config=body.config,
            granted_permissions=installer.declared_permissions(manifest),
            enabled=not needs_config,
            status="needs_attention" if needs_config else "installed",
            attention={"type": "config_required", "fields": missing} if needs_config else None,
        )
    except PluginStoreError as exc:
        raise _store_error(exc) from None
    instance_id = row["instance_id"]
    if not needs_config:
        try:
            await plugin_manager.start(instance_id)
        except PluginCapacityError as exc:
            await PluginRepository.delete(instance_id)
            raise _capacity_error(exc) from None
        except PluginHostError as exc:
            await PluginRepository.delete(instance_id)
            raise _error(409, exc.code, "The plugin host refused to start the instance") from None
    plugin_manager.announce(instance_id)
    return await _view(await _row(instance_id), True)


@router.post("", status_code=201, response_model=PluginInstanceView, dependencies=[Admin, Writable])
async def create_local_plugin(
    body: CreateLocalRequest, actor: audit.RequestActor = audit.SYSTEM_ACTOR
) -> PluginInstanceView:
    """Create a local plugin (code kept in the database, started disabled)."""
    async with audit.audited(actor, "plugin.created", details={"kind": "local"}) as scope:
        view = await _create_local(body)
        scope.target = _target(view.instance_id)
        scope.details["plugin_id"] = view.plugin_id
    return view


async def _create_local(body: CreateLocalRequest) -> PluginInstanceView:
    hooks = list(dict.fromkeys(body.hooks))
    data = installer.local_manifest(body.label, hooks, body.permissions)
    try:
        manifest = parse_manifest(data, origin="local")
    except PluginValidationError as exc:
        raise _error(
            400, "manifest_invalid", "The manifest is invalid", issues=_issues(exc.issues)
        ) from None
    stored = manifest.model_dump(mode="json", by_alias=True, exclude_none=True)
    stored["permissions"] = manifest.permissions.declared()
    row = await PluginRepository.create(
        plugin_id=manifest.id,
        label=body.label,
        kind="local",
        version=manifest.version,
        manifest=stored,
        granted_permissions=stored["permissions"],
        source=installer.local_template_source(body.template),
    )
    plugin_manager.announce(row["instance_id"])
    return await _view(row, True)


# --- change ----------------------------------------------------------------------


@router.patch("/{instance_id}", response_model=PluginInstanceView, dependencies=[Admin, Writable])
async def patch_plugin(
    instance_id: str, body: PatchRequest, actor: audit.RequestActor = audit.SYSTEM_ACTOR
) -> PluginInstanceView:
    """Change an instance. Enabling starts its process, disabling stops it; a change of
    config, scope, grants, quotas or label restarts a running one (none starts while
    the global pause holds). ``config`` replaces the plain fields and merges the
    secrets (write-only).

    Audit: ``plugin.enabled`` / ``plugin.disabled`` for a change of ``enabled``,
    ``plugin.config_changed`` for config, scope, grants, quotas or hooks, otherwise
    ``plugin.changed``; one row per kind, the changed field names only."""
    row = await _row(instance_id)
    fields = {f for f in body.model_fields_set if getattr(body, f) is not None}
    toggled = "enabled" in fields and body.enabled != row["enabled"]
    actions: list[tuple[audit.AuditAction, list[str]]] = []
    if toggled:
        actions.append(("plugin.enabled" if body.enabled else "plugin.disabled", ["enabled"]))
    if config_fields := sorted(fields & _CONFIG_FIELDS):
        actions.append(("plugin.config_changed", config_fields))
    if other := sorted(fields - _CONFIG_FIELDS - {"enabled"}):
        actions.append(("plugin.changed", other))
    if not actions:
        return await _patch(instance_id, row, body)
    first, first_fields = actions[0]
    async with audit.audited(
        actor, first, target=_target(row["instance_id"]), details=_audit_details(row)
    ) as scope:
        scope.details["fields"] = first_fields
        view = await _patch(instance_id, row, body)
    for action, names in actions[1:]:
        await audit.record(
            action,
            "success",
            actor=actor,
            target=_target(row["instance_id"]),
            details=_audit_details(row, fields=names),
        )
    return view


async def _patch(instance_id: str, row: dict[str, Any], body: PatchRequest) -> PluginInstanceView:
    instance_id = row["instance_id"]
    fields = body.model_fields_set
    changes: dict[str, Any] = {}
    stored_secrets = frozenset(row["secret_fields"])
    schema = row["config_schema"]
    # Re-evaluated from the stored config on every call, never trusted from the flag.
    missing = _stored_missing(row)
    if "config" in fields and body.config is not None:
        invalid, missing = _check_config(schema, body.config, stored_secrets)
        if invalid:
            raise _config_invalid(invalid)
        changes["config"] = body.config
    if "granted_permissions" in fields and body.granted_permissions is not None:
        if row["kind"] == "local":
            # A local plugin declares and grants together: editing its permissions
            # rewrites its manifest, revalidated (consent is the admin's request).
            draft = {**row["manifest"], "permissions": body.granted_permissions}
            try:
                parsed = parse_manifest(draft, origin="local")
            except PluginValidationError as exc:
                raise _error(
                    400,
                    "manifest_invalid",
                    "The permissions are invalid",
                    issues=_issues(exc.issues),
                ) from None
            declared = parsed.permissions.declared()
            changes["manifest"] = {**row["manifest"], "permissions": declared}
            changes["granted_permissions"] = declared
        elif problems := installer.grant_problems(row["manifest"], body.granted_permissions):
            raise _error(
                400,
                "permission_exceeds_manifest",
                "A grant must be a subset of the manifest's permissions",
                fields=problems,
            )
        else:
            changes["granted_permissions"] = body.granted_permissions
    if "hooks" in fields and body.hooks is not None:
        if row["kind"] != "local":
            raise _error(409, "not_local", "Only a local plugin has editable hooks")
        draft = {
            **(changes.get("manifest") or row["manifest"]),
            "hooks": list(dict.fromkeys(body.hooks)),
        }
        try:
            parse_manifest(draft, origin="local")
        except PluginValidationError as exc:
            raise _error(
                400, "manifest_invalid", "The hooks are invalid", issues=_issues(exc.issues)
            ) from None
        changes["manifest"] = draft
    if "scope" in fields and body.scope is not None:
        if not _check_scope(body.scope):
            raise _error(400, "scope_invalid", "The scope is not valid")
        changes["scope"] = body.scope
    if "quotas" in fields and body.quotas is not None:
        bad = _check_quotas(body.quotas)
        if bad:
            raise _error(400, "quota_invalid", "A quota is invalid or above its cap", fields=bad)
        changes["quotas"] = body.quotas
    for name in ("label", "auto_update", "sort_order"):
        if name in fields and getattr(body, name) is not None:
            changes[name] = getattr(body, name)
    enabling = body.enabled is True and not row["enabled"]
    will_run = row["enabled"] if body.enabled is None else body.enabled
    acknowledged = "granted_permissions" in fields
    if will_run:
        blocked = _status_block(row, acknowledged=acknowledged)
        if blocked is not None:
            raise blocked
        if missing:
            raise _error(
                409, "config_required", "Required config fields are missing", fields=missing
            )
    if body.enabled is not None:
        changes["enabled"] = body.enabled
    changes.update(
        _status_changes(
            row,
            missing=missing,
            will_run=will_run,
            acknowledged=acknowledged,
            config_changed="config" in changes,
        )
    )
    if not changes:
        return await _view(row, True)
    try:
        updated = await PluginRepository.update(instance_id, **changes)
    except PluginStoreError as exc:
        raise _store_error(exc) from None
    if updated is None:
        raise _error(404, "plugin_not_found", "No such plugin instance")
    restart = enabling or bool(
        set(changes) & {"config", "scope", "granted_permissions", "quotas", "label", "manifest"}
    )
    if updated["enabled"] != row["enabled"] or (restart and updated["enabled"]):
        try:
            await _apply_runtime(instance_id, updated, restart=restart)
        except HTTPException:
            if enabling:
                await PluginRepository.update(instance_id, enabled=False)
            plugin_manager.announce(instance_id)
            raise
    plugin_manager.announce(instance_id)
    return await _view(await _row(instance_id), True)


@router.put("/{instance_id}/source", response_model=SourceResult, dependencies=[Admin, Writable])
async def put_source(
    instance_id: str, body: SourceRequest, actor: audit.RequestActor = audit.SYSTEM_ACTOR
) -> SourceResult:
    """Replace a local plugin's code (``ast.parse`` first) and restart it when enabled.
    Audited as ``plugin.updated`` with the source's size in bytes (``source_bytes``),
    never its text."""
    row = await _row(instance_id)
    async with audit.audited(
        actor,
        "plugin.updated",
        target=_target(row["instance_id"]),
        details=_audit_details(
            row, fields=["source"], source_bytes=len(body.source.encode("utf-8"))
        ),
    ):
        return await _put_source(row["instance_id"], row, body)


async def _put_source(instance_id: str, row: dict[str, Any], body: SourceRequest) -> SourceResult:
    if row["kind"] != "local":
        raise _error(409, "not_local", "Only a local plugin has editable source")
    if len(body.source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise _error(400, "source_too_large", "The source is over 1 MiB")
    try:
        ast.parse(body.source)
    except SyntaxError as exc:
        raise _error(
            400, "source_syntax_error", "The source is not valid Python", line=exc.lineno
        ) from None
    except (ValueError, RecursionError, MemoryError):
        raise _error(400, "source_invalid", "The source cannot be parsed") from None
    updated = await PluginRepository.update(instance_id, source=body.source)
    assert updated is not None
    if updated["enabled"]:
        await _apply_runtime(instance_id, updated, restart=True)
    plugin_manager.announce(instance_id)
    report = scan_source(body.source)
    scan = [
        {"code": r.code, "line": r.line, "detail": r.detail, "informative": r.informative}
        for r in report.reasons
    ]
    return SourceResult(
        instance=await _view(await _row(instance_id), True), scan=scan, http_hosts=report.http_hosts
    )


# --- actions ---------------------------------------------------------------------


@router.post(
    "/{instance_id}/restart", response_model=PluginInstanceView, dependencies=[Admin, Writable]
)
async def restart_plugin(instance_id: str) -> PluginInstanceView:
    """Restart an instance with a fresh failure count (also clears ``crash_loop``).
    409 ``plugins_paused`` while the global pause holds."""
    row = await _row(instance_id)
    if plugin_manager.paused:
        raise _paused_error()
    if not row["enabled"]:
        raise _error(409, "plugin_disabled", "The instance is disabled")
    await _apply_runtime(instance_id, row, restart=True)
    plugin_manager.announce(instance_id)
    return await _view(await _row(instance_id), True)


@router.post("/{instance_id}/check", response_model=CheckResult, dependencies=[Admin, Writable])
async def check_plugin(instance_id: str) -> CheckResult:
    """Validate the package and scan its code (``dev check``'s first two stages). Nothing
    is executed here: the unsandboxed dry run stays a development tool, ``test`` is the
    live check."""
    from meshloom_plugin.dev import CheckReport, Finding, check_directory

    row = await _row(instance_id)
    http_hosts: list[str] = []
    if row["kind"] == "local":
        report = CheckReport()
        try:
            parse_manifest(row["manifest"], origin="local")
        except PluginValidationError as exc:
            for issue in exc.issues:
                report.errors.append(Finding("package", issue.code, issue.message, issue.path))
        scanned = scan_source(row["source"] or "")
        http_hosts = list(scanned.http_hosts)
        for reason in scanned.reasons:
            text = reason.code if reason.detail is None else f"{reason.code}: {reason.detail}"
            report.warnings.append(
                Finding(
                    "scan",
                    reason.code,
                    text,
                    line=reason.line or None,
                    informative=reason.informative,
                )
            )
    else:
        directory = plugin_manager.plugins_root() / row["plugin_id"]
        origin = "bundled" if row["kind"] == "bundled" else "third_party"
        report = await asyncio.to_thread(check_directory, directory, origin=origin, dry_run=False)
    body = report.to_json()
    return CheckResult(
        ok=body["ok"],
        errors=[CheckFinding(**f) for f in body["errors"]],
        warnings=[CheckFinding(**f) for f in body["warnings"]],
        http_hosts=http_hosts,
    )


@router.post("/{instance_id}/test", response_model=TestResult, dependencies=[Admin, Writable])
async def test_plugin(instance_id: str, body: TestRequest) -> TestResult:
    """Inject a synthetic ``event.message`` (``test: true``) and return what the plugin
    sent: the sends are captured by the host and never reach the radio. 409
    ``plugins_paused`` while the global pause holds."""
    from app.repository import ChannelRepository, ContactRepository

    row = await _row(instance_id)
    if plugin_manager.paused:
        raise _paused_error()
    if len(body.text.encode("utf-8")) > MAX_TEST_TEXT_BYTES:
        raise _error(400, "test_text_too_long", "The test text is over 1 KiB")
    if body.channel and body.contact:
        raise _error(400, "test_target_ambiguous", "Give a channel or a contact, not both")
    if "message" not in (row["manifest"].get("hooks") or []):
        raise _error(409, "hook_not_declared", "The plugin does not subscribe to messages")
    is_dm = body.contact is not None
    sender_key = ("ab" * 32) if not is_dm else body.contact.lower()  # type: ignore[union-attr]
    channel_key = ""
    channel_name = None
    if is_dm:
        contact = await ContactRepository.get_by_key(sender_key)
        if contact is None:
            raise _error(404, "contact_not_found", "No such contact")
        sender_name = contact.name or "Tester"
        conversation_key = sender_key
    else:
        channel_key = (body.channel or "CD" * 16).upper()
        if body.channel:
            channel = await ChannelRepository.get_by_key(channel_key)
            if channel is None:
                raise _error(404, "channel_not_found", "No such channel")
            channel_name = channel.name
        else:
            channel_name = "#test"
        sender_name = "Tester"
        conversation_key = channel_key
    data = {
        "sender_name": sender_name,
        "sender_key": sender_key if is_dm else None,
        "message_text": body.text,
        "is_dm": is_dm,
        "channel_key": None if is_dm else channel_key,
        "channel_name": channel_name,
        "conversation_key": conversation_key,
        "is_public_channel": False,
        "sender_timestamp": None,
        "received_at": None,
        "path": "",
        "path_bytes_per_hop": 1,
        "packet_hash": None,
        "region": None,
        "scoped": False,
        "is_outgoing": False,
    }
    try:
        outcome = await plugin_manager.dispatch_test(instance_id, "message", data)
    except PluginHostError as exc:
        status = 409 if exc.code in ("instance_not_running", "plugins_paused") else 502
        raise _error(status, exc.code, "The test event failed") from None
    except TimeoutError:
        raise _error(504, "test_timeout", "The plugin did not answer in time") from None
    secrets = await _clear_secrets(instance_id)
    logs, _ = await _log_tail(instance_id, 20)
    return TestResult(
        result=_scrub_value(outcome.get("result"), secrets),
        sends=_scrub_value(list(outcome.get("sends") or []), secrets),
        truncated=bool(outcome.get("truncated")),
        logs=logs,
    )


# --- logs, diagnostic, delete ----------------------------------------------------


@router.get("/{instance_id}/logs", response_model=LogsResult, dependencies=[Admin])
async def plugin_logs(
    instance_id: str, limit: int = Query(LOG_DEFAULT_LIMIT, ge=1, le=LOG_MAX_LIMIT)
) -> LogsResult:
    """The tail of the instance's stderr ring: truncated, redacted lines. Never part of
    ``/api/debug``."""
    await _row(instance_id)
    lines, truncated = await _log_tail(instance_id, limit)
    return LogsResult(lines=lines, truncated=truncated)


@router.get("/{instance_id}/diagnostic", response_model=DiagnosticResult, dependencies=[Admin])
async def plugin_diagnostic(instance_id: str) -> DiagnosticResult:
    """A block to paste in a report: versions, isolation, last error, the last log
    lines. No config value and no secret."""
    row = await _row(instance_id)
    live = _live(instance_id)
    lines, _ = await _log_tail(instance_id, DIAGNOSTIC_LOG_LINES)
    secrets = await _clear_secrets(instance_id)
    last_error = live.get("last_error") if live else row["last_error"]
    return DiagnosticResult(
        plugin_id=row["plugin_id"],
        plugin_version=row["version"],
        kind=row["kind"],
        meshloom_version=get_app_build_info().version,
        python_version=platform.python_version(),
        isolation=isolation_level(),
        status=live.get("status") or row["status"],
        last_error=_clean_line(last_error, secrets) if last_error else None,
        restarts=live.get("restarts", 0),
        dropped_events=live.get("dropped_events", 0),
        granted_permissions=sorted(
            effective_permissions(row["manifest"], row["granted_permissions"])
        ),
        logs=lines,
    )


@router.delete("/{instance_id}", status_code=204, dependencies=[Admin])
async def delete_plugin(
    instance_id: str,
    keep_data: bool = Query(False),
    actor: audit.RequestActor = audit.SYSTEM_ACTOR,
) -> Response:
    """Uninstall an instance. ``keep_data`` keeps its key-value store; its secrets
    always go. The shared code of a bundled plugin stays for its other instances."""
    row = await _row(instance_id)
    instance_id = row["instance_id"]
    async with audit.audited(
        actor,
        "plugin.removed",
        target=_target(instance_id),
        details=_audit_details(row, keep_data=keep_data),
    ):
        await _delete(row, keep_data)
    return Response(status_code=204)


async def _delete(row: dict[str, Any], keep_data: bool) -> None:
    instance_id = row["instance_id"]
    await plugin_manager.remove(instance_id, purge=True)
    await PluginRepository.delete(instance_id, keep_data=keep_data)
    if row["kind"] == "local":
        shutil.rmtree(
            plugin_manager.plugins_root() / "local" / row["instance_id"], ignore_errors=True
        )
