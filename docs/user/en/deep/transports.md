---
title: Radio transports
description: USB serial, TCP or Bluetooth, chosen in the web interface, plus how Meshloom reconnects.
level: deep
order: 11
---

Meshloom talks to one radio, through one link at a time. That link is called the transport. There are three: serial (a USB cable), TCP (the radio is reachable over the network) and BLE (Bluetooth Low Energy). You choose it in the web interface, not in environment variables. The choice is saved in the database.

Until a transport is saved, the radio stays paused and the status bar says so.

## Choose the transport

1. Open **Settings > Radio**. On a new install, the **Connect a radio** banner or the **Connect** button in the status bar takes you there.
2. In **Transport**, pick **Serial**, **TCP** or **Bluetooth**.
3. Fill in the fields below, then press **Save and connect**.

| Transport | Fields | Notes |
|---|---|---|
| Serial | **Serial port**, **Baud rate** | The port is a drop-down list. **Auto-detect** (the default) works when one radio is plugged in. **Refresh ports** rescans. The baud rate is `115200`; leave it unless your firmware says otherwise. |
| TCP | **TCP host**, **TCP port** | The host is an IP address or a name. The port defaults to `5000`. |
| Bluetooth | **BLE address**, **BLE PIN** | **Scan** lists nearby radios; click one to fill the address. The PIN is required (it is shown on the radio's screen). On Linux, Meshloom pairs the radio itself with this PIN (see [Pairing](#pairing)). Once saved, leave the PIN field empty to keep it. |

Do not set `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST` or `MESHCORE_BLE_ADDRESS` to pick a transport: these variables are no longer read. An existing database that had them may import them once, at the first start after an upgrade.

If a transport is not usable on this machine (no serial support, no Bluetooth adapter), the panel says why next to the field.

## Serial (USB)

This is the usual setup: the radio is plugged into the machine that runs Meshloom.

To put MeshCore firmware on a radio from your browser (companion, repeater or room server), use the [flasher](/en/flasher/).

A serial port can only be used by one program at a time. If another MeshCore app, a serial console or a second Meshloom holds the port, connection attempts keep failing. After three identical "Serial Connection started" lines, Meshloom stops repeating them and writes one `WARNING` that mentions possible port contention.

With Docker, give the container access to the device in `docker-compose.yml`. The transport itself is still chosen in the interface; inside the container the radio is at `/dev/meshcore-radio`:

```yaml
devices:
  - /dev/serial/by-id/your-meshcore-radio:/dev/meshcore-radio
```

If a `by-id` path contains a `:`, Docker Compose cannot read it. Use another path or a symbolic link without the colon on the host side.

## TCP

Use TCP when the radio is reachable over the network, for example a radio with Wi-Fi, or another Meshloom that shares its radio (see below).

Meshloom cannot assume it is the only program talking to a radio on the network. So over TCP it re-writes the channel slot on the radio before every channel message, instead of trusting its own memory of what each slot holds. See [Radio, contacts, and channels](/en/docs/deep/radio/).

## Bluetooth (BLE)

BLE needs the address and the PIN. In the Home Assistant add-on, Bluetooth needs no setting: the add-on uses the Bluetooth adapter of the Home Assistant machine (built-in or USB) through the host's Bluetooth service. Home Assistant's own Bluetooth integration can stay on, as it shares the adapter. Bluetooth proxies (ESPHome, Shelly) do not count: the radio has to be within range of the Home Assistant machine itself. Pairing with the PIN is not confirmed there yet: Meshloom registers no Bluetooth pairing agent of its own and relies on the host to accept the PIN, so if saving fails with `BLE pairing failed`, use USB or TCP instead. Bots can reach the host's system services through the same Bluetooth access, so turn them off (`disable_bots`) if the add-on is reachable from outside Home Assistant. In Docker it usually needs manual changes to the Compose file (access to the host's Bluetooth, sometimes extra privileges). With Docker as root on Linux, the installer maps the host's D-Bus socket when it finds one, but this is not always enough. If the radio holds many contacts, the first read of its contact table can time out. Meshloom still loads your favorites and recent contacts as best it can, and **Let the radio evict its oldest contacts** (**Settings → Radio → Advanced**) is meant for this case.

### Pairing

A MeshCore radio asks for its PIN once, the first time a machine pairs with it over Bluetooth. On Linux, Meshloom does this itself when it connects, with the PIN you saved: the Linux package, the Raspberry Pi image, Docker running as root with the host's D-Bus socket mapped, and the Home Assistant add-on once it can reach the host's Bluetooth service. You no longer need `bluetoothctl pair` or `trust` first. A pairing made earlier with `bluetoothctl` is reused as is. Meshloom never makes itself the system's default pairing agent, so a desktop or Home Assistant keeps handling its own pairing requests.

Under the PIN field, **Pairing** shows where things stand:

- **Paired with this machine**: nothing to do.
- **Not paired yet**, or **This machine has not seen the radio yet**: Meshloom pairs at the next connection, and **Pair now** does it right away. The radio must be on, in range, and not connected to a phone: a companion radio accepts one connection at a time.
- **The last pairing attempt failed**, with the reason. After a wrong PIN, Meshloom makes one attempt only, then pauses automatic pairing for this PIN: correct the PIN and press **Save and connect**, or press **Pair again**. When the radio is absent or does not answer, automatic attempts space out, from 5 seconds up to one minute, and the panel shows when the next one is due.
- **Automatic pairing is unavailable**, with the cause: Bluetooth service unreachable, no adapter, adapter off or blocked by rfkill, or permission refused. Meshloom then connects as before, and the radio must be paired with the system's tools.

Press **Forget pairing** when the radio was reset or re-flashed: it no longer knows this machine, so the connection keeps failing. After three failures in a row on an existing pairing, the panel suggests it. Meshloom then pairs again with the saved PIN. Forget the pairing before switching to another radio too. A leftover pairing does no harm, and `bluetoothctl remove <address>` deletes it later.

On macOS and Windows, or with an address that is not a MAC address (`AA:BB:CC:DD:EE:FF`), pair the radio with the system's Bluetooth tools.

## Share your radio with other apps

**Settings > Proxy** (**Radio Proxy**) lets Meshloom act as a MeshCore companion radio over TCP. A phone app, or another Meshloom set to the TCP transport, can then use your radio through this one. It is off until you tick **Enable TCP companion proxy**.

| Setting | Default |
|---|---|
| Bind address | `0.0.0.0` (all network interfaces) |
| Port | `5001` |
| Max clients | `8` |

The protocol has no authentication, so use it only on a trusted network. A Meshloom cannot use its own proxy as its radio. On the Home Assistant add-on, the port is set by the add-on instead (see [Install](/en/docs/install/)).

## What the installer does

The Linux one-liner does not store any transport. A native install asks nothing about the radio: you set it in the web interface after the first start. With Docker, the installer looks for serial devices only if Docker runs as root on Linux. With one device, it maps it automatically. With several, it asks which one. See [Other install paths](/en/docs/deep/install-paths/).

## Reconnection

Once connected, Meshloom checks the link every five seconds and reconnects by itself when it drops.

- **Reconnect** (in the status bar, or in **Settings > Radio**) forces an attempt right away.
- **Disconnect** closes the link and pauses automatic attempts, for example so another app can use the radio. Press **Reconnect** to resume.

Each time the link comes back, Meshloom redoes its start-up work: it listens for radio events again, reads the private key into memory, sets the radio clock, and synchronizes contacts and channels. If the radio refuses the time because its clock is ahead, Meshloom reboots it once per Meshloom start, stops the start-up work and reconnects once the radio is back (see [Troubleshooting](/en/docs/deep/troubleshooting/#the-radio-clock-is-stuck-in-the-future)). This has a time limit of 5 minutes. If it is exceeded, Meshloom tries once more, then reports that the radio start-up looks stuck and asks you to reboot the radio and restart the server. If the radio is only temporarily unreachable, the monitor keeps retrying every five seconds.
