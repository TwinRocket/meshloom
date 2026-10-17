"""Restore of a full backup: the identity decision table, inspect, and the job.

``decide_identity_restore`` is the single source of truth for what a restore
may do with the radio identity (import the backup key, wipe the history
recorded under another key, bind the backup key). Inspect, the synchronous
check before a job starts and the job itself (right before the import) all
call it, so a confirmation asked in the UI always matches what runs.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace

from app.models import (
    BackupCounts,
    BackupEnvelope,
    BackupExportV2,
    BackupIdentityRelation,
    BackupIdentityRequires,
    BackupInspectResult,
    BackupRestoreItem,
    BackupRestoreJob,
    BackupRestoreRequest,
    BackupRestoreStep,
    BackupRestoreStepName,
    BackupRestoreSteps,
    BackupRestoreStepStatus,
    BackupRestoreV2Result,
    BackupV1Document,
    RadioTransportSnapshot,
)
from app.radio import RadioOperationBusyError, RadioOperationError
from app.services.backup import BackupError, open_backup_document

logger = logging.getLogger(__name__)

GATE_MISMATCH = "identity_mismatch"
GATE_UNBOUND_LEGACY = "identity_unbound_legacy"


@dataclass(frozen=True)
class IdentityDecision:
    """What a restore does with the identity (C.4 table of the TR-1 plan).

    ``deferred``: the live key is unknown (radio not connected); nothing is
    imported or bound now, the radio steps are skipped.
    """

    relation: BackupIdentityRelation
    import_key: bool = False
    wipe: bool = False
    wipe_optional: bool = False
    bind: bool = False
    deferred: bool = False
    requires_replace_live: bool = False
    requires_wipe: bool = False
    refusal: str | None = None

    @property
    def requires(self) -> BackupIdentityRequires:
        return BackupIdentityRequires(
            replace_live_identity=self.requires_replace_live, wipe_history=self.requires_wipe
        )


def _norm(key: str | None) -> str | None:
    return key.strip().lower() if isinstance(key, str) and key.strip() else None


def decide_identity_restore(
    *,
    gate: str | None,
    bound: str | None,
    live: str | None,
    backup: str | None,
    has_history: bool,
) -> IdentityDecision:
    """Pure. ``backup`` is the backup's public key when it carries a private key."""
    gate = gate if gate in (GATE_MISMATCH, GATE_UNBOUND_LEGACY) else None
    bound, live, backup = _norm(bound), _norm(live), _norm(backup)
    if backup is None:
        return IdentityDecision(relation="no_identity")

    def replace_live(*, wipe: bool, relation: BackupIdentityRelation = "replaces_live"):
        return IdentityDecision(
            relation=relation,
            import_key=True,
            wipe=wipe,
            bind=True,
            requires_replace_live=True,
            requires_wipe=wipe,
        )

    if gate == GATE_MISMATCH:
        if live is None:
            return IdentityDecision(relation="needs_radio", refusal="backup_identity_needs_radio")
        if bound == backup:
            if live == backup:  # nothing to import, just close the gate
                return IdentityDecision(relation="same", bind=True)
            # Row 8: radio L plugged into a Meshloom bound to P; history is P's.
            return replace_live(wipe=False, relation="restores_bound")
        if live == backup:
            # Row 9: the live radio already is P; the history belongs to B.
            return IdentityDecision(
                relation="replaces_history", wipe=True, bind=True, requires_wipe=True
            )
        # Row 10: three different keys.
        return IdentityDecision(relation="conflict", refusal="backup_identity_conflict")

    if gate == GATE_UNBOUND_LEGACY:
        if live is None:
            return IdentityDecision(relation="needs_radio", refusal="backup_identity_needs_radio")
        if live == backup:
            # Row 11: legacy history, the live radio is P: bind, keep it.
            return IdentityDecision(relation="same", bind=True)
        # Row 12: the history has no bound owner; kept and attributed to P
        # unless the user asks for a wipe.
        return replace(replace_live(wipe=False), wipe_optional=True)

    # No gate.
    if live is None:
        if bound == backup:
            return IdentityDecision(relation="same", deferred=True)  # row 2
        if bound is None:
            # Rows 3-4: never bind P before a radio shows it holds P.
            return IdentityDecision(relation="new_database", deferred=True)
        # Row 7: neither the history owner nor the radio can be checked.
        return IdentityDecision(relation="needs_radio", refusal="backup_identity_needs_radio")

    if live == backup:
        if bound in (None, backup):
            return IdentityDecision(relation="same", bind=bound is None)  # row 1
        # The radio already is P but the history belongs to B (gate not open yet).
        return IdentityDecision(
            relation="replaces_history", wipe=True, bind=True, requires_wipe=True
        )
    if bound == live:
        # Rows 5-6: a reflashed radio auto-bound; history (if any) is L's.
        return replace_live(wipe=has_history)
    if bound is None:
        return replace(replace_live(wipe=False), wipe_optional=True)
    if bound == backup:
        return replace_live(wipe=False, relation="restores_bound")
    return IdentityDecision(relation="conflict", refusal="backup_identity_conflict")


def check_identity_confirmations(
    decision: IdentityDecision,
    *,
    live: str | None,
    replace_live_identity: bool,
    wipe_history: bool,
    expected_live_public_key: str | None,
) -> None:
    """Raise the 409 the POST answers when a confirmation is missing."""
    if decision.refusal is not None:
        raise BackupError(
            decision.refusal,
            status=409,
            message=(
                "Connect the radio, then restore"
                if decision.refusal == "backup_identity_needs_radio"
                else "The bound key, the live key and the backup key all differ"
            ),
        )
    if decision.requires_replace_live and (
        not replace_live_identity or _norm(expected_live_public_key) != _norm(live)
    ):
        raise BackupError(
            "backup_identity_replaces_live",
            status=409,
            message="The restore replaces the identity of the connected radio",
            params={"live_public_key": live},
        )
    if decision.requires_wipe and not wipe_history:
        raise BackupError(
            "backup_identity_wipe_required",
            status=409,
            message="The history recorded under another key must be erased",
        )


# -- Inputs ------------------------------------------------------------------


@dataclass
class IdentityInputs:
    gate: str | None
    bound: str | None
    live: str | None
    live_name: str | None
    has_history: bool
    transport: RadioTransportSnapshot
    setup_complete: bool


async def read_live_identity() -> tuple[str | None, str | None]:
    """(public key, name) of the connected radio once setup completed, else (None, None)."""
    from app.services.radio_identity import read_radio_public_key
    from app.services.radio_runtime import radio_runtime

    manager = radio_runtime.manager
    if not manager.is_connected or not manager.is_setup_complete:
        return None, None
    try:
        async with radio_runtime.radio_operation("backup_identity_read", blocking=False) as mc:
            return await read_radio_public_key(mc)
    except RadioOperationError:
        # Busy (a restore or a send): the cached SELF_INFO of this connection.
        mc = manager.meshcore
        info = getattr(mc, "self_info", None) if mc is not None else None
        if isinstance(info, dict) and isinstance(info.get("public_key"), str):
            name = info.get("name")
            return _norm(info["public_key"]), name if isinstance(name, str) else None
        return None, None


async def gather_identity_inputs() -> IdentityInputs:
    from app.services import radio_transport
    from app.services.radio_runtime import radio_runtime

    snapshot = await radio_transport.get_transport()
    gate = snapshot.identity_state if snapshot.identity_state else None
    if gate in (GATE_MISMATCH, GATE_UNBOUND_LEGACY):
        live, live_name = _norm(snapshot.mismatch_new_public_key), snapshot.mismatch_new_name
    else:
        live, live_name = await read_live_identity()
    return IdentityInputs(
        gate=gate,
        bound=_norm(snapshot.bound_public_key),
        live=live,
        live_name=live_name,
        has_history=await radio_transport.database_has_mesh_history(),
        transport=snapshot,
        setup_complete=radio_runtime.manager.is_setup_complete,
    )


def backup_public_key(doc: BackupV1Document | BackupExportV2) -> str | None:
    """The backup's key when it carries a private key (else it has no identity)."""
    if isinstance(doc, BackupExportV2) and doc.identity and doc.identity.private_key:
        return doc.identity.public_key
    return None


def decide_for(doc: BackupV1Document | BackupExportV2, inputs: IdentityInputs) -> IdentityDecision:
    return decide_identity_restore(
        gate=inputs.gate,
        bound=inputs.bound,
        live=inputs.live,
        backup=backup_public_key(doc),
        has_history=inputs.has_history,
    )


# -- Inspect -----------------------------------------------------------------


def _pin_text(pin: int | None) -> str:
    return "" if not pin else f"{pin:06d}"


async def _live_wifi_ssid() -> tuple[bool, str | None]:
    """(read, ssid) from the connected radio; (False, None) when unreadable."""
    from app.services.companion_cli import CliError, companion_cli_session, wifi_ssid

    try:
        async with companion_cli_session("backup_inspect_wifi", blocking=False) as cli:
            capabilities = await cli.capabilities()
            if not capabilities.supported or not capabilities.wifi:
                return False, None
            return True, await wifi_ssid(cli)
    except (CliError, RadioOperationError):
        return False, None


async def inspect_backup(
    doc: BackupV1Document | BackupExportV2 | BackupEnvelope, passphrase: str | None
) -> BackupInspectResult:
    """What a restore of this file would do, without writing anything."""
    encrypted = isinstance(doc, BackupEnvelope)
    opened = await open_backup_document(doc, passphrase)
    inputs = await gather_identity_inputs()
    decision = decide_for(opened, inputs)

    result = BackupInspectResult(
        format=opened.format,
        encrypted=encrypted,
        exported_at=opened.exported_at,
        counts=BackupCounts(
            contacts=len(opened.contacts),
            channels=len(opened.channels),
            groups=len(opened.groups),
        ),
        identity_relation=decision.relation,
        identity_requires=decision.requires,
        identity_refusal=decision.refusal,
        live_radio_public_key=inputs.live,
        live_radio_name=inputs.live_name,
    )
    if not isinstance(opened, BackupExportV2):
        return result

    extra = opened.meshloom_extra
    result.meshloom_version = opened.meshloom_version
    result.warnings = list(opened.warnings)
    result.public_key = opened.identity.public_key if opened.identity else None
    result.has_private_key = backup_public_key(opened) is not None
    result.private_key_status = opened.identity.private_key_status if opened.identity else None
    result.has_radio_settings = opened.radio is not None
    result.radio_name = opened.radio.name if opened.radio else None
    result.has_wifi = opened.wifi is not None
    if extra is not None:
        result.counts.fanout_configs = len(extra.fanout_configs)
        result.counts.push_subscriptions = len(extra.push_subscriptions)
        result.bot_names = [cfg.name or cfg.id for cfg in extra.fanout_configs if cfg.type == "bot"]

    transport = inputs.transport
    if (
        transport.transport == "ble"
        and opened.radio is not None
        and opened.radio.ble_pin is not None
        and _pin_text(opened.radio.ble_pin) != (transport.ble_pin or "")
    ):
        result.ble_pin_differs_on_ble = True
    if transport.transport == "tcp" and opened.wifi is not None:
        read, live_ssid = await _live_wifi_ssid()
        # Unreadable counts as different: storing another network and
        # rebooting would drop Meshloom's own link.
        result.wifi_ssid_differs_on_tcp = not read or live_ssid != opened.wifi.ssid
    return result


# -- The restore job -------------------------------------------------------------

STEP_ORDER: tuple[BackupRestoreStepName, ...] = (
    "identity",
    "meshloom",
    "radio_settings",
    "channels",
    "contacts",
    "wifi",
)
RADIO_STEPS: tuple[BackupRestoreStepName, ...] = ("radio_settings", "channels", "contacts", "wifi")
MAX_KEPT_JOBS = 5
RERUN_HINT = (
    "Run the restore again once the radio is back: steps already done are not repeated "
    "in a way that changes anything."
)

_jobs: OrderedDict[str, BackupRestoreJob] = OrderedDict()
_running_job_id: str | None = None


def get_restore_job(job_id: str) -> BackupRestoreJob | None:
    return _jobs.get(job_id)


def get_latest_restore_job() -> BackupRestoreJob | None:
    return next(reversed(_jobs.values()), None) if _jobs else None


def restore_job_running() -> bool:
    return _running_job_id is not None


def _register(job: BackupRestoreJob) -> None:
    _jobs[job.job_id] = job
    while len(_jobs) > MAX_KEPT_JOBS:
        _jobs.popitem(last=False)


@dataclass
class RestoreOptions:
    steps: BackupRestoreSteps
    replace_live_identity: bool = False
    wipe_history: bool = False
    expected_live_public_key: str | None = None


class _Run:
    """One restore run. Holds the decrypted document only in this object,
    which lives as long as the job coroutine; the job model never sees it."""

    def __init__(
        self,
        job: BackupRestoreJob,
        doc: BackupV1Document | BackupExportV2,
        options: RestoreOptions,
    ) -> None:
        self.job = job
        self.doc = doc
        self.options = options
        self.result = BackupRestoreV2Result()
        self.stop = False  # nothing else is safe (identity step failed)
        self.radio_skip_code: str | None = None

    def step(self, name: BackupRestoreStepName) -> BackupRestoreStep:
        return next(step for step in self.job.steps if step.step == name)

    def set(
        self,
        name: BackupRestoreStepName,
        status: BackupRestoreStepStatus,
        code: str | None = None,
        detail: str | None = None,
    ) -> None:
        step = self.step(name)
        step.status = status
        step.code = code
        step.detail = detail

    def skip_radio_steps(self, code: str, detail: str | None = None) -> None:
        for name in RADIO_STEPS:
            if self.step(name).status == "pending":
                self.set(name, "skipped", code, detail)


def _settle_identity(run: _Run, inputs: IdentityInputs) -> IdentityDecision | None:
    """Decide and check; record the outcomes that need no write and return None.

    Returns the decision only when an import, a wipe or a bind is needed.
    """
    options = run.options
    decision = decide_for(run.doc, inputs)
    try:
        check_identity_confirmations(
            decision,
            live=inputs.live,
            replace_live_identity=options.replace_live_identity,
            wipe_history=options.wipe_history,
            expected_live_public_key=options.expected_live_public_key,
        )
    except BackupError as exc:
        run.set("identity", "failed", exc.code, exc.message)
        run.stop = True
        return None
    if decision.deferred:
        run.set(
            "identity",
            "skipped",
            "radio_not_connected",
            "Connect the radio: Meshloom asks what to do with its identity; "
            "choose Restore from a backup.",
        )
        run.radio_skip_code = "radio_not_connected"
        return None
    if not (decision.import_key or decision.wipe or decision.bind):
        run.set("identity", "done", "already_this_identity")
        return None
    return decision


async def _identity_step(run: _Run) -> None:
    from app.services.radio_runtime import radio_runtime

    doc, options = run.doc, run.options
    backup_key = backup_public_key(doc)
    if isinstance(doc, BackupV1Document):
        run.set("identity", "skipped", "v1_backup")
        run.radio_skip_code = "v1_backup"
        return
    if backup_key is None or not options.steps.identity:
        run.set("identity", "skipped", "no_identity" if backup_key is None else "step_off")
        snapshot = await _transport_snapshot()
        if snapshot.identity_state in (GATE_MISMATCH, GATE_UNBOUND_LEGACY):
            run.radio_skip_code = "identity_gate_open"
        return

    manager = radio_runtime.manager
    run.set("identity", "running")
    # Decided first outside the lifecycle transition: entering it closes ingest,
    # and only the post-connect setup opens it again. The common outcomes (same
    # radio, radio not connected, a refusal) must leave the running session alone.
    if _settle_identity(run, await gather_identity_inputs()) is None:
        return

    gate_was_open = False
    try:
        async with manager.lifecycle_transition("backup_identity_restore"):
            setup_ran = False
            try:
                # Re-decided with fresh inputs: adverts may have filled the history
                # (row 5 becoming row 6) since the first decision.
                inputs = await gather_identity_inputs()
                gate_was_open = inputs.gate is not None
                decision = _settle_identity(run, inputs)
                if decision is not None:
                    setup_ran = await _execute_identity(run, decision, backup_key, inputs)
            finally:
                if not setup_ran and not gate_was_open:
                    await _restore_session()
    except RadioOperationBusyError:
        run.set("identity", "failed", "radio_busy")
        run.stop = True
    finally:
        if run.stop and gate_was_open:
            # Never let connect/setup loop against the open gate.
            await manager.pause_connection()


async def _restore_session() -> None:
    """Reopen the ingest the lifecycle transition closed, when nothing ran setup.

    Same session, same identity: ingest opens again for it. Otherwise the
    post-connect setup decides (it opens the identity gate on another key).
    """
    from app.services.radio_ingest_gate import allow_ingest
    from app.services.radio_runtime import radio_runtime

    manager = radio_runtime.manager
    if not manager.connection_desired or not manager.is_connected:
        return  # the connection monitor reconnects and runs setup
    try:
        if manager.is_setup_complete:
            snapshot = await _transport_snapshot()
            live, _name = await read_live_identity()
            bound = _norm(snapshot.bound_public_key)
            if not snapshot.identity_state and bound is not None and live == bound:
                allow_ingest()
                return
        await radio_runtime.prepare_connected(broadcast_on_success=True)
    except Exception:
        logger.exception("Setup after a stopped identity restore failed")


async def _transport_snapshot() -> RadioTransportSnapshot:
    from app.services import radio_transport

    return await radio_transport.get_transport()


async def _execute_identity(
    run: _Run, decision: IdentityDecision, backup_key: str, inputs: IdentityInputs
) -> bool:
    """Inside the lifecycle transition: reconnect, check, import, wipe, bind, setup.

    Returns True once the bind ran and the post-connect setup was started.
    """
    from app import keystore
    from app.repository.radio_transport import RadioTransportRepository
    from app.services.backup_radio import import_private_key_raw
    from app.services.radio_identity import (
        clear_ha_after_identity_wipe,
        read_radio_public_key,
        snapshot_ha_retained_topics,
        wipe_mesh_identity_data,
    )
    from app.services.radio_runtime import radio_runtime

    manager = radio_runtime.manager
    doc = run.doc
    assert isinstance(doc, BackupExportV2) and doc.identity and doc.identity.private_key

    def fail(code: str) -> bool:
        run.set("identity", "failed", code)
        run.stop = True
        return False

    # a. Transport only (works while the identity gate is open).
    manager.resume_connection()
    if not await manager.reconnect(broadcast_on_success=False):
        return fail("radio_not_connected")

    # b. Re-read the live key on every path: the decision may rest on a gate
    # snapshot written before this reconnect. Then import first and verify: a
    # refused import must not cost the history.
    key = bytes.fromhex(doc.identity.private_key.get_secret_value())
    try:
        async with radio_runtime.radio_operation("backup_identity_verify") as mc:
            live, _name = await read_radio_public_key(mc)
            if live != backup_key and (not decision.import_key or live != inputs.live):
                return fail("live_identity_changed")
            if decision.import_key and live != backup_key:
                outcome = await import_private_key_raw(mc, key)
                if outcome != "ok":
                    return fail(
                        {
                            "disabled": "private_key_import_disabled",
                            "rejected": "private_key_import_rejected",
                        }.get(outcome, "private_key_import_unverified")
                    )
                live, _name = await read_radio_public_key(mc)
                if live != backup_key:
                    return fail("private_key_import_unverified")
                await keystore.export_and_store_private_key(mc)
    except RadioOperationError:
        return fail("radio_not_connected")
    finally:
        key = b""
    if decision.import_key:
        run.result.identity_restored = True

    # c. Wipe the history recorded under another key, when the table says so.
    if decision.wipe or (decision.wipe_optional and run.options.wipe_history):
        topics = await snapshot_ha_retained_topics()
        await wipe_mesh_identity_data()
        await clear_ha_after_identity_wipe(topics)

    # d. Bind (closes the gate), e. normal setup.
    await RadioTransportRepository.bind_public_key(backup_key)
    run.set("identity", "done", "restored" if decision.import_key else "bound")
    try:
        connected = await radio_runtime.prepare_connected(broadcast_on_success=True)
    except Exception:
        logger.exception("Setup after the identity restore failed")
        connected = False
    if not connected:
        run.radio_skip_code = "radio_not_connected"
    return True


async def _meshloom_step(run: _Run) -> None:
    from app.services.backup import restore_json, restore_meshloom_extra

    if not run.options.steps.meshloom:
        run.set("meshloom", "skipped", "step_off")
        return
    run.set("meshloom", "running")
    doc = run.doc
    result = await restore_json(
        BackupRestoreRequest(
            confirm=True,
            contacts=doc.contacts,
            channels=doc.channels,
            settings=doc.settings,  # the instance: keeps model_fields_set
            groups=doc.groups,
            push_defaults=doc.push_defaults,
            push_conversation_overrides=doc.push_conversation_overrides,
            vapid_subject=doc.vapid_subject,
        )
    )
    run.result.contacts_upserted = result.contacts_upserted
    run.result.channels_upserted = result.channels_upserted
    run.result.groups_upserted = result.groups_upserted
    run.result.settings_updated = result.settings_updated
    if isinstance(doc, BackupExportV2) and doc.meshloom_extra is not None:
        run.result.warnings.extend(await restore_meshloom_extra(doc.meshloom_extra))
    run.set("meshloom", "done")


async def _radio_steps(run: _Run) -> None:
    from app.repository import AppSettingsRepository
    from app.services.backup_radio import (
        push_channels_to_radio,
        push_contacts_to_radio,
        write_radio_node_settings,
    )
    from app.services.companion_cli import (
        CliError,
        companion_cli_session,
        set_wifi_enabled,
        store_wifi_credentials,
        wifi_enabled,
    )
    from app.services.radio_runtime import radio_runtime

    doc, steps = run.doc, run.options.steps
    if run.radio_skip_code is not None:
        run.skip_radio_steps(
            run.radio_skip_code,
            RERUN_HINT if run.radio_skip_code == "radio_not_connected" else None,
        )
        return
    assert isinstance(doc, BackupExportV2)
    manager = radio_runtime.manager
    if not manager.is_connected or not manager.is_setup_complete:
        run.skip_radio_steps("radio_not_connected", RERUN_HINT)
        return

    try:
        async with companion_cli_session("backup_restore") as cli:
            mc = manager.meshcore
            try:
                capabilities = await cli.capabilities()
            except CliError:
                capabilities = None
            cli_ok = capabilities is not None and capabilities.supported

            # 3. Radio settings, then one clock sync.
            if doc.radio is None:
                run.set("radio_settings", "skipped", "not_in_backup")
            elif not steps.radio_settings:
                run.set("radio_settings", "skipped", "step_off")
            else:
                run.set("radio_settings", "running")
                app_settings = await AppSettingsRepository.get()
                outcome = await write_radio_node_settings(
                    mc,
                    cli if cli_ok else None,
                    doc.radio,
                    write_ble_pin=steps.ble_pin,
                    flood_scope=app_settings.flood_scope,
                )
                run.step("radio_settings").items = outcome.items
                run.result.warnings.extend(outcome.warnings)
                await _maybe_save_ble_pin(outcome, doc)
                failed = any(item.status == "failed" for item in outcome.items)
                run.set("radio_settings", "failed" if failed else "done")
                run.step("radio_settings").items = outcome.items
                from app.radio_sync import sync_radio_time_outcome

                if await sync_radio_time_outcome(mc) == "rebooted":
                    run.skip_radio_steps("radio_rebooting", RERUN_HINT)
                    return

            # 4. Channels (from the DB: needs the meshloom step).
            if not steps.meshloom:
                run.set("channels", "skipped", "meshloom_step_off")
            elif not steps.channels:
                run.set("channels", "skipped", "step_off")
            elif not manager.device_info_loaded:
                run.set("channels", "skipped", "device_info_unknown")
            else:
                run.set("channels", "running")
                written, warnings = await push_channels_to_radio(mc)
                run.result.channels_on_radio = written
                run.result.warnings.extend(warnings)
                run.set("channels", "done")

            # 5. Contacts (favourites first, within the radio's limit).
            if not steps.meshloom:
                run.set("contacts", "skipped", "meshloom_step_off")
            elif not steps.contacts:
                run.set("contacts", "skipped", "step_off")
            else:
                run.set("contacts", "running")
                pushed = await push_contacts_to_radio(mc)
                run.result.contacts_on_radio = pushed.written
                run.result.warnings.extend(pushed.warnings)
                if pushed.unreadable:
                    run.set("contacts", "failed", "radio_contacts_unreadable")
                else:
                    if pushed.table_full:
                        run.step("contacts").items = [
                            BackupRestoreItem(
                                name=f"table_full:{pushed.table_full}",
                                status="failed",
                                code="contacts_table_full",
                            )
                        ]
                    run.set("contacts", "done")

            # 6. Wi-Fi credentials, stored in the radio (never in Meshloom).
            if doc.wifi is None:
                run.set("wifi", "skipped", "not_in_backup")
            elif not steps.wifi:
                run.set("wifi", "skipped", "step_off")
            elif not cli_ok or capabilities is None or not capabilities.wifi:
                run.set("wifi", "unsupported", "wifi_unsupported")
            else:
                run.set("wifi", "running")
                try:
                    await store_wifi_credentials(cli, doc.wifi.ssid, doc.wifi.password, typed=False)
                    if doc.wifi.enabled is not None and (
                        await wifi_enabled(cli) != doc.wifi.enabled
                    ):
                        await set_wifi_enabled(cli, doc.wifi.enabled)
                except CliError as exc:
                    run.set("wifi", "failed", exc.code)
                else:
                    run.result.reboot_required = True
                    run.set("wifi", "done")
    except (CliError, RadioOperationError):
        for name in RADIO_STEPS:
            if run.step(name).status == "running":
                run.set(name, "failed", "radio_not_connected", RERUN_HINT)
        run.skip_radio_steps("radio_not_connected", RERUN_HINT)


async def _maybe_save_ble_pin(outcome, doc: BackupExportV2) -> None:
    """After a PIN the user chose to restore was accepted on a BLE link, save it
    as the transport PIN so the next BLE connect pairs."""
    from app.repository.radio_transport import RadioTransportRepository

    if doc.radio is None or doc.radio.ble_pin is None:
        return
    if not any(item.name == "ble_pin" and item.status == "done" for item in outcome.items):
        return
    snapshot = await _transport_snapshot()
    if snapshot.transport != "ble":
        return
    snapshot.ble_pin = "" if doc.radio.ble_pin == 0 else f"{doc.radio.ble_pin:06d}"
    await RadioTransportRepository.save(snapshot)


async def _run_restore(run: _Run) -> None:
    global _running_job_id
    try:
        await _identity_step(run)
        if run.stop:
            for name in STEP_ORDER[1:]:
                if run.step(name).status == "pending":
                    run.set(name, "skipped", "identity_failed")
            run.job.state = "failed"
            return
        await _meshloom_step(run)
        await _radio_steps(run)
        run.job.state = "done"
    except Exception:
        logger.exception("Backup restore failed")
        for step in run.job.steps:
            if step.status in ("pending", "running"):
                step.status = "failed"
                step.code = "internal_error"
        run.job.state = "failed"
    finally:
        run.result.steps = [step.model_copy() for step in run.job.steps]
        run.result.warnings = list(dict.fromkeys(run.result.warnings))
        run.job.result = run.result
        run.job.finished_at = int(time.time())
        run.doc = None  # type: ignore[assignment]  # drop the secrets now
        _running_job_id = None


async def start_restore_backup_v2(
    doc: BackupV1Document | BackupExportV2, options: RestoreOptions
) -> str:
    """Check what can be checked before writing anything, then start the job.

    Refusals (another restore running, a missing identity confirmation) are
    raised here, synchronously, before any write.
    """
    global _running_job_id
    from app.background_tasks import spawn

    if _running_job_id is not None:
        raise BackupError(
            "backup_restore_running", status=409, message="A restore is already running"
        )
    job = BackupRestoreJob(
        job_id=uuid.uuid4().hex,
        started_at=int(time.time()),
        steps=[BackupRestoreStep(step=name) for name in STEP_ORDER],
    )
    # Reserved before the first await: a second POST (double click, two tabs)
    # must see it while the identity check below reads the radio.
    _running_job_id = job.job_id
    try:
        if options.steps.identity and backup_public_key(doc) is not None:
            inputs = await gather_identity_inputs()
            check_identity_confirmations(
                decide_for(doc, inputs),
                live=inputs.live,
                replace_live_identity=options.replace_live_identity,
                wipe_history=options.wipe_history,
                expected_live_public_key=options.expected_live_public_key,
            )
    except BaseException:
        _running_job_id = None
        raise
    _register(job)
    spawn(_run_restore(_Run(job, doc, options)), name=f"backup_restore_{job.job_id[:8]}")
    return job.job_id
