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
| Home Assistant add-on | No variable to edit: the add-on's options (log level, password, bots) become variables, and the add-on sets the others itself. See [On Home Assistant](/en/docs/install/#on-home-assistant) | Restart the add-on |

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
| `radio_ble_pin` | empty | Bluetooth PIN, required with Bluetooth. On Linux, Meshloom pairs with it (1 to 6 digits) |

Until a connection is chosen, the radio stays paused. Do not set `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` or `MESHCORE_BLE_ADDRESS` (nor their companions `MESHCORE_SERIAL_BAUDRATE`, `MESHCORE_TCP_PORT` and `MESHCORE_BLE_PIN`). A new database ignores them. An existing database without a saved connection copies them once, to carry over an old setup, and never reads them again. If more than one of the three is set, none is copied.

## Server and data

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | Path of the SQLite database. The Linux package sets `/var/lib/meshloom/meshcore.db`, the Home Assistant add-on `/config/meshcore.db` |
| `MESHCORE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. The private key and Wi-Fi passwords are redacted even at `DEBUG`, and the Bluetooth library's debug output stays off |
| `MESHCORE_MANAGED_PORTS` | `false` | Set by a host that decides the listening ports itself, such as the Home Assistant add-on. Settings then shows the proxy port as read-only |
| `MESHCORE_RADIO_PROXY_PORT` | *(not set)* | Port of the radio proxy chosen by the host. Only used with `MESHCORE_MANAGED_PORTS=true`, where it replaces the saved port at every start. The Home Assistant add-on sets it to `5051` |
| `MESHCORE_EMBEDDABLE_SAME_ORIGIN` | `false` | Lets a page from the same origin show Meshloom in a frame. Home Assistant's sidebar does exactly that. By default Meshloom refuses to be framed, which looks like a blank panel while the log shows a healthy `200` |

The contact address of push notifications (VAPID subject) is not a variable: set it in **Settings → Notifications**. Apple requires a real `mailto:` or `https:` address and rejects the `.local` default with `403 BadJwtToken`. See [Push notifications](/en/docs/deep/push/).

## Meshloom Community

No variable sets Community: you join and leave in **Settings → Meshloom Community**, and a new database starts with it off. Its servers are fixed (`mqtt.meshloom.app`, `api.meshloom.app`).

The former `MESHLOOM_COMMUNITY*` variables are listed under **Retired variables** below.

See [Meshloom Community](/en/docs/deep/community/).

## Security

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_DISABLE_BOTS` | `false` | Turns the bot system off at startup. Bots run Python code with full access to the machine. The Linux package sets it to `true` in `meshloom.env` |
| `MESHCORE_DISABLE_PLUGINS` | `false` | Host lock of the plugin system: when `true`, no plugin is started, whatever the settings say. It is separate from `MESHCORE_DISABLE_BOTS` |

From 5.0 `MESHCORE_BASIC_AUTH_USERNAME` and `MESHCORE_BASIC_AUTH_PASSWORD` are retired (also listed under **Retired variables**): when both are set and Meshloom has no account yet, the first start creates the administrator account from them, then they are no longer read and a warning is logged at every start while they are set (remove them; accounts are managed in **Settings → Users & Access**). Setting only one imports nothing and no longer stops Meshloom. `Authorization: Basic` with an account's user name and password is still accepted until 5.2, then removed. A user name longer than 64 characters cannot become an account: the import is skipped with an error in the log, and you create the administrator with the installation code printed in the log. What each option does and does not protect is explained in [Security](/en/docs/deep/security/).

## Installation and updates

The installer sets these for you. You rarely edit them by hand.

| Variable | Description |
|----------|-------------|
| `MESHLOOM_INSTALL_KIND` | How Meshloom was installed: `package`, `compose`, `addon`, `container` or `source`. It decides whether **Settings → Updates** can install an update |
| `MESHLOOM_UPDATE_HELPER` | `compose` for a Docker stack managed by the update helper, `none` to turn installing from the interface off |
| `MESHLOOM_RUN_AS_USER` | Docker only. A number such as `10001` runs Meshloom as that user instead of root. See [Other install paths](/en/docs/deep/install-paths/) |
| `MESHLOOM_IMAGE` | Docker only. The image Compose starts, ideally pinned by digest |

## Retired variables

These variables no longer change anything. Their settings are in the interface:

| Variable | Now in |
|----------|--------|
| `MESHCORE_VAPID_SUBJECT` | **Settings → Notifications**, VAPID subject field |
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | **Settings → Radio → Advanced**, "Check for messages every 10 seconds" |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | **Settings → Radio → Advanced**, "Rewrite the channel before every send" |
| `MESHCORE_LOAD_WITH_AUTOEVICT` | **Settings → Radio → Advanced**, "Let the radio evict its oldest contacts" |

If one is still set when you update, Meshloom copies it into its database once, at the first start: the VAPID subject only when the field in Settings is empty, a workaround only when the variable is `true`. Your setup keeps working as before. After that Meshloom never reads them again and writes a warning in its log at each start while one is still set: remove it from your environment, and change the setting in the interface from now on.

Meshloom Community no longer reads any variable either:

| Retired variable | What happens to it |
|----------|---------|
| `MESHLOOM_COMMUNITY` | Ignored. It only ever applied to a brand-new database, which now starts with Community off |
| `MESHLOOM_COMMUNITY_IATA` | On an existing installation, copied once into the settings at the first start after the upgrade, then ignored. Change the code in the settings |
| `MESHLOOM_COMMUNITY_BROKER_HOST` | Ignored. The status left on a custom broker is cleared once after the upgrade |
| `MESHLOOM_COMMUNITY_API_BASE` | Ignored |

Meshloom writes the same warning at each start while one of them is still set.

| Retired variable | What happens to it |
|----------|---------|
| `MESHCORE_BASIC_AUTH_USERNAME`, `MESHCORE_BASIC_AUTH_PASSWORD` | Read only at the first start with no account, to create the administrator; compatibility until 5.2: `Authorization: Basic` is still accepted, then removed. Accounts are managed in **Settings → Users & Access**. Same warning at each start |
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | Ignored, with the same warning. The radio's private key can always be read back: through `GET /api/radio/private-key` and in the full backup (**Settings → Database**, see [Back up your radio](/en/docs/backup/)). See [Security](/en/docs/deep/security/#the-nodes-private-key) |

## Settings stored in the database

You change these in the web interface. Nothing here is set through the environment.

| Where | What it controls |
|-------|------------------|
| **Radio** | The connection (see above). **Messaging** tab: `max_radio_contacts`, `flood_scope`, `known_regions`, `auto_resend_channel`. **Advertising** tab: `advert_interval` (set in hours in the interface, stored in seconds; `0` turns it off, any other value is at least 1 hour). **Advanced** tab: `enable_message_poll_fallback`, `force_channel_slot_reconfigure`, `load_with_autoevict`, `auto_clock_wraparound` |
| **Database** | `auto_decrypt_dm_on_advert` |
| **Radio-App Management** | `blocked_keys`, `blocked_names`, `discovery_blocked_types`, `tracked_telemetry_repeaters`, `tracked_telemetry_contacts` (8 of each at most), `telemetry_interval_hours`, `telemetry_routed_hourly`, `stale_contact_days` |
| **Alerts** | `telemetry_alert_rules` |
| **Notifications** | `push_defaults`, `push_conversation_overrides`, `vapid_subject`, `notification_destinations` (email and webhook) |
| **Updates** | `auto_update`, `auto_update_window_start`, `auto_update_window_end`, `auto_update_weekdays` |
| **Local Configuration** and **Navigation** | `ui_preferences`: theme, label of the instance, left rail; `auth_allowed_origins`: the other sites allowed to call the API (**Allowed origins**) |
| Discovered channels | `rejected_channels`: the channels you refused |
| Not in the interface | `raw_packet_retention_days`: automatically deletes unreadable packets older than this many days (`0` keeps everything). Set it through `PATCH /api/settings` |

The VAPID key pair (`vapid_private_key` and `vapid_public_key`) is created on first start. The interface is the supported way to change settings; some of them can also be read and changed with `GET` and `PATCH /api/settings`.

Integrations (MQTT, bots, webhooks, Apprise, SQS) are stored apart, in `fanout_configs`. Meshloom Community has its own endpoint, `/api/community`. See [Fanout](/en/docs/deep/fanout/), [Meshloom Community](/en/docs/deep/community/) and [Radio, contacts and channels](/en/docs/deep/radio/).
