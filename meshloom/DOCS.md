# Meshloom

Meshloom is a web client for a MeshCore radio. Plug the radio into the machine
running Home Assistant and the mesh becomes something you can read and answer
from the sidebar.

- **Conversations** with contacts, channels and room servers, with history kept
  across restarts and unread state that survives closing the tab.
- **A map** of every node your radio has heard, with the path a message took and
  where each hop sat when it repeated.
- **Packet tools** — a live feed, a 3-D view of the mesh, route tracing and node
  lookup — for when the question is why something did not arrive.
- **MQTT discovery**, so nodes, signal and battery arrive in Home Assistant as
  entities you can put on a dashboard or drive an automation with.

## What it runs on

amd64, aarch64 and armv7 — so a Raspberry Pi 3 is covered as well as a Pi 4, an
Intel box or a NAS. The add-on adds one small layer to the published Meshloom
image rather than rebuilding it, so installing takes about as long on a Pi as
anywhere else.

## Getting started

1. Plug the radio into the machine running Home Assistant, or leave it on the
   network if you reach it over TCP.
2. Install and start the add-on, then open it from the sidebar. The first install
   builds one small layer on top of the published Meshloom image, so it takes a
   little longer than starting it again later does.
3. Go to **Settings → Radio** and choose the transport — the serial device, or the
   host and port of a radio reachable over the network.

## Options

| Option | What it does |
|---|---|
| `log_level` | How much the add-on writes to its log. |
| `proxy_port` | Intended container-side port of the radio proxy. **Currently not applied** — see below. |
| `public_url` | The address this instance is reached at from outside. Accepted, but no feature uses it yet. |
| `basic_auth_username` / `basic_auth_password` | Guards the web interface when it is published beyond ingress. |
| `vapid_subject` | Contact address for Web Push. |
| `disable_bots` | Turns off the bot system. |

## The radio proxy port

The proxy lets another MeshCore client — a phone app, or a second Meshloom — send
and receive through this radio without unplugging it.

Ingress does not carry it: ingress serves the web interface, and the proxy is a
plain TCP listener. So it is published on the host, and the published port is
changed in this add-on's **Network** panel, not inside Meshloom. The port field
in Meshloom's own settings is read-only here.

The add-on tells Meshloom the port at startup (`MESHCORE_RADIO_PROXY_PORT`, from
the `proxy_port` option, `5051` by default), and Meshloom listens on it whatever
the stored setting says. The proxy itself is off until you enable it in
**Settings > Proxy**. Leave `proxy_port` at `5051`: it has to equal the container
port the add-on forwards, which is fixed by the manifest. Only the host side of
the mapping is yours to change, in the Network panel.

## Reaching it from outside

Ingress authenticates with Home Assistant and needs nothing published, but the
address it serves under belongs to Home Assistant and is not durable — so **Web
Push notifications will not work through ingress alone**.

Giving Meshloom a hostname of its own takes these steps, because ingress cannot
provide one:

1. In this add-on's **Network** panel, give `8000/tcp` a host port. It is blank by
   default, and while it is blank there is no address for anything to point at.
2. Route that hostname to `http://<home-assistant-ip>:<the port you chose>`. The
   **Cloudflared** add-on does this without opening a port on your router.
3. Open Meshloom through that hostname (over HTTPS) to subscribe to Web Push.
   Setting `public_url` is harmless but has no effect yet.

The hostname does not have to resemble Home Assistant's: the tunnel connects to an
address and a port, and Meshloom answers to whatever name it is asked for. Ingress
keeps working meanwhile; the two ways in are independent. A published port is
reachable by anything that can route to that machine, so set
`basic_auth_username` / `basic_auth_password` unless the tunnel authenticates for
you.

## The radio identity

Meshloom binds to the radio's public key the first time it connects. Moving the
radio to another machine, or pointing this instance at a different radio, is
treated as a different node and Meshloom will say so rather than mixing two
histories together.
