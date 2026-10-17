"""Restore of a full backup: the identity decision table, inspect, and the job.

``decide_identity_restore`` is the single source of truth for what a restore
may do with the radio identity (import the backup key, wipe the history
recorded under another key, bind the backup key). Inspect, the synchronous
check before a job starts and the job itself (right before the import) all
call it, so a confirmation asked in the UI always matches what runs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

from app.models import (
    BackupCounts,
    BackupEnvelope,
    BackupExportV2,
    BackupIdentityRelation,
    BackupIdentityRequires,
    BackupInspectResult,
    BackupV1Document,
    RadioTransportSnapshot,
)
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
    from app.radio import RadioOperationError
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
    from app.radio import RadioOperationError
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
