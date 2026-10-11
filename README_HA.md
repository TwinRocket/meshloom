# Home Assistant Integration

Meshloom can send what your radio sees (its own health, repeater readings, the position of nodes, incoming messages) to Home Assistant, where it shows up as ordinary devices and entities you can put on a dashboard or use in automations. It works through MQTT Discovery, so no custom component or HACS install is needed, and it runs from any Meshloom install.

This page is about publishing the mesh **to** Home Assistant over MQTT. Running Meshloom itself as a Home Assistant add-on is a different thing, described in [`meshloom/DOCS.md`](meshloom/DOCS.md).

## Prerequisites

- Home Assistant with the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) configured
- An MQTT broker (e.g. Mosquitto) accessible to both HA and Meshloom
- Meshloom running and connected to a radio

## Setup

1. In Meshloom, go to **Settings > MQTT & Automation**, add an integration and pick **Home Assistant MQTT Discovery**
2. Enter your MQTT broker host and port (same broker HA is connected to)
3. Optionally enter broker username/password and TLS settings
4. Select contacts for GPS tracking and repeaters for telemetry (see below)
5. Configure which messages should fire events (scope selector at the bottom)
6. Save and enable

Devices will appear in HA under **Settings > Devices & Services > MQTT** within a few seconds.

## How MeshCore IDs Map Into Home Assistant

Meshloom uses each node's public key to derive a stable short identifier for MQTT topics:

- Full public key: `ae92577bae6c4f1d...`
- Node ID: `ae92577bae6c` (the first 12 hex characters, lowercased)
- Example MQTT topic: `meshcore/ae92577bae6c/gps`

When this README shows `<node_id>`, it always means that 12-character value. Node IDs appear in:

- MQTT discovery topics under `homeassistant/...`
- Runtime MQTT state topics under the integration's topic prefix (default `meshcore`)

**Entity IDs** are different — HA auto-generates them from the device name and entity name, not from the node ID. For example, a radio named "MyRadio" produces entities like `binary_sensor.myradio_connected` and `event.myradio_messages`. A contact named "Alice" produces `device_tracker.alice`. You can find your actual entity IDs in **Settings > Devices & Services > MQTT** in HA, and you can rename them in HA's UI without affecting the integration.

You can also see the MQTT topic IDs in Meshloom's Home Assistant integration UI:

- `What gets created in Home Assistant`
- `Published topic summary`

## What Gets Created

### Local Radio Device

Created as soon as Meshloom knows the radio's identity, which is the first health update after the radio connects. Updates every 60 seconds.

| Entity | Type | Description |
|--------|------|-------------|
| `binary_sensor.<radio_name>_connected` | Connectivity | Radio online/offline |
| `sensor.<radio_name>_noise_floor` | Signal strength | Radio noise floor (dBm) |
| `sensor.<radio_name>_battery` | Voltage | Radio battery (V) |
| `sensor.<radio_name>_uptime` | Duration | Uptime (s) |
| `sensor.<radio_name>_last_rssi` / `_last_snr` | Signal | Last received RSSI (dBm) / SNR (dB) |
| `sensor.<radio_name>_tx_airtime` / `_rx_airtime` | Duration | Transmit / receive airtime (s) |
| `sensor.<radio_name>_packets_received` / `_packets_sent` | count | Packet counters |

### Repeater Devices

One device per tracked repeater selected in the HA integration. Updates when telemetry is collected: by the automatic cycle (every 8 hours by default, adjustable from 1 to 24 hours in **Settings > Radio-App Management**), or when you fetch it by hand from the repeater dashboard.

Repeaters must first be added to automatic telemetry tracking (from the repeater's telemetry history pane; the list is under **Settings > Radio-App Management**, at most 8 repeaters). Only tracked repeaters appear in the HA integration's repeater picker.

| Entity | Type | Unit | Description |
|--------|------|------|-------------|
| `sensor.<repeater_name>_battery_voltage` | Voltage | V | Battery level |
| `sensor.<repeater_name>_noise_floor` | Signal strength | dBm | Local noise floor |
| `sensor.<repeater_name>_last_rssi` | Signal strength | dBm | Last received signal strength |
| `sensor.<repeater_name>_last_snr` | -- | dB | Last signal-to-noise ratio |
| `sensor.<repeater_name>_packets_received` | -- | count | Total packets received |
| `sensor.<repeater_name>_packets_sent` | -- | count | Total packets sent |
| `sensor.<repeater_name>_recv_errors` | -- | count | Receive errors |
| `sensor.<repeater_name>_uptime` | Duration | s | Uptime since last reboot |

If the repeater also reports CayenneLPP readings (temperature, humidity, voltage...), Meshloom adds one more sensor per reading, named like the contact ones below (`sensor.<repeater_name>_lpp_<type>_ch<n>`). A GPS reading from a repeater is not published.

If Meshloom already has a cached telemetry snapshot for that repeater, it republishes it on startup so HA can populate the sensors immediately instead of waiting for the next collection cycle. Repeater and LPP sensors are marked unavailable in HA if no new value arrives within 10 hours.

### Contact Devices

One HA device per tracked contact, which can expose two kinds of entities.

**GPS tracker** -- one `device_tracker`, populated from two sources:

- **Advertisements** -- updates passively whenever Meshloom hears an advert carrying GPS coordinates from that contact. No radio commands are sent; it piggybacks on normal mesh traffic.
- **CayenneLPP telemetry** -- if the contact also reports a GPS reading in its LPP telemetry (and is tracked for contact telemetry collection), that reading updates the tracker too. GPS is routed to the tracker, not exposed as a numeric sensor.

**CayenneLPP sensors** -- if the contact is tracked for telemetry collection and reports LPP readings, a numeric sensor is created per reading, auto-detected from the data (e.g. `sensor.<contact_name>_lpp_temperature_ch1`, `_lpp_voltage_ch1`).

| Entity | Description |
|--------|-------------|
| `device_tracker.<contact_name>` | GPS position (`latitude`/`longitude` attributes, plus `altitude` when a telemetry reading includes it) |
| `sensor.<contact_name>_lpp_<type>_ch<n>` | CayenneLPP sensor reading (auto-detected; GPS excluded -- see tracker above) |

### Message Event Entity

A single radio-scoped event entity, `event.<radio_name>_messages`, fires for each message matching your configured scope. Each event carries these attributes:

| Attribute | Example | Description |
|-----------|---------|-------------|
| `event_type` | `message_received` | Always `message_received` |
| `sender_name` | `Alice` | Display name of the sender |
| `sender_key` | `aabbccdd...` | Sender's public key |
| `text` | `hello` | Message body |
| `message_type` | `PRIV` or `CHAN` | Direct message or channel |
| `channel_name` | `#general` | Channel name (null for DMs) |
| `conversation_key` | `aabbccdd...` | Contact key (DM) or channel key |
| `outgoing` | `false` | Whether you sent this message |

## Entity Naming

HA auto-generates entity IDs by slugifying the device name and entity name. For a radio named "My Radio", entities look like `binary_sensor.my_radio_connected` and `event.my_radio_messages`. For a repeater named "Hilltop", `sensor.hilltop_battery_voltage`. For a contact named "Alice", `device_tracker.alice`. You can rename entities in HA's UI without affecting the integration.

MQTT topic paths use the 12-character node ID (first 12 hex characters of the public key). For example:

- Local radio health: `meshcore/<radio_node_id>/health`
- Repeater telemetry: `meshcore/<repeater_node_id>/telemetry`
- Contact GPS: `meshcore/<contact_node_id>/gps`
- Message events: `meshcore/<radio_node_id>/events/message`

## What Appears When

- Created once the radio is known: the local radio device and its entities
- Created when selected in the HA integration: tracked repeater devices and tracked contact device trackers
- Populated only after data exists: contact GPS trackers need an advert with GPS or a GPS reading in collected LPP telemetry; repeater sensors need telemetry, although cached repeater telemetry is replayed on startup when available
- Message event entity: always created once the HA integration is enabled for a connected radio

## Common Automations

### Low repeater battery alert

Notify when a tracked repeater's battery drops below a threshold.

**GUI:** Settings > Automations > Create > Numeric state trigger on `sensor.<repeater_name>_battery_voltage`, below `3.8`, action: notification.

**YAML:**
```yaml
automation:
  - alias: "Repeater battery low"
    trigger:
      - platform: numeric_state
        entity_id: sensor.hilltop_battery_voltage
        below: 3.8
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Repeater Battery Low"
          message: >-
            {{ state_attr('sensor.hilltop_battery_voltage', 'friendly_name') }}
            is at {{ states('sensor.hilltop_battery_voltage') }}V
```

### Radio offline alert

Notify if the radio has been disconnected for more than 5 minutes.

**GUI:** Settings > Automations > Create > State trigger on `binary_sensor.<radio_name>_connected`, to `off`, for `00:05:00`, action: notification.

**YAML:**
```yaml
automation:
  - alias: "Radio offline"
    trigger:
      - platform: state
        entity_id: binary_sensor.myradio_connected
        to: "off"
        for: "00:05:00"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "MeshCore Radio Offline"
          message: "Radio has been disconnected for 5 minutes"
```

### Alert on any message from a specific room

Trigger when a message arrives in a specific channel. Two approaches:

#### Option A: Scope filtering (fully GUI, no template)

If you only care about one room, configure the HA integration's message scope to "Only listed channels" and select that room. Then every event fire is from that room.

**GUI:** Settings > Automations > Create > State trigger on `event.<radio_name>_messages`, action: notification.

**YAML:**
```yaml
automation:
  - alias: "Emergency channel alert"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Message in #emergency"
          message: >-
            {{ trigger.to_state.attributes.sender_name }}:
            {{ trigger.to_state.attributes.text }}
```

#### Option B: Template condition (multiple rooms, one integration)

Keep scope as "All messages" and filter in the automation. The trigger is GUI, but the condition uses a one-line template.

**GUI:** Settings > Automations > Create > State trigger on `event.<radio_name>_messages` > Add condition > Template > enter the template below.

**YAML:**
```yaml
automation:
  - alias: "Emergency channel alert"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    condition:
      - condition: template
        value_template: >-
          {{ trigger.to_state.attributes.channel_name == '#emergency' }}
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Message in #emergency"
          message: >-
            {{ trigger.to_state.attributes.sender_name }}:
            {{ trigger.to_state.attributes.text }}
```

### Alert on DM from a specific contact

**YAML:**
```yaml
automation:
  - alias: "DM from Alice"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    condition:
      - condition: template
        value_template: >-
          {{ trigger.to_state.attributes.message_type == 'PRIV'
             and trigger.to_state.attributes.sender_name == 'Alice' }}
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "DM from Alice"
          message: "{{ trigger.to_state.attributes.text }}"
```

### Alert on messages containing a keyword

**YAML:**
```yaml
automation:
  - alias: "Keyword alert"
    trigger:
      - platform: state
        entity_id: event.myradio_messages
    condition:
      - condition: template
        value_template: >-
          {{ 'emergency' in trigger.to_state.attributes.text | lower }}
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Emergency keyword detected"
          message: >-
            {{ trigger.to_state.attributes.sender_name }} in
            {{ trigger.to_state.attributes.channel_name or 'DM' }}:
            {{ trigger.to_state.attributes.text }}
```

### Track a contact on the HA map

No automation needed. Once a contact is selected for GPS tracking, their `device_tracker` entity automatically appears on the HA map. Go to **Settings > Dashboards > Map** (or add a Map card to any dashboard) and the tracked contact will show up when they advertise their GPS position.

### Dashboard card showing repeater battery

Add a sensor card to any dashboard:

```yaml
type: sensor
entity: sensor.hilltop_battery_voltage
name: "Hilltop Repeater Battery"
```

Or an entities card for multiple repeaters:

```yaml
type: entities
title: "Repeater Status"
entities:
  - entity: sensor.hilltop_battery_voltage
    name: "Hilltop"
  - entity: sensor.valley_battery_voltage
    name: "Valley"
  - entity: sensor.ridge_battery_voltage
    name: "Ridge"
```

### Full monitoring dashboard with message feed

This example creates a dashboard with repeater vitals, a live message feed, and a network activity graph. Replace the three slug values below to match your setup — find your entity IDs in **Settings > Devices & Services > MQTT**.

```yaml
# ┌─────────────────────────────────────────────────────┐
# │  Replace these three values to match your entities  │
# │                                                     │
# │  radio_slug:    the prefix on your radio sensors    │
# │                 e.g. sensor.MYRADIO_noise_floor     │
# │  repeater_slug: the prefix on your repeater sensors │
# │                 e.g. sensor.HILLTOP_battery_voltage  │
# │  message_event: your message event entity ID        │
# │                 e.g. event.MYRADIO_messages          │
# └─────────────────────────────────────────────────────┘
#
#   radio_slug:    myradio
#   repeater_slug: hilltop
#   message_event: event.myradio_messages
```

**Step 1 — Dashboard YAML** (Settings > Dashboards > Add > edit in YAML):

```yaml
views:
  - title: MeshCore
    icon: mdi:radio-tower
    cards:
      - type: entities
        title: Hilltop — Current                     # ← repeater name
        state_color: true
        entities:
          - entity: sensor.hilltop_battery_voltage    # ← repeater_slug
            name: Battery
          - entity: sensor.hilltop_noise_floor        # ← repeater_slug
            name: Noise Floor
          - entity: sensor.hilltop_last_rssi          # ← repeater_slug
            name: Last RSSI
          - entity: sensor.hilltop_last_snr           # ← repeater_slug
            name: Last SNR
          - entity: sensor.hilltop_uptime             # ← repeater_slug
            name: Uptime
          - entity: sensor.hilltop_packets_received   # ← repeater_slug
            name: Packets Rx
          - entity: sensor.hilltop_packets_sent       # ← repeater_slug
            name: Packets Tx

      - type: statistics-graph
        title: Battery Voltage
        entities:
          - sensor.hilltop_battery_voltage            # ← repeater_slug
        stat_types: [mean, min, max]
        days_to_show: 7
        period: hour

      - type: statistics-graph
        title: Noise Floor
        entities:
          - sensor.hilltop_noise_floor                # ← repeater_slug
        stat_types: [mean, min, max]
        days_to_show: 7
        period: hour

      - type: markdown
        title: Message Feed (Last 10)
        content: |
          {% for i in range(1, 11) %}
          {% set msg = states('input_text.meshcore_msg_' ~ i) %}
          {% if msg and msg not in ['unknown', '', 'unavailable'] %}
          {{ msg }}

          {% endif %}
          {% endfor %}
          {% if states('input_text.meshcore_msg_1') in ['unknown', '', 'unavailable'] %}
          *No messages yet.*
          {% endif %}

      - type: statistics-graph
        title: Overall Packets Received
        entities:
          - sensor.myradio_packets_received           # ← radio_slug
        stat_types: [change]
        days_to_show: 7
        period: hour
```

**Step 2 — Message feed helpers**: create 10 text helpers named `MeshCore Msg 1` through `MeshCore Msg 10` (Settings > Helpers > Add > Text). These act as a rolling buffer for the Markdown card above.

**Step 3 — Message feed automation** (Settings > Automations > Create > edit in YAML):

```yaml
alias: MeshCore Message Feed Buffer
description: Rolling buffer of recent mesh messages for dashboard display
mode: queued
max: 10
triggers:
  - trigger: state
    entity_id: event.myradio_messages               # ← message_event
actions:
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_10
    data:
      value: "{{ states('input_text.meshcore_msg_9') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_9
    data:
      value: "{{ states('input_text.meshcore_msg_8') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_8
    data:
      value: "{{ states('input_text.meshcore_msg_7') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_7
    data:
      value: "{{ states('input_text.meshcore_msg_6') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_6
    data:
      value: "{{ states('input_text.meshcore_msg_5') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_5
    data:
      value: "{{ states('input_text.meshcore_msg_4') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_4
    data:
      value: "{{ states('input_text.meshcore_msg_3') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_3
    data:
      value: "{{ states('input_text.meshcore_msg_2') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_2
    data:
      value: "{{ states('input_text.meshcore_msg_1') }}"
  - action: input_text.set_value
    target:
      entity_id: input_text.meshcore_msg_1
    data:
      value: >-
        {{ as_timestamp(trigger.to_state.last_changed) |
        timestamp_custom('%-I:%M %p') }} |
        **{% if trigger.to_state.attributes.channel_name %}{{
        trigger.to_state.attributes.channel_name }}{% else %}DM{% endif %}** |
        {{ trigger.to_state.attributes.sender_name or 'Unknown' }}:
        {{ (trigger.to_state.attributes.text or '')[:180] }}
```

## Troubleshooting

### Devices don't appear in HA

- Verify the MQTT integration is configured in HA (**Settings > Devices & Services > MQTT**) and shows "Connected"
- Verify Meshloom's HA integration shows "Connected" (green dot)
- Check that both HA and Meshloom are using the same MQTT broker
- Subscribe to discovery topics to verify messages are flowing:
  ```
  mosquitto_sub -h <broker> -t 'homeassistant/#' -v
  ```

### Stale or duplicate devices

If you see unexpected devices (e.g. a generic "MeshCore Radio" alongside your named radio), clear the stale retained messages:
```
mosquitto_pub -h <broker> -t 'homeassistant/binary_sensor/meshcore_unknown/connected/config' -r -n
mosquitto_pub -h <broker> -t 'homeassistant/sensor/meshcore_unknown/noise_floor/config' -r -n
```

### Repeater sensors show "Unknown" or "Unavailable"

Repeater telemetry only updates when collected, and HA marks a sensor unavailable after 10 hours without a new value. Trigger a manual fetch by opening the repeater's dashboard in Meshloom and clicking "Status", or wait for the next auto-collect cycle (8 hours by default).

If Meshloom already has cached telemetry for that repeater, it republishes the last known values on startup. If the sensors are still unknown or unavailable, it usually means no telemetry has ever been collected for that repeater yet.

### Contact device tracker shows "Unknown"

The contact's GPS position updates only when Meshloom hears an advertisement with GPS coordinates from that node, or collects LPP telemetry that contains a GPS reading. Until then the tracker shows as unknown.

### Entity is "Unavailable"

Radio health entities have a 120-second expiry. If Meshloom stops sending health updates (e.g. it's shut down or loses connection to the broker), HA marks the entities as unavailable after 2 minutes. Restart Meshloom or check the broker connection.

## Removing the Integration

Disabling or deleting the HA integration in Meshloom's settings publishes empty retained messages to all discovery topics, which removes the devices and entities from HA automatically.

## Local Test Environment

For local development, Meshloom includes a helper that starts Mosquitto and Home Assistant with MQTT preconfigured:

```bash
./scripts/setup/start_ha_test_env.sh
```

That gives you:

- Home Assistant at `http://localhost:8123`
- Mosquitto at `localhost:1883`
- A pre-created HA MQTT integration using that broker

The Home Assistant login is `dev` / `dev`, and Mosquitto accepts anonymous connections. To watch all MQTT traffic during testing:

```bash
docker exec ha-test-mosquitto mosquitto_sub -h 127.0.0.1 -t '#' -v
```

To stop and clean up:

```bash
./scripts/setup/stop_ha_test_env.sh --clean
```

## MQTT Topics Reference

Runtime/state topics (where data is published; `meshcore` is the default topic prefix):

| Topic | Content | Update frequency |
|-------|---------|-----------------|
| `meshcore/{node_id}/health` | `{"connected": bool, "noise_floor_dbm": int, "battery_volts": float, ...}` | Every 60s |
| `meshcore/{node_id}/telemetry` | `{"battery_volts": float, ...}` | ~8h or manual |
| `meshcore/{node_id}/gps` | `{"latitude": float, "longitude": float, ...}` | On advert |
| `meshcore/{node_id}/events/message` | `{"event_type": "message_received", ...}` | On message |

Discovery topics (entity registration, under `homeassistant/`):

| Pattern | Entity type |
|---------|------------|
| `homeassistant/binary_sensor/meshcore_<node_id>/connected/config` | Radio connectivity |
| `homeassistant/sensor/meshcore_<node_id>/<object_id>/config` | Radio, repeater and LPP sensors (e.g. `noise_floor`, `battery_voltage`) |
| `homeassistant/device_tracker/meshcore_<node_id>/config` | Contact GPS tracker |
| `homeassistant/event/meshcore_<node_id>/messages/config` | Message event entity |

The `{node_id}` is always the first 12 characters of the node's public key, lowercased.
