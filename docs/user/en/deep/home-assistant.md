---
title: Home Assistant
description: MQTT Discovery, devices, automations, and the topics that break.
level: deep
order: 17
---

Meshloom can publish what it knows about your mesh to Home Assistant through MQTT Discovery. Devices and entities (the sensors and switches Home Assistant shows) appear by themselves. You do not need a custom component or HACS.

This is different from the Home Assistant add-on, which runs Meshloom itself inside Home Assistant (see [Installation](/en/docs/install/)). The MQTT link described here works from any installation.

## What you need

- Home Assistant with the [MQTT integration](https://www.home-assistant.io/integrations/mqtt/) set up.
- An MQTT broker that both Home Assistant and Meshloom can reach.
- Meshloom connected to a radio.

## Setting up

1. In Meshloom, open **Settings → MQTT & Automation**, choose **Add Integration**, then **Home Assistant MQTT Discovery**.
2. Enter the address and port of the broker (default port 1883). Add a user name, a password and TLS if your broker needs them. The topic prefix is `meshcore` unless you change it.
3. Under **GPS Tracked Contacts**, pick the contacts you want to see on the Home Assistant map.
4. Under **Telemetry Tracked Repeaters**, pick the repeaters whose sensors you want. Only repeaters that already collect telemetry automatically appear in this list. To add one, open the repeater's dashboard and tick the tracking option at the bottom. The repeaters being tracked are listed in **Settings → Radio-App Management**.
5. Under **Message Events**, choose which messages should trigger an event.
6. Choose **Save as Enabled**.

The devices appear in Home Assistant under **Settings → Devices & services → MQTT**. The form also lists what will be created and the exact topics.

## Names and identifiers

Meshloom identifies a node by the first 12 characters of its public key, in lowercase:

- public key: `ae92577bae6c4f1d...`
- node identifier: `ae92577bae6c`
- topic of its position: `meshcore/ae92577bae6c/gps`

Home Assistant builds the names of its entities from the names of the devices and sensors, not from this identifier.

## What appears in Home Assistant

### Your radio

A device named after your radio. It refreshes every 60 seconds.

| Entity | Description |
|--------|-------------|
| `binary_sensor.<radio>_connected` | Whether the radio is connected |
| `sensor.<radio>_noise_floor` | Background radio noise, in dBm |
| `sensor.<radio>_battery` | Battery, in V |
| `sensor.<radio>_uptime` | Time since the radio started, in s |
| `sensor.<radio>_last_rssi` and `_last_snr` | Strength and quality of the last packet received |
| `sensor.<radio>_tx_airtime` and `_rx_airtime` | Time spent sending and receiving, in s |
| `sensor.<radio>_packets_received` and `_packets_sent` | Packet counts |

### Repeaters

One device per selected repeater. Its sensors update when telemetry is collected: automatically every 8 hours by default (you can change this in **Radio-App Management**), or when you refresh the repeater's dashboard.

| Entity | Unit | Description |
|--------|------|-------------|
| `sensor.<repeater>_battery_voltage` | V | Battery level |
| `sensor.<repeater>_noise_floor` | dBm | Background noise at the repeater |
| `sensor.<repeater>_last_rssi` | dBm | Strength of the last packet received |
| `sensor.<repeater>_last_snr` | dB | Quality of the last packet received |
| `sensor.<repeater>_packets_received` | count | Packets received |
| `sensor.<repeater>_packets_sent` | count | Packets sent |
| `sensor.<repeater>_rx_errors` | count | Receive errors |
| `sensor.<repeater>_uptime` | s | Time since the last restart |

If the repeater has environment sensors (CayenneLPP format), Meshloom adds one sensor per reading, such as temperature or humidity.

### Contacts

| Entity | Description |
|--------|-------------|
| `device_tracker.<contact>` | Position, with `latitude`, `longitude` and sometimes `altitude`. It updates when an advert with GPS coordinates is heard, or when the contact's telemetry contains a position |
| `sensor.<contact>_...` | One sensor per CayenneLPP reading of a contact whose telemetry is tracked |

### Message events

`event.<radio>_messages` fires for each message that matches the scope you chose.

| Attribute | Example | Description |
|-----------|---------|-------------|
| `event_type` | `message_received` | Always `message_received` |
| `sender_name` | `Alice` | Display name |
| `sender_key` | `aabbccdd...` | Public key of the sender |
| `text` | `hello` | Text of the message |
| `message_type` | `PRIV` or `CHAN` | Direct message or channel |
| `channel_name` | `#general` | Name of the channel, or empty for a direct message |
| `conversation_key` | `aabbccdd...` | Key of the contact or the channel |
| `outgoing` | `false` | Whether you sent it |

## Example automations

### Low repeater battery

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

### Radio offline for five minutes

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

### Message in a specific channel

Set the scope to **Only listed channels/contacts** and tick the channel, or filter every event:

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

## When it does not work

If no device appears, check that the MQTT integration of Home Assistant is connected, that Meshloom shows the integration as connected, and that both use the same broker. Watch what Meshloom announces:

```text
mosquitto_sub -h <broker> -t 'homeassistant/#' -v
```

Meshloom only announces its devices once it knows the radio's identity, so the radio must be connected.

Old or duplicate devices can be removed by sending an empty retained message on their discovery topic:

```text
mosquitto_pub -h <broker> -t 'homeassistant/binary_sensor/meshcore_unknown/connected/config' -r -n
mosquitto_pub -h <broker> -t 'homeassistant/sensor/meshcore_unknown/noise_floor/config' -r -n
```

Some entities show `Unknown` or `Unavailable` for a while, which is normal:

- Repeater sensors stay `Unknown` until Meshloom has collected telemetry, and become `Unavailable` if nothing arrives for 10 hours.
- A contact's tracker stays unknown until an advert with a position, or a telemetry report, arrives.
- Radio entities become `Unavailable` after 120 seconds without news, for example when Meshloom stops.

Disabling or deleting the integration removes its entities from Home Assistant.

## Topics

Home Assistant reads these topics. Use them from other tools too.

| Topic | Content | When |
|-------|---------|------|
| `meshcore/{node_id}/health` | `{"connected": true, "noise_floor_dbm": -110, ...}` with the radio sensors above | Every 60 s |
| `meshcore/{node_id}/telemetry` | `{"battery_volts": 4.1, ...}` | When telemetry is collected |
| `meshcore/{node_id}/gps` | `{"latitude": 48.85, "longitude": 2.35, ...}` | When a position is heard |
| `meshcore/{node_id}/events/message` | `{"event_type": "message_received", ...}` | For each message in scope |

The prefix `meshcore` is the one you set in the form. Discovery messages always use the `homeassistant/` prefix:

| Pattern | Entity |
|---------|--------|
| `homeassistant/binary_sensor/meshcore_<node_id>/connected/config` | Radio connection |
| `homeassistant/sensor/meshcore_<node_id>/<name>/config` | Sensors of the radio, of repeaters and of contacts |
| `homeassistant/device_tracker/meshcore_<node_id>/config` | Position of a contact |
| `homeassistant/event/meshcore_<node_id>/messages/config` | Message events |
