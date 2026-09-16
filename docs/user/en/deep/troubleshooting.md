---
title: Troubleshooting
description: DEBUG logs, /api/debug, and the radio cases that come back often.
level: deep
order: 19
---

Start with `DEBUG` logs and the support snapshot.

## DEBUG logs

```bash
MESHCORE_LOG_LEVEL=DEBUG uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

For a package, put the variable in `/etc/meshloom/meshloom.env` and restart `meshloom`. For Docker, put it in Compose `environment:`.

## Support snapshot

`/api/debug` is also available under **Settings > About > Open debug support snapshot**. It includes runtime information, radio state, contact/channel drift, version, git details, and recent logs. Non-log data does not include keys or channel names; logs can include channel names or keys but never the private key. Stop copying at `STOP COPYING HERE` if you do not want to share logs.

## `ModuleNotFoundError: No module named 'meshcore'`

Run `uv sync` from the repository root, verify `uv --version`, and always use `uv run uvicorn ...`. A system `/usr/bin/uvicorn` is the usual cause. Install `uv` with `curl -LsSf https://astral.sh/uv/install.sh | sh`.

## Radio paused, transport not configured

The status bar stays paused and **Connect** opens **Settings > Radio**. That is expected on a new install: transport is no longer chosen at install time. Set USB, TCP, or BLE there and apply. See [First launch](/en/docs/first-run/) and [Radio transports](/en/docs/deep/transports/).

Leftover `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST`, and `MESHCORE_BLE_ADDRESS` are no longer read at runtime. An existing database may import them once on the first startup after the upgrade.

## “Radio not bound” or identity-mismatch dialog

After an upgrade, a database that already has contacts or messages asks for confirmation even when the radio is the same. **Bind without wiping** keeps history. **New radio** wipes it. **Previous key: unknown** means the bind did not exist yet, not that another device is connected.

If two different keys are shown, the radio changed. Bind-without-wipe is not offered.

## Full contact table

Meshloom loads favorites and recent contacts so the radio can ACK DMs. If the table is full, empty it with another MeshCore client, lower the target, enable `MESHCORE_LOAD_WITH_AUTOEVICT=true`, or ignore the warning. **Messaging is not affected.** See [Radio, contacts, and channels](/en/docs/deep/radio/).

## Messages stay on the radio

The hourly audit checks missed event messages and channel-slot drift. `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true` changes it to a 10-second poll.

## Wrong channel slot

`MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true` forces `set_channel(...)` before every send, at a cost of about 500 ms.

## Serial port contention

Serial is exclusive. Another client, console, or Meshloom instance causes repeated failures. Meshloom groups repeated `Serial Connection started` lines and logs a `WARNING` about possible contention.

## Post-connect setup loops

Setup retries every five seconds if the radio is temporarily unavailable. This is intentional. A timeout logs an error and asks for a radio and server restart.

## Radio clock stuck in the future

`__CLOWNTOWN_DO_CLOCK_WRAPAROUND=true` is an experimental last resort when no rescue mode or GPS time is available. It is hardware-dependent and may not be safe or effective.

## Windows empty packet

`Received empty packet: index out of range` with contact-sync failure is an intermittent Windows startup issue. Restarting usually resolves it.

## Advert parsing error

A malformed or truncated RF advert can cause `IndexError: index out of range` in `meshcore/reader.py`. This is a parser-hardening issue, not database corruption; later packets normally continue.

## `/docs` is not this site

FastAPI serves generated interactive API documentation at `http://localhost:8000/docs`. This site is the user documentation.

## UI missing while API works

Build the frontend: `cd frontend && npm install && npm run build`. The backend falls back from `frontend/dist` to `frontend/prebuilt`; if neither exists it serves only the API.

## Nothing arrives live

The WebSocket `/api/ws` is not passing through the proxy. Check upgrade headers and idle timeouts shorter than 30 seconds. See [HTTPS](/en/docs/deep/https/).

## Report a bug

Include DEBUG logs and `/api/debug` in an issue on the [GitHub repository](https://github.com/TwinRocket/meshloom).
