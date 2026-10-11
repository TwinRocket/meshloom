---
title: Variables and settings
description: What lives in the environment, what lives in the database.
level: deep
order: 14
---

Meshloom has two places where it is configured, and they do not overlap.

The **environment** holds what must be known before anything starts: where to write the database, whether bots are allowed, whether a password protects the page, and which diagnostic switches are on. Changing it requires a restart.

**Settings** live in the database. You change them in the web interface, and they apply immediately. The radio connection is one of them: it is a setting, not an environment variable. See [Radio transports](/en/docs/deep/transports/).

## Where variables go

| Installation | Where | After a change |
|--------------|-------|----------------|
| Linux package (`.deb` / `.rpm`) | `/etc/meshloom/meshloom.env`, owned by root: edit it with `sudo` | `sudo systemctl restart meshloom` |
| Service installed from a source checkout | No file. Add `Environment=NAME=value` lines under `[Service]` with `sudo systemctl edit meshloom` | `sudo systemctl restart meshloom` |
| Docker | `environment:` block of the Compose file, or a `.env` next to it | `docker compose up -d` |
| Checkout run by hand | The environment of the shell that runs `uv run uvicorn` | Restart the command |

Re-running `install_service.sh` rewrites the main systemd unit of a source install. A drop-in created with `systemctl edit` is kept, and `meshloom.env` is not involved.

## Radio connection

The connection is chosen in **Settings → Radio** and stored in the database:

| Setting | Default | Description |
|---------|---------|-------------|
| `radio_transport` | *(not set: paused)* | `serial`, `tcp` or `ble` |
| `radio_serial_port` | empty | Serial port. Empty means auto-detect |
| `radio_serial_baudrate` | `115200` | Serial speed |
| `radio_tcp_host` | empty | Address of the radio on the network |
| `radio_tcp_port` | `5000` | TCP port |
| `radio_ble_address` | empty | Bluetooth address of the radio |
| `radio_ble_pin` | empty | Bluetooth PIN, required with Bluetooth |

Until a connection is chosen, the radio stays paused. Do not set `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` or `MESHCORE_BLE_ADDRESS`. A new database ignores them. An existing database without a saved connection copies them once, to carry over an old setup, and never reads them again.

## Server and data

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | Path of the SQLite database |
| `MESHCORE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |
| `MESHCORE_VAPID_SUBJECT` | `mailto:noreply@meshcore.local` | Contact address used for push notifications when the field in Settings is empty (see below) |
| `MESHCORE_PUBLIC_URL` | *(empty)* | Accepted, but no feature uses it today |
| `MESHCORE_MANAGED_PORTS` | `false` | Set by a host that decides the listening ports itself, such as the Home Assistant add-on. Settings then shows the proxy port as read-only |
| `MESHCORE_RADIO_PROXY_PORT` | *(not set)* | Port of the radio proxy chosen by the host. Only used with `MESHCORE_MANAGED_PORTS=true`, where it replaces the saved port at every start. The Home Assistant add-on sets it to `5051` |
| `MESHCORE_EMBEDDABLE_SAME_ORIGIN` | `false` | Lets a page from the same origin show Meshloom in a frame. Home Assistant's sidebar does exactly that. By default Meshloom refuses to be framed, which looks like a blank panel while the log shows a healthy `200` |

The VAPID contact is best set in **Settings → Notifications**. `MESHCORE_VAPID_SUBJECT` is only used when that field is empty. Apple requires a real `mailto:` or `https:` address and rejects the `.local` default with `403 BadJwtToken`. See [Push notifications](/en/docs/deep/push/) and [Apple's documentation](https://developer.apple.com/documentation/usernotifications/sending-web-push-notifications-in-web-apps-and-browsers).

## Meshloom Community

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHLOOM_COMMUNITY` | *(on)* | Applies to a brand-new database only. Unset or `1` starts with Community on. `0`, `false`, `off` or `no` starts with it off. Existing databases are never switched |
| `MESHLOOM_COMMUNITY_IATA` | *(empty)* | Three-letter airport code. While set, it replaces the code saved in the interface |
| `MESHLOOM_COMMUNITY_BROKER_HOST` | *(empty)* | Replaces the Community publishing server (default `mqtt.meshloom.app`) while set |
| `MESHLOOM_COMMUNITY_API_BASE` | *(empty)* | Replaces the Community API address (default `https://api.meshloom.app`) while set |
| `MESHLOOM_COMMUNITY_LOCKED` | *(empty)* | Only the value `1` counts: the interface then cannot turn Community on |

See [Meshloom Community](/en/docs/deep/community/).

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_DISABLE_BOTS` | `false` | Turns the bot system off at startup. The Linux package sets it to `true` in `meshloom.env` |
| `MESHCORE_BASIC_AUTH_USERNAME` | *(empty)* | User name asked by the browser for the whole application |
| `MESHCORE_BASIC_AUTH_PASSWORD` | *(empty)* | Matching password |
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | `false` | Allows `GET /api/radio/private-key` |

The two password variables must be set together. Setting only one stops Meshloom at startup. What each option does and does not protect is explained in [Security](/en/docs/deep/security/).

## Installation and updates

The installer sets these for you. You rarely edit them by hand.

| Variable | Description |
|----------|-------------|
| `MESHLOOM_INSTALL_KIND` | How Meshloom was installed: `package`, `compose`, `addon`, `container` or `source`. It decides whether **Settings → Updates** can install an update |
| `MESHLOOM_UPDATE_HELPER` | `compose` for a Docker stack managed by the update helper, `none` to turn installing from the interface off |
| `MESHLOOM_RUN_AS_USER` | Docker only. A number such as `10001` runs Meshloom as that user instead of root. See [Other install paths](/en/docs/deep/install-paths/) |
| `MESHLOOM_IMAGE` | Docker only. The image Compose starts, ideally pinned by digest |

## Diagnostics and workarounds

These exist to diagnose or work around radios that misbehave. None is needed in normal use.

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | `false` | Makes the radio audit check for waiting messages every 10 seconds instead of every hour |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | `false` | Rewrites the channel into the radio before every channel send |
| `MESHCORE_LOAD_WITH_AUTOEVICT` | `false` | Lets the radio drop its oldest contacts when its table is full |
| `MESHCORE_SKIP_POST_CONNECT_SYNC` | `false` | After connecting, skips the contact and channel sync, the startup advert, reading the messages waiting on the radio, and the periodic jobs (sync, adverts, audit, telemetry) |
| `__CLOWNTOWN_DO_CLOCK_WRAPAROUND` | `false` | Very experimental: tries a 32-bit clock wraparound |

The audit always runs; the poll variable only changes how often. Forcing the channel rewrite makes each channel send a little slower. The skip variable is a diagnostic exit: event handlers, key export, clock sync and automatic message fetching still run. The last variable is a last resort for a radio whose clock is stuck in the future, and may not be safe on every board.

## Settings stored in the database

You change these in the web interface. Nothing here is set through the environment.

| Where | What it controls |
|-------|------------------|
| **Radio** | The connection (see above). **Messaging** tab: `max_radio_contacts`, `flood_scope`, `known_regions`, `auto_resend_channel`. **Advertising** tab: `advert_interval` (set in hours in the interface, stored in seconds; `0` turns it off, any other value is at least 1 hour) |
| **Database** | `auto_decrypt_dm_on_advert` |
| **Radio-App Management** | `blocked_keys`, `blocked_names`, `discovery_blocked_types`, `tracked_telemetry_repeaters`, `tracked_telemetry_contacts` (8 of each at most), `telemetry_interval_hours`, `telemetry_routed_hourly`, `stale_contact_days` |
| **Alerts** | `telemetry_alert_rules` |
| **Notifications** | `push_defaults`, `push_conversation_overrides`, `vapid_subject`, `notification_destinations` (email and webhook) |
| **Updates** | `auto_update`, `auto_update_window_start`, `auto_update_window_end`, `auto_update_weekdays` |
| **Local Configuration** and **Navigation** | `ui_preferences`: theme, label of the instance, left rail |
| Discovered channels | `rejected_channels`: the channels you refused |
| Not in the interface | `raw_packet_retention_days`: automatically deletes unreadable packets older than this many days (`0` keeps everything). Set it through `PATCH /api/settings` |

The VAPID key pair (`vapid_private_key` and `vapid_public_key`) is created on first start. The interface is the supported way to change settings; some of them can also be read and changed with `GET` and `PATCH /api/settings`.

Integrations (MQTT, bots, webhooks, Apprise, SQS) are stored apart, in `fanout_configs`. Meshloom Community has its own endpoint, `/api/community`. See [Fanout](/en/docs/deep/fanout/), [Meshloom Community](/en/docs/deep/community/) and [Radio, contacts and channels](/en/docs/deep/radio/).
