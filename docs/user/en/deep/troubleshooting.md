---
title: Troubleshooting
description: DEBUG logs, the support snapshot, and the radio problems that come back most often.
level: deep
order: 19
---

Start with two things: turn on `DEBUG` logs, and copy the support snapshot. Both are also what to attach when you report a bug.

## DEBUG logs

Set `MESHCORE_LOG_LEVEL=DEBUG`, then restart Meshloom.

- **Linux package or Raspberry Pi image:** add the line to `/etc/meshloom/meshloom.env`, then run `sudo systemctl restart meshloom`.
- **Docker:** add it under `environment:` in `docker-compose.yml`, then run `docker compose up -d`.
- **From a source checkout:**

```bash
MESHCORE_LOG_LEVEL=DEBUG uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-proxy-headers
```

## Support snapshot

Open **Settings > About > Open debug support snapshot** (the same data is at `/api/debug`). It lists the version, the system, the radio state, the gap between the radio and the database (contacts and channels), the companion CLI status (`companion_cli`: why the radio cannot use it, such as firmware older than v1.18 or a link through another Meshloom's radio proxy, or the result of its last check; empty until a feature has checked it), and the recent logs. Outside the logs it never contains the private key, and other keys appear only as fingerprints. The logs never contain the private key or a Wi-Fi password, even at `DEBUG`. The logs can contain channel names and channel keys at any level; at `DEBUG` they can also contain repeater and room login passwords and the Bluetooth PIN. Stop copying at the line `STOP COPYING HERE` if you do not want to share them.

## `ModuleNotFoundError: No module named 'meshcore'`

This happens when running from a source checkout. Run `uv sync` from the repository root, check that `uv --version` works, and always start the server with `uv run uvicorn ...`. The usual cause is a system-wide `/usr/bin/uvicorn` being used instead. To install `uv`: `curl -LsSf https://astral.sh/uv/install.sh | sh`.

## The radio stays paused: "No radio connected"

A new install shows the **No radio connected** banner, and the status bar says **Radio Paused**. This is normal: the link to the radio is no longer chosen during installation. Press **Connect a radio** (or **Connect** in the status bar), pick USB, TCP or Bluetooth in **Settings > Radio**, then **Save and connect**. See [First launch](/en/docs/first-run/) and [Radio transports](/en/docs/deep/transports/).

Variables such as `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` and `MESHCORE_BLE_ADDRESS` are no longer read. An existing database that had them may import them once, at the first start after an upgrade.

## "Radio Disconnected", or it never connects

1. Check the cable and the power, or the address of the radio for TCP and Bluetooth.
2. In **Settings > Radio**, check the serial port (try **Refresh ports**), the TCP host and port, or the Bluetooth address and PIN.
3. Make sure nothing else holds the radio: a serial port serves one program at a time (see "Serial port contention" below).
4. A radio running repeater firmware does not answer a client. It needs companion firmware.
5. Meshloom retries on its own every five seconds. **Reconnect** forces an attempt.

## Bluetooth pairing fails

The **Pairing** block in **Settings > Radio** (under the BLE PIN) gives the reason. See [Pairing](/en/docs/deep/transports/#pairing).

| Message | What to do |
|---|---|
| **wrong PIN** | Check the PIN shown on the radio, save it, then press **Pair again**. Automatic pairing stays paused for that PIN until then. |
| **The radio refused the pairing** | The radio was reset, or paired with another machine: press **Forget pairing**, then **Pair again**. On Ubuntu or Home Assistant OS, the system may also block Meshloom from receiving the PIN request; the log then says so. |
| **The radio refuses this machine's saved pairing** | The radio was reset or re-flashed while this machine kept its pairing: press **Forget pairing**. |
| **Radio not found** or **did not answer** | Turn the radio on, bring it closer, and disconnect any phone using it. Meshloom retries on its own, up to once a minute. |
| **The PIN must be 1 to 6 digits** | Enter the PIN as the radio shows it. |
| **The system Bluetooth service cannot be reached** | In Docker, map the host's `/run/dbus/system_bus_socket` into the container (the installer does it with Docker as root). In Home Assistant, the add-on reaches the host's bus by itself: restart it, and if the problem persists, its log has the details. Meshloom still tries to connect without pairing. |
| **bluetoothd is not running** | Start it: `sudo systemctl start bluetooth`. |
| **not allowed to pair** | The system refused Meshloom access to the Bluetooth service. Check the D-Bus policy of BlueZ, or pair with `bluetoothctl`. |
| **No Bluetooth adapter**, **adapter is turned off**, **blocked (rfkill)** | Plug in or turn on the adapter: `bluetoothctl power on`, or `rfkill unblock bluetooth`. |
| **blocked in this machine's Bluetooth settings** | Unblock the radio: `bluetoothctl unblock <address>`. |

## Dialog "This radio is not bound" or "does not match the stored identity"

Meshloom ties its database to the public key of your radio, so that it never mixes the history of two radios.

- **This radio is not bound to this instance** appears after an upgrade when the database already holds contacts or messages, even if the radio is the same. **Previous key: Unknown** only means the database is older than this check, not that the radio changed. If it is your radio, choose **Bind without wipe**: the history stays. If it is a different radio, choose **New radio**: mesh contacts, messages, stored packets and telemetry history are erased.
- **This radio does not match the stored identity** means a radio with another key is connected. Choose **Cancel** to keep the stored identity (then reconnect the original radio), or **Wipe and continue** to adopt the new one and erase the same data. There is no option to keep the history, because it belongs to the other radio.

In both cases the channels and Meshloom's settings stay.

## "Could not enumerate radio contacts", or a full contact table

A radio keeps only a limited number of contacts. Meshloom loads your favorites and recent contacts onto it so that it can acknowledge direct messages. If the table is full, you can empty it with another MeshCore app, lower **Max Contacts on Radio** in **Settings > Radio**, turn on **Let the radio evict its oldest contacts** in **Settings → Radio → Advanced** (the radio then removes old contacts by itself), or ignore the warning. **Sending and receiving messages still works.** See [Radio, contacts, and channels](/en/docs/deep/radio/).

## Messages stay on the radio

Meshloom normally receives messages as the radio announces them, and checks once an hour for ones it missed (and for channel slots that drifted). If messages still pile up on the radio, turn on **Check for messages every 10 seconds** in **Settings → Radio → Advanced**: the check then runs every 10 seconds.

## A message goes out on the wrong channel

Another app is probably changing the channel slots on the radio under Meshloom. Turn on **Rewrite the channel before every send** in **Settings → Radio → Advanced**: Meshloom then re-writes the channel on the radio before every send. It is reliable but adds a short delay to each message.

## Serial port contention

A serial port can only serve one program. If another MeshCore app, a serial console or a second Meshloom holds it, connection attempts keep failing. After three identical "Serial Connection started" lines, Meshloom writes one `WARNING` about possible contention and stops repeating the line. Close the other program.

## The start-up work loops

If the radio is unreachable for a moment, Meshloom retries its start-up work every five seconds. This is intentional. If the work exceeds five minutes twice in a row, Meshloom reports "Radio startup appears stuck": reboot the radio and restart the server. After three failures in a row, the log suggests the usual causes: another program holds the port, the radio runs repeater firmware instead of companion firmware, or it needs to be unplugged and plugged back in.

## The radio clock is stuck in the future

The radio only accepts a time later than its own. A radio whose clock is ahead (for example in 2082) refuses every time update and dates its messages and adverts with that wrong time. Meshloom fixes it in two steps, only when the clock is more than 30 seconds ahead.

**1. A reboot.** Meshloom reboots the radio once per radio and per Meshloom start, whether this happens at connection, during the periodic sync or after a change in **Settings > Radio**: boards whose clock does not survive a restart then accept the right time at the next connection. While the radio restarts, the status bar says **Radio Rebooting** and Meshloom reconnects by itself, then redoes its start-up work. You do not need to press **Reconnect**: during the reboot it only answers that the radio is rebooting. If the radio has not come back after 90 seconds, Meshloom says so and reports the reconnection errors again.

**2. A clock rollover**, if the clock is still ahead after that reboot (a board that keeps its clock across restarts). At the next connection, Meshloom sets the latest time the radio can hold (year 2106); the radio clock then rolls over to zero, and Meshloom sets the right time and checks it. This happens only when the radio is more than a year ahead of the server and its clock is more than ten years past the date of this Meshloom build (a server clock behind, even by years on an offline board, must not set a correct radio back), during the connection start-up (never during the periodic sync), and at most once per radio until Meshloom restarts, whether it worked or not. **Fix a radio clock stuck in the future**, in **Settings > Radio > Advanced**, turns it on or off; it is on by default and applies at the next connection.

The log shows each step: `Radio clock at connect` gives the radio clock before Meshloom touches it, `Radio clock still ahead after reboot` announces the rollover, and `Radio clock fixed by wraparound` confirms it. If the log says `Radio did not wrap its clock`, the board did not roll over and its clock may now read 2106: reboot the radio by hand, set its time with another MeshCore app if it has one, and turn the option off for this board. If nothing helps, the radio stays ahead and the log says so at each connection. A radio less than a year ahead after the reboot, or whose clock is not ten years past the date of this build, is left as it is (`no clock wraparound` in the log): check the server's time first.

Meshloom does neither step while the server's own clock is wrong (before 2026 or before the date of this Meshloom build, for example a board started without a network time source): the log then says `System clock looks wrong`. Set the server's time right first.

## `/docs` is not this documentation

`http://localhost:8000/docs` is the technical documentation of the API, generated by the server. The user documentation is this site. From Meshloom 5.0 that page needs you to be signed in.

## The page does not load but the API answers

The interface files are missing. From a source checkout, build them: `cd frontend && npm install && npm run build`. The server looks in `frontend/dist`, then in `frontend/prebuilt`; if neither exists, it serves only the API. Release packages already include the interface.

## Nothing updates live

The live connection (WebSocket, `/api/ws`) is not getting through your reverse proxy. Check that the proxy forwards the upgrade headers, and that its idle time-out is longer than 30 seconds (the interface sends a ping every 30 seconds). See [HTTPS](/en/docs/deep/https/).

## Report a bug

Open an issue on the [GitHub repository](https://github.com/TwinRocket/meshloom) and attach the DEBUG logs and the support snapshot.
