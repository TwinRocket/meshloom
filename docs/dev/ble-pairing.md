# BLE pairing through BlueZ

A MeshCore companion radio protects its BLE link with a static passkey (the PIN
shown on the radio). bleak's `pair()`, which `meshcore`'s `create_ble(address, pin)`
calls, registers no BlueZ agent: BlueZ then pairs as `NoInputNoOutput` and the
radio answers `org.bluez.Error.AuthenticationFailed` (upstream meshcore_py#92).
Until this change, headless users had to run `bluetoothctl pair` and `trust` by hand.

Meshloom now pairs the radio itself, over D-Bus, before handing it to meshcore.
Code: `app/services/ble_pairing.py` (sequence, errors, retry policy, status),
`app/services/ble_agent.py` (the `org.bluez.Agent1` object), `app/radio.py`
(`_connect_ble`, `ble_maintenance`), `app/routers/radio.py`
(`/radio/transport/ble-pairing`). User side: `docs/user/*/deep/transports.md`,
"Pairing".

## When it runs

`_connect_ble` calls `ensure_paired(address, pin)` when `should_prepair(address)`:
Linux, dbus-fast importable, and the saved address is a MAC
(`AA:BB:CC:DD:EE:FF`). Otherwise nothing changes. It runs only inside
`connect()`, which tears meshcore down first, under `_reconnect_lock`: meshcore
never holds the link while we pair.

## D-Bus sequence

One fresh system-bus connection per attempt (`_open_bus`, auth chosen by bleak's
`get_dbus_authenticator()`, so `BLEAK_DBUS_AUTH_UID` keeps working; no Meshloom
variable):

1. `GetManagedObjects` (10 s: bluetoothd may be D-Bus activated).
2. Pick the adapter: `Adapter1` objects that are `Powered` and have `central` in
   `Roles` (no `Roles`, i.e. older BlueZ: powered is enough), sorted by path,
   first one. A warning is logged once when several are eligible. bleak picks
   from a `set[str]` whose order depends on per-process hash randomisation, so
   its choice cannot be reproduced; instead we **pin** the adapter for meshcore
   (step 8).
3. Look the device up on that adapter only (`<adapter>/dev_AA_BB_…`, checked
   against `Address` and `Adapter`). A bond on another adapter does not count.
4. Bonded there (`Bonded` when BlueZ >= 5.70 exposes it, else `Paired`): set
   `Trusted` if needed, release an orphan link (`Connected=true` can only be a
   leftover, meshcore is not running), return `already_paired`. **No agent.**
   `Paired=true, Bonded=false` is a session-only pairing that does not survive a
   reconnect: it is paired again.
5. Unknown device: `SetDiscoveryFilter({"Transport": "le"})`, `StartDiscovery`,
   poll `GetAll(Device1)` every 0.5 s for up to 10 s, `StopDiscovery`. Known but
   not paired: no discovery, `Pair()` directly (a radio that is not advertising
   fails as unreachable).
6. Export `PairingAgent(device_path, pin)` at
   `/org/meshloom/ble_agent/<random>`, `AgentManager1.RegisterAgent(path,
   "KeyboardOnly")`, then `Device1.Pair()` **on the same connection** (25 s).
   BlueZ routes a `Pair()` to the agent registered by the same D-Bus sender
   (`agent_get(owner)` in `src/agent.c`) and allows one agent per sender. We never
   call `RequestDefaultAgent`, so a desktop agent or Home Assistant keeps its own.
7. Success: `Trusted=true`, then **`Disconnect` and wait** until `Connected=false`
   (3 s max), re-read the properties.
8. meshcore gets `create_ble(address=…, device=BLEDevice(address, name,
   {"path": device_path, "props": props}), pin=None)`.
9. Cleanup (own 4 s budget, outside the 45 s overall budget): `CancelPairing`
   after our own `Pair()` timeout, `UnregisterAgent` then unexport (a late
   `Release()` gets `UnknownMethod`, harmless), `StopDiscovery` if ours,
   `Disconnect` if a failed attempt left the link up, then close the bus.
   On cancellation (monitor stopped, shutdown) there is **no D-Bus round trip**:
   closing the connection makes BlueZ drop our agent, our discovery session and a
   pending bonding (it watches `NameOwnerChanged`).

### Why the link must be released

bleak 2.1 `BleakClient(address_str).connect()` first runs
`BleakScanner.find_device_by_address()`, which only reports devices seen in
`InterfacesAdded`/`PropertiesChanged` during its scan, never already-known
objects. A connected companion does not advertise. A link left up by `Pair()`
would therefore make meshcore fail with `BleakDeviceNotFoundError`. With a
`BLEDevice` carrying `details["path"]`, bleak skips that scan and either reuses a
live link or calls `Device1.Connect` (BlueZ waits for an advertisement itself).
`pin=None` guarantees bleak's agent-less `pair()` never runs, whatever the bleak
version. meshcore resets to the user-provided `device` on every library
reconnect, so the adapter stays pinned. Bleak 3 must keep reading
`details["path"]` (`tests/test_radio.py::TestBlePrePairing`, hardware item 17).

## The agent

`KeyboardOnly` against a `DisplayOnly` companion (Bluefruit `setPIN` + MITM on
nRF52, Bluedroid `ESP_IO_CAP_OUT` + SC MITM bond on ESP32) means passkey entry:
legacy or Secure Connections, the callback is `RequestPasskey` (`u`, 0..999999).
The PIN must match `^\d{1,6}$` (`012345` is 12345); it is checked before any
D-Bus call. `RequestPinCode` (BR/EDR only) returns the PIN if it is 1-16 printable
ASCII. `RequestConfirmation` and `RequestAuthorization` are always rejected: a
comparison value is random, never the static PIN, and remote-initiated Just Works
is not a PIN radio. `AuthorizeService` is rejected. Every request for another
device path is rejected. The agent never logs the PIN or a passkey.

## Errors

`BlePairingError(code, http_status, fallback, retry, params)`; `code` is also the
i18n key under `errors.`. `fallback=True` means environmental: `_connect_ble`
logs one warning per distinct code (then DEBUG) and keeps today's
`create_ble(address, pin)` path.

| D-Bus error / condition | code | HTTP | fallback | retry |
|---|---|---|---|---|
| PIN not 1-6 digits (before any D-Bus call) | `ble_pair_invalid_pin` | 422 | no | sticky |
| socket missing / refused / bus timeout | `ble_pair_dbus_unavailable` | 503 | yes | - |
| `ServiceUnknown`, `NameHasNoOwner`; `UnknownObject`/`UnknownMethod` on `RegisterAgent` | `ble_pair_bluez_unavailable` | 503 | yes | - |
| `AccessDenied`, `AuthError` | `ble_pair_permission_denied` | 503 | yes | - |
| no `Adapter1` (or none with the central role) | `ble_pair_no_adapter` | 503 | yes | - |
| none powered, `PowerState=off-blocked` (BlueZ >= 5.64) | `ble_pair_adapter_blocked` | 503 | no | backoff |
| none powered, `NotReady` | `ble_pair_adapter_off` | 503 | no | backoff |
| not found after discovery, `DoesNotExist`, `UnknownObject` on the device | `ble_pair_device_not_found` | 404 | no | backoff |
| `Blocked`, or the device has `Blocked=true` | `ble_pair_device_blocked` | 409 | no | sticky |
| any `Pair()` error after the agent saw an invalid PIN | `ble_pair_invalid_pin` | 422 | no | sticky |
| any `Pair()` error after `RequestConfirmation`/`RequestAuthorization` | `ble_pair_failed` (`params.error="unexpected pairing method"`, WARNING names it) | 502 | no | sticky |
| `AuthenticationFailed`/`Rejected` after `RequestPasskey` | `ble_pair_wrong_pin` | 422 | no | sticky |
| same, PIN never requested | `ble_pair_rejected` | 422 | no | sticky |
| `AuthenticationCanceled`, `ConnectionAttemptFailed`, `NotAvailable`, `Failed` with `le-connection-abort-by-local` / `Software caused connection abort` / `Page Timeout` / `Host is down` | `ble_pair_unreachable` | 503 | no | backoff |
| `AuthenticationTimeout`, `NoReply`, our own timeouts | `ble_pair_timeout` | 504 | no | backoff |
| `InProgress` on `Pair()` | `ble_pair_in_progress` | 409 | no | - |
| `AlreadyExists` on `Pair()` | success (`paired`) | - | - | - |
| `InvalidArguments`/`AlreadyExists` on `RegisterAgent`, anything else | `ble_pair_failed` (`params.error=<name>`) | 502 | no | backoff |
| not Linux / no dbus-fast | `ble_pair_unsupported` | 503 | yes | - |

BlueZ's own mapping (`device.c::new_authentication_return`): `CONNECT_FAILED` →
`ConnectionAttemptFailed`, `TIMEOUT` → `AuthenticationTimeout`,
`BUSY`/`REJECTED` → `AuthenticationRejected`, `CANCELLED`/`NO_RESOURCES`/
`DISCONNECTED` → `AuthenticationCanceled`, default → `AuthenticationFailed`. An
agent `Rejected` reply becomes an SMP "Passkey Entry Failed", hence
`AuthenticationFailed`, not `Canceled`. BlueZ >= 5.65 reports connection errors
as `org.bluez.Error.Failed` with `btd_error_le_errno` texts.

**Sticky** blocks automatic attempts for that (address, PIN) fingerprint (a
SHA-256, never the PIN) until the PIN or address changes, an explicit Pair, or
Forget. **Backoff** waits 5, 10, 20, 40, then 60 s between automatic attempts;
success, explicit Pair, Forget or a new fingerprint resets it. A blocked attempt
re-raises the stored error with `already_reported=True` and sends nothing on
D-Bus; `reconnect()` logs those at DEBUG and broadcasts nothing. A real failure
is logged once at WARNING and broadcast once as a coded WS `error`, outside the
`reconnection_failed` budget; a retry failing with the same code stays quiet.

Stale bond: after 3 consecutive connection failures following `already_paired`
(the radio was reset and lost our keys), one WS error `ble_pair_stale_bond` is
sent and the status carries `hint: "stale_bond_suspected"`. The bond is never
removed automatically.

## API

| Route | What |
|---|---|
| `GET /api/radio/transport/ble-pairing` | `RadioBlePairingStatus` from the saved transport. Problems are in `state`/`error_code`, never an HTTP error. No D-Bus when not BLE, not a MAC, or while pairing; otherwise one `GetManagedObjects` (2 s), cached 1 s. |
| `POST /api/radio/transport/ble-pairing` | Pair now with the saved PIN (`explicit`: lifts sticky/backoff). Under `lifecycle_transition` then `ble_maintenance`; disconnects meshcore first, then reconnects when the connection is wanted. Synchronous: at most 45 + 4 s. |
| `DELETE /api/radio/transport/ble-pairing` | Forget: `RemoveDevice` on every adapter (the picked one first), after disconnecting meshcore. The monitor pairs again afterwards. 409 if the saved transport is no longer BLE (an orphan bond is harmless; `bluetoothctl remove`). |
| `POST /api/radio/transport/ble-scan` | 409 `ble_pair_in_progress` while a pairing runs. |

`ble_maintenance()` holds `_reconnect_lock` and never waits: if a reconnect holds
it (the startup task or `POST /radio/reconnect`, which `lifecycle_transition`
does not cancel), it raises `RadioOperationBusyError` with code
`ble_pair_in_progress` or `radio_busy` (503). Lock order: lifecycle →
`_reconnect_lock` → radio operation lock (`disconnect`) → `ble_pairing` lock;
nothing takes the `ble_pairing` lock and then a radio lock.

`PUT /api/radio/transport` holds the lifecycle lock for pairing (up to 45 + 4 s),
`Device1.Connect` (bleak, 10 s, no scan any more) and the post-connect setup.
Other transitions get 503 after 20 s, as they already do during a slow setup. The
PUT swallows the reconnect error; the UI shows the outcome through the coded WS
toast and by polling the pairing status every 2 s while saving or pairing.

## Discovery and other clients

`StartDiscovery` returns `InProgress` only when **our** sender already discovers.
Other clients' sessions (Home Assistant, a desktop) merge silently, and our
`StopDiscovery` only ends ours, so never assume an idle adapter: on HA OS the
Bluetooth integration may keep scanning. `SetDiscoveryFilter` is per client and
disappears with the connection. A local process other than meshcore holding the
LE link to the radio would be disconnected by the orphan rule; the companion
accepts one connection anyway.

## Permissions per platform

- BlueZ D-Bus policy (distro packages, read 2026-10-17): Debian 12 (5.66),
  Debian 13 (5.82) and Fedora 44 (5.87) ship
  `<policy context="default"><allow send_destination="org.bluez"/></policy>`, so
  any local uid may call `RegisterAgent`, `Pair` and `RemoveDevice`. Debian 12 also
  allows group `bluetooth`. The deb/rpm postinstall still adds `meshloom` to
  `bluetooth` for hardened policies. Fedora and Arch use dbus-broker: same policy
  files, same result.
- Agent callbacks: BlueZ calls `org.bluez.Agent1` on our connection. This relies
  on `policy user="root"` allowing `send_interface="org.bluez.Agent1"`, valid
  because bluetoothd runs as root (Debian, RPi OS, Fedora, HA OS).
- systemd unit (`pkg/nfpm/meshloom.service`): `ProtectSystem=strict`, no
  `RestrictAddressFamilies`; connecting an AF_UNIX socket on a read-only mount
  works. Same for the Docker `:ro` socket mount written by `install.sh`.
- Docker with userns-remap or rootless: the EXTERNAL uid is not 0, still covered
  by the default context.
- AppArmor-mediated dbus-daemon (Ubuntu, HA OS add-on): a confined container may
  be denied **receiving** method calls from bluetoothd. Symptom: `Pair()` →
  `AuthenticationFailed` without any `RequestPasskey`, which maps to
  `ble_pair_rejected`; the log then hints at AppArmor. Only hardware can confirm
  (items 12 and 14); a fix would belong to the add-on or Docker profile.
- Home Assistant add-on: needs `host_dbus` (separate change). Without it the
  socket is absent and pairing falls back.

## Tests

- `tests/conftest.py` replaces `ble_pairing._open_bus` with one that raises
  `ble_pair_dbus_unavailable` and resets the module state: no test touches the
  host's system bus.
- `tests/test_ble_pairing.py`: classification table, agent helpers, sticky and
  backoff windows (frozen clock), one report per failure, stale-bond counter,
  fallback log, adapter choice.
- `tests/test_ble_pairing_dbus.py` with `tests/fake_bluez.py`: a private
  `dbus-daemon` (skipped when absent, **required when `CI` is set**) and a fake
  bluetoothd answering through a raw message handler, so it sees each caller's
  unique name like BlueZ. It models one agent per sender, `Pair()` calling
  `RequestPasskey` on the caller's agent (wrong value or agent error →
  `AuthenticationFailed`), `Connected` after `Pair()`, `Bonded` present or not,
  `Release()` after `UnregisterAgent`, and `NameOwnerChanged` (calls after the
  client left are recorded). Covers fresh pairing, already bonded, two adapters,
  discovery, wrong PIN, timeout, cancellation without round trip, forget and
  status, and that DEBUG logs never contain the PIN.
- `tests/test_radio.py::TestBlePrePairing` and
  `tests/test_radio_router.py::TestBlePairingRoutes`: the radio and route seams.

## Hardware checklist

Radio: MeshCore BLE companion, nRF52 (RAK4631/T114) and ESP32 (Heltec V3), PIN known.

1. Debian/RPi OS package, never paired: Save and connect → paired and connected;
   `bluetoothctl info` shows Paired/Bonded/Trusted yes; no ~10 s gap between
   "paired" and meshcore connecting (journal timestamps).
2. Wrong PIN: one attempt, UI "wrong PIN" and paused note, no attempt every 5 s;
   fix the PIN → connects.
3. PIN with a leading zero (`012345`).
4. Pre-paired with `bluetoothctl`: connects;
   `dbus-monitor --system "interface=org.bluez.AgentManager1"` shows no `RegisterAgent`.
5. Desktop image with a desktop agent: no popup, pairing succeeds, the desktop
   agent still works afterwards.
6. Factory-reset or re-flash the radio: connection fails; after 3 failures the
   stale-bond hint appears; Forget → pairs again → connected.
7. Forget on a radio that was **not** reset (it keeps its bond for our address):
   a new pairing is accepted, on Bluefruit/nRF52 and Bluedroid/ESP32.
8. Radio off / out of range / connected to a phone: "not found" / "did not
   answer", attempts back off up to 60 s; afterwards `busctl introspect` and
   `dbus-monitor` show the agent unregistered and discovery stopped.
9. `kill -9` the service during a pairing: the next start pairs normally.
10. Host reboot: the bond persists, reconnects without pairing.
11. Docker as root with the socket: works. Without the socket: the UI says the
    service cannot be reached; behaviour otherwise unchanged. With
    `MESHLOOM_RUN_AS_USER`: record what happens.
12. Ubuntu + Docker (AppArmor-mediated dbus): pairing works, or the
    `ble_pair_rejected` log hint fires; record which.
13. Fedora rpm.
14. HA OS add-on once `host_dbus` is merged: pairing works, HA's Bluetooth
    integration unaffected.
15. Two adapters (built-in + USB dongle): the warning is logged, pairing and
    connection use the same adapter (`bluetoothctl` shows the bond on that
    controller only).
16. `journalctl -u meshloom` at DEBUG grepped for the PIN digits: absent.
17. Repeat 1, 2 and 6 on bleak 3 (checks `BleakClient(BLEDevice)` still uses
    `details["path"]`).
