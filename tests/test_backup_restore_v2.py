"""Full backup restore engine: the owner's order, the identity table in action,
each radio step and its failure paths, against a fake radio on the real
meshcore stack."""

from __future__ import annotations

import asyncio
import json

import pytest

import app.keystore as keystore
import app.services.backup_restore as restore_module
from app.models import (
    BackupAutoadd,
    BackupChannel,
    BackupContact,
    BackupExportV2,
    BackupFloodScope,
    BackupIdentity,
    BackupOtherParams,
    BackupRadioParams,
    BackupRestoreSteps,
    BackupTuning,
    BackupV1Document,
    BackupWifi,
    ContactUpsert,
    MeshloomExtra,
    RadioNodeSettings,
)
from app.radio import radio_manager
from app.repository import AppSettingsRepository, ContactRepository
from app.repository.radio_transport import RadioTransportRepository
from app.services.backup import BackupError, export_backup_v2, export_document
from app.services.backup_restore import (
    RestoreOptions,
    get_restore_job,
    start_restore_backup_v2,
)
from app.services.radio_ingest_gate import allow_ingest, deny_ingest, ingest_allowed
from app.services.radio_runtime import RadioRuntime
from tests.fixtures.fake_node import FakeNode, connected_node
from tests.fixtures.log_capture import assert_no_secret_logged

BACKUP_KEY = bytes(range(1, 65))
OTHER_KEY = bytes(range(100, 164))
WIFI_PASSWORD = "  sp ace d pw  "


def _pub(key: bytes) -> str:
    return FakeNode(private_key=key).public_key


P = _pub(BACKUP_KEY)
L = _pub(OTHER_KEY)


@pytest.fixture(autouse=True)
def _reset_jobs():
    keystore.clear_keys()
    restore_module._jobs.clear()
    restore_module._running_job_id = None
    yield
    restore_module._jobs.clear()
    restore_module._running_job_id = None
    keystore.clear_keys()
    deny_ingest()


class SetupStub:
    def __init__(self) -> None:
        self.result = True
        self.calls = 0
        self.time_sync = "synced"


@pytest.fixture
def setup_stub(monkeypatch):
    """Replace the real post-connect setup and the clock sync (covered elsewhere)."""
    stub = SetupStub()

    async def prepare(self, *, broadcast_on_success: bool = True) -> bool:
        stub.calls += 1
        radio_manager._setup_complete = stub.result
        if stub.result:
            allow_ingest()  # as evaluate_connected_identity does on the bound key
        return stub.result

    async def time_sync(mc, **kwargs):
        return stub.time_sync

    monkeypatch.setattr(RadioRuntime, "prepare_connected", prepare)
    monkeypatch.setattr("app.radio_sync.sync_radio_time_outcome", time_sync)
    return stub


def radio_settings(**changes) -> RadioNodeSettings:
    fields = {
        "name": "Restored",
        "lat": 45.75,
        "lon": 4.85,
        "tx_power": 18,
        "radio": BackupRadioParams(freq=869.618, bw=62.5, sf=8, cr=8),
        "client_repeat": False,
        "path_hash_mode": 1,
        "other": BackupOtherParams(
            manual_add_contacts=1,
            telemetry_mode_base=2,
            telemetry_mode_loc=1,
            telemetry_mode_env=0,
            adv_loc_policy=1,
            multi_acks=2,
        ),
        "tuning": BackupTuning(rx_delay_raw=1234, airtime_factor_raw=5678),
        "ble_pin": 222333,
        "custom_vars": {"gps": "1"},
        "default_flood_scope": BackupFloodScope(name="#lyon", key_hex="ab" * 16),
        "default_flood_scope_read": True,
        "autoadd": BackupAutoadd(config=5, max_hops=3),
        "cli_prefs": {"tz.offset": "2", "cad": "on"},
    }
    fields.update(changes)
    return RadioNodeSettings(**fields)


def make_doc(
    *,
    key: bytes | None = BACKUP_KEY,
    radio: RadioNodeSettings | None = None,
    wifi: BackupWifi | None = None,
    contacts: list[BackupContact] | None = None,
    channels: list[BackupChannel] | None = None,
    extra: MeshloomExtra | None = None,
) -> BackupExportV2:
    doc = BackupExportV2(
        exported_at=1,
        identity=BackupIdentity(
            public_key=_pub(key),
            private_key=key.hex(),  # type: ignore[arg-type]
            private_key_status="included",
        )
        if key
        else None,
        radio=radio if radio is not None else radio_settings(),
        wifi=wifi,
        contacts=contacts or [],
        channels=channels or [],
        meshloom_extra=extra,
    )
    # As read from a file.
    return BackupExportV2.model_validate(export_document(doc))


async def run(doc, **options) -> restore_module.BackupRestoreJob:
    steps = options.pop("steps", None) or BackupRestoreSteps()
    job_id = await start_restore_backup_v2(doc, RestoreOptions(steps=steps, **options))
    for _ in range(1000):
        job = get_restore_job(job_id)
        assert job is not None
        if job.state != "running":
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("restore job did not finish")


def statuses(job) -> dict[str, tuple[str, str | None]]:
    return {step.step: (step.status, step.code) for step in job.steps}


def items(job, step: str) -> dict[str, tuple[str, str | None]]:
    found = next(s for s in job.steps if s.step == step)
    return {item.name: (item.status, item.code) for item in found.items}


def contact(key: str, *, favorite: bool = False, flags: int = 0, **kw) -> BackupContact:
    return BackupContact(public_key=key, name=key[:4], type=1, flags=flags, favorite=favorite, **kw)


# -- order -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_owner_order_key_db_settings_channels_contacts_wifi(
    backup_db, setup_stub, monkeypatch
):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    doc = make_doc(
        wifi=BackupWifi(ssid="HomeNet", password=WIFI_PASSWORD),  # type: ignore[arg-type]
        contacts=[contact("11" * 32, favorite=True)],
        channels=[BackupChannel(key="CD" * 16, name="#ops", is_hashtag=True)],
    )
    import app.services.backup as backup_module

    real_restore_json = backup_module.restore_json

    async def recording_restore_json(request):
        node.log.append(("db_merge", None))
        return await real_restore_json(request)

    monkeypatch.setattr(backup_module, "restore_json", recording_restore_json)
    async with connected_node(node):
        job = await run(doc, replace_live_identity=True, expected_live_public_key=L)

    assert job.state == "done", statuses(job)
    names = node.names()
    order = [
        names.index("import_key"),
        names.index("db_merge"),
        names.index("set_name"),
        names.index("set_channel"),
        names.index("add_contact"),
        max(
            i
            for i, (name, detail) in enumerate(node.log)
            if name == "cli" and "set wifi.pwd" in detail
        ),
    ]
    assert order == sorted(order), names
    assert node.public_key == P
    assert (await RadioTransportRepository.get()).bound_public_key == P
    assert job.result is not None
    assert job.result.identity_restored is True
    assert job.result.reboot_required is True
    assert node.stored_pwd == WIFI_PASSWORD  # byte-exact, spaces kept


# -- identity table in action ----------------------------------------------------


@pytest.mark.asyncio
async def test_reflashed_radio_needs_the_replace_confirmation(backup_db, setup_stub):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    async with connected_node(node):
        with pytest.raises(BackupError) as exc:
            await run(make_doc())
        assert exc.value.code == "backup_identity_replaces_live"
        assert exc.value.params["live_public_key"] == L
    assert node.public_key == L


@pytest.mark.asyncio
async def test_history_under_the_live_key_needs_both_confirmations(backup_db, setup_stub):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    await ContactRepository.upsert(ContactUpsert(public_key="99" * 32, name="Old", type=1))
    async with connected_node(node):
        with pytest.raises(BackupError) as exc:
            await run(make_doc())
        assert exc.value.code == "backup_identity_replaces_live"
        with pytest.raises(BackupError) as exc:
            await run(make_doc(), replace_live_identity=True, expected_live_public_key=L)
        assert exc.value.code == "backup_identity_wipe_required"
        job = await run(
            make_doc(contacts=[contact("11" * 32)]),
            replace_live_identity=True,
            wipe_history=True,
            expected_live_public_key=L,
        )
    assert job.state == "done", statuses(job)
    assert await ContactRepository.get_by_key("99" * 32) is None  # L's history wiped
    assert await ContactRepository.get_by_key("11" * 32) is not None  # backup merged
    assert node.public_key == P
    assert (await RadioTransportRepository.get()).bound_public_key == P


@pytest.mark.asyncio
async def test_gate_open_on_the_bound_backup_key_imports_without_wipe(backup_db, setup_stub):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(P)
    await RadioTransportRepository.set_identity_gate(
        state="identity_mismatch", previous_public_key=P, new_public_key=L, new_name="New"
    )
    await ContactRepository.upsert(ContactUpsert(public_key="99" * 32, name="Mine", type=1))
    async with connected_node(node):
        with pytest.raises(BackupError) as exc:
            await run(make_doc())
        assert exc.value.code == "backup_identity_replaces_live"
        job = await run(make_doc(), replace_live_identity=True, expected_live_public_key=L)
    assert job.state == "done", statuses(job)
    assert node.public_key == P
    snapshot = await RadioTransportRepository.get()
    assert snapshot.identity_state is None and snapshot.bound_public_key == P
    assert await ContactRepository.get_by_key("99" * 32) is not None  # P's history kept


@pytest.mark.asyncio
async def test_legacy_gate_with_the_backup_radio_binds_without_import(backup_db, setup_stub):
    node = FakeNode(private_key=BACKUP_KEY)
    await RadioTransportRepository.set_identity_gate(
        state="identity_unbound_legacy", new_public_key=P, new_name="Base"
    )
    await ContactRepository.upsert(ContactUpsert(public_key="99" * 32, name="Legacy", type=1))
    async with connected_node(node):
        job = await run(make_doc())
    assert job.state == "done", statuses(job)
    assert "import_key" not in node.names()
    snapshot = await RadioTransportRepository.get()
    assert snapshot.identity_state is None and snapshot.bound_public_key == P
    assert await ContactRepository.get_by_key("99" * 32) is not None


@pytest.mark.asyncio
async def test_legacy_gate_with_another_radio_imports_and_keeps_history(backup_db, setup_stub):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.set_identity_gate(
        state="identity_unbound_legacy", new_public_key=L, new_name="New"
    )
    await ContactRepository.upsert(ContactUpsert(public_key="99" * 32, name="Legacy", type=1))
    async with connected_node(node):
        with pytest.raises(BackupError):
            await run(make_doc())
        job = await run(make_doc(), replace_live_identity=True, expected_live_public_key=L)
    assert job.state == "done", statuses(job)
    assert node.public_key == P
    assert await ContactRepository.get_by_key("99" * 32) is not None


@pytest.mark.asyncio
async def test_import_disabled_stops_everything_and_pauses_the_link(backup_db, setup_stub):
    node = FakeNode(private_key=OTHER_KEY)
    node.import_disabled = True
    await RadioTransportRepository.bind_public_key(P)
    await RadioTransportRepository.set_identity_gate(
        state="identity_mismatch", previous_public_key=P, new_public_key=L
    )
    async with connected_node(node):
        job = await run(
            make_doc(contacts=[contact("11" * 32)]),
            replace_live_identity=True,
            expected_live_public_key=L,
        )
        assert radio_manager.connection_desired is False
    assert job.state == "failed"
    assert statuses(job)["identity"] == ("failed", "private_key_import_disabled")
    assert statuses(job)["meshloom"] == ("skipped", "identity_failed")
    assert await ContactRepository.get_by_key("11" * 32) is None
    assert "set_name" not in node.names()
    assert (await RadioTransportRepository.get()).identity_state == "identity_mismatch"


@pytest.mark.asyncio
async def test_live_key_changed_before_the_import(backup_db, setup_stub, monkeypatch):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    async with connected_node(node):
        real = restore_module.gather_identity_inputs
        calls = 0

        async def stale_inputs():
            nonlocal calls
            calls += 1
            inputs = await real()
            if calls == 3:  # the job's check inside the transition, then a swap
                node.private_key = bytes(range(150, 214))
            return inputs

        monkeypatch.setattr(restore_module, "gather_identity_inputs", stale_inputs)
        job = await run(make_doc(), replace_live_identity=True, expected_live_public_key=L)
    assert statuses(job)["identity"] == ("failed", "live_identity_changed")
    assert "import_key" not in node.names()


@pytest.mark.asyncio
async def test_history_appearing_after_the_check_stops_the_job(backup_db, setup_stub):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    async with connected_node(node):
        job_id = await start_restore_backup_v2(
            make_doc(),
            RestoreOptions(
                steps=BackupRestoreSteps(), replace_live_identity=True, expected_live_public_key=L
            ),
        )
        # Adverts fill contacts before the job reaches the identity step.
        await ContactRepository.upsert(ContactUpsert(public_key="77" * 32, name="Adv", type=1))
        for _ in range(500):
            job = get_restore_job(job_id)
            assert job is not None
            if job.state != "running":
                break
            await asyncio.sleep(0.01)
    assert statuses(job)["identity"] == ("failed", "backup_identity_wipe_required")
    assert "import_key" not in node.names()
    assert await ContactRepository.get_by_key("77" * 32) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("with_history", [False, True])
async def test_radio_not_connected_never_binds_the_backup_key(backup_db, setup_stub, with_history):
    from app.services.radio_identity import evaluate_connected_identity

    if with_history:
        await ContactRepository.upsert(ContactUpsert(public_key="99" * 32, name="Old", type=1))
    job = await run(make_doc(contacts=[contact("11" * 32)]))
    assert statuses(job)["identity"] == ("skipped", "radio_not_connected")
    assert statuses(job)["meshloom"] == ("done", None)
    for step in ("radio_settings", "channels", "contacts", "wifi"):
        assert statuses(job)[step] == ("skipped", "radio_not_connected")
    assert (await RadioTransportRepository.get()).bound_public_key is None

    # The next radio (L != P) opens the legacy gate, not a mismatch.
    node = FakeNode(private_key=OTHER_KEY)
    async with connected_node(node) as (loop, _n):
        assert await evaluate_connected_identity(loop.mc) == "stop"
    snapshot = await RadioTransportRepository.get()
    assert snapshot.identity_state == "identity_unbound_legacy"
    assert snapshot.mismatch_new_public_key == L
    assert await ContactRepository.get_by_key("11" * 32) is not None


@pytest.mark.asyncio
async def test_bound_to_another_key_with_no_radio_is_refused(backup_db, setup_stub):
    await RadioTransportRepository.bind_public_key("cc" * 32)
    with pytest.raises(BackupError) as exc:
        await run(make_doc())
    assert exc.value.code == "backup_identity_needs_radio"


@pytest.mark.asyncio
async def test_setup_not_back_after_the_import(backup_db, setup_stub):
    setup_stub.result = False
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    async with connected_node(node):
        job = await run(
            make_doc(contacts=[contact("11" * 32)]),
            replace_live_identity=True,
            expected_live_public_key=L,
        )
    assert statuses(job)["identity"][0] == "done"
    assert statuses(job)["meshloom"] == ("done", None)
    assert statuses(job)["radio_settings"] == ("skipped", "radio_not_connected")
    assert job.steps[2].detail and "again" in job.steps[2].detail


# -- the ingest gate after the identity step ---------------------------------------
#
# Entering the lifecycle transition closes ingest and only the post-connect
# setup opens it again: a restore that runs no setup must leave ingest open.


@pytest.mark.asyncio
async def test_same_identity_keeps_ingest_open(backup_db, setup_stub):
    node = await _same_identity_node()
    async with connected_node(node):
        allow_ingest()
        job = await run(make_doc())
        assert ingest_allowed() is True
    assert statuses(job)["identity"] == ("done", "already_this_identity")
    assert setup_stub.calls == 0


@pytest.mark.asyncio
async def test_import_refused_without_gate_reopens_ingest(backup_db, setup_stub):
    node = FakeNode(private_key=OTHER_KEY)
    node.import_disabled = True
    await RadioTransportRepository.bind_public_key(L)
    async with connected_node(node):
        allow_ingest()
        job = await run(make_doc(), replace_live_identity=True, expected_live_public_key=L)
        assert ingest_allowed() is True
        assert radio_manager.connection_desired is True
    assert statuses(job)["identity"] == ("failed", "private_key_import_disabled")
    assert setup_stub.calls == 0  # same session, same key: no setup needed


@pytest.mark.asyncio
async def test_refusal_inside_the_transition_reopens_ingest(backup_db, setup_stub, monkeypatch):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    real = restore_module.gather_identity_inputs
    calls = 0

    async def history_appears():
        nonlocal calls
        calls += 1
        if calls == 3:  # inside the transition: adverts filled the history
            await ContactRepository.upsert(ContactUpsert(public_key="77" * 32, name="Adv", type=1))
        return await real()

    monkeypatch.setattr(restore_module, "gather_identity_inputs", history_appears)
    async with connected_node(node):
        allow_ingest()
        job = await run(make_doc(), replace_live_identity=True, expected_live_public_key=L)
        assert ingest_allowed() is True
    assert statuses(job)["identity"] == ("failed", "backup_identity_wipe_required")
    assert "import_key" not in node.names()


@pytest.mark.asyncio
async def test_swapped_radio_after_the_gate_snapshot_never_wipes(backup_db, setup_stub):
    """Row 9 rests on the gate snapshot (live == P); the reconnected radio is L."""
    node = FakeNode(private_key=OTHER_KEY)
    b_key = "cc" * 32
    await RadioTransportRepository.bind_public_key(b_key)
    await RadioTransportRepository.set_identity_gate(
        state="identity_mismatch", previous_public_key=b_key, new_public_key=P, new_name="P"
    )
    await ContactRepository.upsert(ContactUpsert(public_key="99" * 32, name="B's", type=1))
    async with connected_node(node):
        job = await run(make_doc(), wipe_history=True)
        assert radio_manager.connection_desired is False  # paused on the open gate
    assert statuses(job)["identity"] == ("failed", "live_identity_changed")
    assert await ContactRepository.get_by_key("99" * 32) is not None
    snapshot = await RadioTransportRepository.get()
    assert snapshot.bound_public_key == b_key
    assert snapshot.identity_state == "identity_mismatch"
    assert "import_key" not in node.names()


# -- radio steps -----------------------------------------------------------------


async def _same_identity_node(**node_changes) -> FakeNode:
    node = FakeNode(private_key=BACKUP_KEY)
    for key, value in node_changes.items():
        setattr(node, key, value)
    await RadioTransportRepository.bind_public_key(P)
    return node


@pytest.mark.asyncio
async def test_every_radio_setting_is_written_back(backup_db, setup_stub):
    node = await _same_identity_node()
    async with connected_node(node):
        job = await run(make_doc())
    assert statuses(job)["identity"] == ("done", "already_this_identity")
    assert statuses(job)["radio_settings"] == ("done", None)
    assert node.name == "Restored"
    assert (node.lat, node.lon) == (45.75, 4.85)
    assert node.tx_power == 18
    assert (node.freq, node.bw, node.sf, node.cr, node.repeat) == (869.618, 62.5, 8, 8, False)
    assert node.other == {
        "manual_add_contacts": 1,
        "telemetry": 0b000110,
        "adv_loc_policy": 1,
        "multi_acks": 2,
    }
    assert node.tuning == (1234, 5678)
    assert node.path_hash_mode == 1
    assert node.custom_vars["gps"] == "1"
    assert node.default_scope == ("#lyon", bytes.fromhex("ab" * 16))
    assert node.autoadd == (5, 3)
    assert node.ble_pin == 222333
    assert node.prefs["tz.offset"] == "2" and node.prefs["cad"] == "on"


@pytest.mark.asyncio
async def test_rejected_groups_fail_alone(backup_db, setup_stub):
    node = await _same_identity_node()
    node.reject = {0x08}  # set name
    async with connected_node(node):
        job = await run(make_doc(radio=radio_settings(tx_power=40)))
    found = items(job, "radio_settings")
    assert found["name"] == ("failed", "name_rejected")
    assert found["tx_power"] == ("done", None)  # clamped to the radio's max
    assert node.tx_power == node.max_tx_power
    assert found["radio_params"] == ("done", None)
    assert statuses(job)["radio_settings"] == ("failed", None)
    assert job.result is not None and "tx_power_clamped" in job.result.warnings


@pytest.mark.asyncio
async def test_repeat_refused_on_the_target_retries_without(backup_db, setup_stub):
    node = await _same_identity_node(repeat_allowed=False)
    async with connected_node(node):
        job = await run(make_doc(radio=radio_settings(client_repeat=True)))
    assert items(job, "radio_settings")["radio_params"] == ("done", "client_repeat_not_allowed")
    assert node.repeat is False and node.sf == 8
    assert "client_repeat_not_allowed" in job.result.warnings  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_path_hash_mode_unsupported(backup_db, setup_stub):
    node = await _same_identity_node()
    async with connected_node(node):
        radio_manager.path_hash_mode_supported = False
        job = await run(make_doc())
    assert items(job, "radio_settings")["path_hash_mode"] == (
        "unsupported",
        "path_hash_mode_unsupported",
    )


@pytest.mark.asyncio
async def test_clock_reboot_skips_the_rest(backup_db, setup_stub):
    setup_stub.time_sync = "rebooted"
    node = await _same_identity_node()
    async with connected_node(node):
        job = await run(make_doc(contacts=[contact("11" * 32)]))
    assert statuses(job)["radio_settings"] == ("done", None)
    for step in ("channels", "contacts", "wifi"):
        assert statuses(job)[step] == ("skipped", "radio_rebooting")


@pytest.mark.asyncio
async def test_path_hash_opt_in_only_from_meshloom_extra(backup_db, setup_stub):
    await AppSettingsRepository.set_path_hash_one_byte_opt_in(True)
    node = await _same_identity_node()
    async with connected_node(node):
        await run(make_doc(radio=radio_settings(path_hash_mode=0)))
    assert await AppSettingsRepository.get_path_hash_one_byte_opt_in() is True
    async with connected_node(node):
        await run(make_doc(extra=MeshloomExtra(path_hash_one_byte_opt_in=False)))
    assert await AppSettingsRepository.get_path_hash_one_byte_opt_in() is False


@pytest.mark.asyncio
async def test_channels_raw_frames_keep_private_secrets(backup_db, setup_stub):
    node = await _same_identity_node()
    long_name = "#" + "é" * 15 + "abcdefghi"  # 1 + 30 + 9 bytes
    secret = "0F" * 16
    async with connected_node(node):
        job = await run(
            make_doc(
                channels=[
                    BackupChannel(key=secret, name="#private", is_hashtag=False, favorite=True),
                    BackupChannel(key="1E" * 16, name=long_name, is_hashtag=False),
                ]
            )
        )
    assert statuses(job)["channels"] == ("done", None)
    frames = {
        detail[1].rstrip(b"\x00").decode(): detail[2]
        for name, detail in node.log
        if name == "set_channel"
    }
    assert frames["#private"] == bytes.fromhex(secret)  # never derived from the name
    cut = next(raw for raw in frames if raw.startswith("#é"))
    assert len(cut.encode()) <= 31
    assert cut == "#" + "é" * 15  # the 2-byte character crossing byte 31 dropped whole
    raw = next(d[1] for n, d in node.log if n == "set_channel" and d[1].startswith(b"#\xc3"))
    assert len(raw) == 32
    assert job.result is not None and job.result.channels_on_radio >= 2


@pytest.mark.asyncio
async def test_channels_skipped_without_device_info(backup_db, setup_stub):
    node = await _same_identity_node()
    async with connected_node(node):
        radio_manager.device_info_loaded = False
        job = await run(make_doc())
    assert statuses(job)["channels"] == ("skipped", "device_info_unknown")


@pytest.mark.asyncio
async def test_contacts_favourites_first_within_the_limit(backup_db, setup_stub):
    await AppSettingsRepository.update(max_radio_contacts=3)
    node = await _same_identity_node()
    node.add_contact("ee" * 32, name="auto-added")  # not selected: removed first
    # Three favourites and two recents; the radio may hold three.
    contacts = [
        contact(f"{i:02x}" * 32, favorite=i <= 3, last_advert=1_700_000_000 + i)
        for i in range(1, 6)
    ]
    async with connected_node(node):
        job = await run(make_doc(contacts=contacts))
    assert statuses(job)["contacts"] == ("done", None)
    assert set(node.contacts) == {f"{i:02x}" * 32 for i in range(1, 4)}
    names = node.names()
    assert names.index("remove_contact") < names.index("add_contact")
    for i in range(1, 4):
        assert node.contacts[f"{i:02x}" * 32]["flags"] & 0x01
    assert job.result is not None and job.result.contacts_on_radio == 3


@pytest.mark.asyncio
async def test_contacts_flags_and_updates(backup_db, setup_stub):
    node = await _same_identity_node()
    present = "21" * 32
    node.add_contact(present, flags=0x10)
    async with connected_node(node):
        await run(
            make_doc(
                contacts=[
                    contact(
                        present, favorite=True, flags=0x10, direct_path="a1b2", direct_path_len=2
                    ),
                    contact("22" * 32, flags=0x12, last_advert=1_700_000_000),
                ]
            )
        )
    assert ("update_contact", (present, 0x01)) in node.log  # 0x10 stripped, favourite 0x01
    assert node.contacts[present]["out_path"] == bytes.fromhex("a1b2")
    assert node.contacts["22" * 32]["flags"] == 0x02
    assert (await ContactRepository.get_by_key(present)).flags & 0x10 == 0  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_autoevict_clears_the_favourite_bit(backup_db, setup_stub):
    await AppSettingsRepository.update(load_with_autoevict=True)
    node = await _same_identity_node()
    async with connected_node(node):
        await run(make_doc(contacts=[contact("31" * 32, favorite=True, flags=0x01)]))
    assert node.contacts["31" * 32]["flags"] & 0x01 == 0


@pytest.mark.asyncio
async def test_table_full_is_a_sub_item_failure(backup_db, setup_stub):
    node = await _same_identity_node(max_contacts=2)
    contacts = [contact(f"{i:02x}" * 32, favorite=True) for i in range(1, 5)]
    async with connected_node(node):
        radio_manager.max_contacts = 10  # what Meshloom believes; the table holds 2
        job = await run(make_doc(contacts=contacts))
    assert statuses(job)["contacts"] == ("done", None)
    [item] = next(s for s in job.steps if s.step == "contacts").items
    assert item.code == "contacts_table_full" and item.status == "failed"
    assert statuses(job)["wifi"][0] in ("skipped", "done")


@pytest.mark.asyncio
async def test_reconcile_stopped_then_restarted(backup_db, setup_stub, monkeypatch):
    from app import radio_sync

    calls: list[str] = []
    real_stop = radio_sync.stop_background_contact_reconciliation
    real_start = radio_sync.start_background_contact_reconciliation

    async def stop():
        calls.append("stop")
        await real_stop()

    def start(**kwargs):
        calls.append("start")
        real_start(**kwargs)

    monkeypatch.setattr(radio_sync, "stop_background_contact_reconciliation", stop)
    monkeypatch.setattr(radio_sync, "start_background_contact_reconciliation", start)
    node = await _same_identity_node()
    async with connected_node(node):
        await run(make_doc(contacts=[contact("41" * 32)]))
    assert calls[:2] == ["stop", "start"]


@pytest.mark.asyncio
async def test_meshloom_step_off_skips_channels_and_contacts(backup_db, setup_stub):
    node = await _same_identity_node()
    async with connected_node(node):
        job = await run(make_doc(), steps=BackupRestoreSteps(meshloom=False))
    assert statuses(job)["channels"] == ("skipped", "meshloom_step_off")
    assert statuses(job)["contacts"] == ("skipped", "meshloom_step_off")


@pytest.mark.asyncio
async def test_firmware_without_cli(backup_db, setup_stub):
    node = await _same_identity_node(fw_ver=12)
    async with connected_node(node):
        job = await run(make_doc(wifi=BackupWifi(ssid="HomeNet", password="secret-pw")))  # type: ignore[arg-type]
    assert statuses(job)["wifi"] == ("unsupported", "wifi_unsupported")
    assert items(job, "radio_settings")["cli_prefs"] == ("unsupported", "cli_unsupported")
    assert items(job, "radio_settings")["name"] == ("done", None)


@pytest.mark.asyncio
async def test_ble_pin_on_a_ble_link(backup_db, setup_stub):
    node = await _same_identity_node()
    snapshot = await RadioTransportRepository.get()
    snapshot.transport = "ble"
    snapshot.ble_address = "AA:BB:CC:DD:EE:FF"
    snapshot.ble_pin = "123456"
    await RadioTransportRepository.save(snapshot)
    async with connected_node(node):
        job = await run(make_doc(), steps=BackupRestoreSteps(ble_pin=False))
    assert items(job, "radio_settings")["ble_pin"] == ("skipped", "step_off")
    assert node.ble_pin == 123456
    async with connected_node(node):
        job = await run(make_doc())
    assert items(job, "radio_settings")["ble_pin"] == ("done", None)
    assert node.ble_pin == 222333
    assert (await RadioTransportRepository.get()).ble_pin == "222333"


@pytest.mark.asyncio
async def test_v1_file_restores_meshloom_only(backup_db, setup_stub):
    node = await _same_identity_node()
    doc = BackupV1Document(contacts=[contact("51" * 32)])
    async with connected_node(node):
        job = await run(doc)
    assert statuses(job)["identity"] == ("skipped", "v1_backup")
    assert statuses(job)["meshloom"] == ("done", None)
    assert statuses(job)["radio_settings"] == ("skipped", "v1_backup")
    assert node.log == [] or all(name == "cli" for name, _ in node.log)


@pytest.mark.asyncio
async def test_ui_preferences_kept_when_the_file_has_none(backup_db, setup_stub):
    from app.models import AppSettings, UiPreferences

    await AppSettingsRepository.update(
        ui_preferences=UiPreferences.model_validate({"theme": "dark"})
    )
    before = (await AppSettingsRepository.get()).ui_preferences
    doc = BackupV1Document.model_validate(
        {"format": "meshloom-backup-v1", "settings": {"max_radio_contacts": 42}}
    )
    await run(doc)
    after = await AppSettingsRepository.get()
    assert after.max_radio_contacts == 42
    assert after.ui_preferences == before

    full = AppSettings.model_validate(
        {**(await AppSettingsRepository.get()).model_dump(mode="json"), "ui_preferences": {}}
    )
    await run(BackupV1Document(settings=full))
    assert (await AppSettingsRepository.get()).ui_preferences == UiPreferences.model_validate({})


@pytest.mark.asyncio
async def test_one_restore_at_a_time(backup_db, setup_stub):
    restore_module._running_job_id = "busy"
    with pytest.raises(BackupError) as exc:
        await run(make_doc(key=None))
    assert exc.value.code == "backup_restore_running" and exc.value.status == 409


@pytest.mark.asyncio
async def test_two_posts_racing_the_identity_check_start_one_job(
    backup_db, setup_stub, monkeypatch
):
    release = asyncio.Event()
    real = restore_module.gather_identity_inputs

    async def slow_inputs():
        await release.wait()
        return await real()

    monkeypatch.setattr(restore_module, "gather_identity_inputs", slow_inputs)
    first = asyncio.ensure_future(
        start_restore_backup_v2(make_doc(), RestoreOptions(steps=BackupRestoreSteps()))
    )
    await asyncio.sleep(0)
    with pytest.raises(BackupError) as exc:
        await start_restore_backup_v2(make_doc(), RestoreOptions(steps=BackupRestoreSteps()))
    assert exc.value.code == "backup_restore_running"
    release.set()
    job_id = await first
    for _ in range(500):
        if not restore_module.restore_job_running():
            break
        await asyncio.sleep(0.01)
    assert get_restore_job(job_id) is not None


@pytest.mark.asyncio
async def test_refused_identity_check_releases_the_slot(backup_db, setup_stub):
    await RadioTransportRepository.bind_public_key("cc" * 32)
    with pytest.raises(BackupError):
        await run(make_doc())
    assert restore_module.restore_job_running() is False


@pytest.mark.asyncio
async def test_radio_lost_mid_step_fails_the_running_step(backup_db, setup_stub, monkeypatch):
    from app.radio import RadioOperationError

    async def lost(mc):
        raise RadioOperationError("gone")

    monkeypatch.setattr("app.services.backup_radio.push_channels_to_radio", lost)
    node = await _same_identity_node()
    async with connected_node(node):
        job = await run(make_doc(channels=[BackupChannel(key="CD" * 16, name="#ops")]))
    assert statuses(job)["channels"] == ("failed", "radio_not_connected")
    assert statuses(job)["contacts"] == ("skipped", "radio_not_connected")
    assert all(step.status != "running" for step in job.steps)


@pytest.mark.asyncio
async def test_finished_job_holds_no_secret_and_logs_none(backup_db, setup_stub, secret_debug_logs):
    node = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    doc = make_doc(wifi=BackupWifi(ssid="HomeNet", password="Wifi-S3cret-9"))  # type: ignore[arg-type]
    async with connected_node(node) as (loop, _n):
        job = await run(doc, replace_live_identity=True, expected_live_public_key=L)
        await loop.settle()
    assert job.state == "done", statuses(job)
    dumped = json.dumps(job.model_dump(mode="json"))
    assert BACKUP_KEY.hex() not in dumped and "Wifi-S3cret-9" not in dumped
    assert node.stored_pwd == "Wifi-S3cret-9"
    assert_no_secret_logged(secret_debug_logs, "Wifi-S3cret-9", BACKUP_KEY)


@pytest.mark.asyncio
async def test_export_then_restore_round_trip(backup_db, setup_stub):
    source = FakeNode(private_key=BACKUP_KEY)
    source.stored_ssid, source.stored_pwd = "Net 5G", " a b c "
    keystore.set_private_key(BACKUP_KEY)
    async with connected_node(source):
        exported = export_document(await export_backup_v2())
    doc = BackupExportV2.model_validate(json.loads(json.dumps(exported)))
    target = FakeNode(private_key=OTHER_KEY)
    await RadioTransportRepository.bind_public_key(L)
    async with connected_node(target):
        job = await run(doc, replace_live_identity=True, expected_live_public_key=L)
    assert job.state == "done", statuses(job)
    assert target.public_key == P
    assert (target.stored_ssid, target.stored_pwd) == ("Net 5G", " a b c ")


@pytest.fixture
async def file_db(tmp_path, monkeypatch):
    """A file-backed WAL database, as in production."""
    import app.database as database_module
    import app.services.radio_identity as radio_identity
    from tests.conftest import patched_database

    path = tmp_path / "meshcore.db"
    async with patched_database(str(path)) as db:
        monkeypatch.setattr(database_module, "db", db)
        monkeypatch.setattr(radio_identity, "db", db)
        yield db, path


@pytest.mark.asyncio
async def test_the_wifi_password_never_reaches_the_database_files(file_db, setup_stub):
    db, path = file_db
    secret = "Wal-Never-9x"
    source = FakeNode(private_key=BACKUP_KEY)
    source.stored_ssid, source.stored_pwd = "HomeNet", secret
    keystore.set_private_key(BACKUP_KEY)
    await RadioTransportRepository.bind_public_key(P)
    async with connected_node(source):
        exported = export_document(await export_backup_v2())
        job = await run(BackupExportV2.model_validate(exported))
    assert job.state == "done", statuses(job)
    assert source.stored_pwd == secret
    async with db.tx() as conn:
        await conn.execute("PRAGMA wal_checkpoint(FULL)")
    for suffix in ("", "-wal", "-shm"):
        candidate = path.with_name(path.name + suffix)
        if candidate.exists():
            assert secret.encode() not in candidate.read_bytes(), candidate.name
