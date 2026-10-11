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
MESHCORE_LOG_LEVEL=DEBUG uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Support snapshot

Open **Settings > About > Open debug support snapshot** (the same data is at `/api/debug`). It lists the version, the system, the radio state, the gap between the radio and the database (contacts and channels), and the recent logs. Outside the logs it never contains the private key, and other keys appear only as fingerprints. The logs can contain channel names or keys, but never the private key. Stop copying at the line `STOP COPYING HERE` if you do not want to share them.

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

## Dialog "This radio is not bound" or "does not match the stored identity"

Meshloom ties its database to the public key of your radio, so that it never mixes the history of two radios.

- **This radio is not bound to this instance** appears after an upgrade when the database already holds contacts or messages, even if the radio is the same. **Previous key: Unknown** only means the database is older than this check, not that the radio changed. If it is your radio, choose **Bind without wipe**: the history stays. If it is a different radio, choose **New radio**: mesh contacts and messages are erased.
- **This radio does not match the stored identity** means a radio with another key is connected. Choose **Cancel** to keep the stored identity (then reconnect the original radio), or **Wipe and continue** to adopt the new one and erase mesh contacts and messages. There is no option to keep the history, because it belongs to the other radio.

In both cases the channels and Meshloom's settings stay.

## "Could not enumerate radio contacts", or a full contact table

A radio keeps only a limited number of contacts. Meshloom loads your favorites and recent contacts onto it so that it can acknowledge direct messages. If the table is full, you can empty it with another MeshCore app, lower **Max Contacts on Radio** in **Settings > Radio**, set `MESHCORE_LOAD_WITH_AUTOEVICT=true` (the radio then removes old contacts by itself), or ignore the warning. **Sending and receiving messages still works.** See [Radio, contacts, and channels](/en/docs/deep/radio/).

## Messages stay on the radio

Meshloom normally receives messages as the radio announces them, and checks once an hour for ones it missed (and for channel slots that drifted). If messages still pile up on the radio, set `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true`: the check then runs every 10 seconds.

## A message goes out on the wrong channel

Another app is probably changing the channel slots on the radio under Meshloom. Set `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true`: Meshloom then re-writes the channel on the radio before every send. It is reliable but adds a short delay to each message.

## Serial port contention

A serial port can only serve one program. If another MeshCore app, a serial console or a second Meshloom holds it, connection attempts keep failing. After three identical "Serial Connection started" lines, Meshloom writes one `WARNING` about possible contention and stops repeating the line. Close the other program.

## The start-up work loops

If the radio is unreachable for a moment, Meshloom retries its start-up work every five seconds. This is intentional. If the work exceeds five minutes twice in a row, Meshloom reports "Radio startup appears stuck": reboot the radio and restart the server. After three failures in a row, the log suggests the usual causes: another program holds the port, the radio runs repeater firmware instead of companion firmware, or it needs to be unplugged and plugged back in.

## The radio clock is stuck in the future

`__CLOWNTOWN_DO_CLOCK_WRAPAROUND=true` is an experimental last resort, for when no rescue mode or GPS time is available to reset the clock. It depends on the hardware and may do nothing. Use it knowingly.

## `/docs` is not this documentation

`http://localhost:8000/docs` is the technical documentation of the API, generated by the server. The user documentation is this site.

## The page does not load but the API answers

The interface files are missing. From a source checkout, build them: `cd frontend && npm install && npm run build`. The server looks in `frontend/dist`, then in `frontend/prebuilt`; if neither exists, it serves only the API. Release packages already include the interface.

## Nothing updates live

The live connection (WebSocket, `/api/ws`) is not getting through your reverse proxy. Check that the proxy forwards the upgrade headers, and that its idle time-out is longer than 30 seconds (the interface sends a ping every 30 seconds). See [HTTPS](/en/docs/deep/https/).

## Report a bug

Open an issue on the [GitHub repository](https://github.com/TwinRocket/meshloom) and attach the DEBUG logs and the support snapshot.
