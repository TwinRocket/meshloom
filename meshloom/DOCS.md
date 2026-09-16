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
| `proxy_port` | The port the radio proxy listens on inside the container. See below. |
| `public_url` | The address this instance is reached at from outside, when a tunnel gives it one. |
| `basic_auth_username` / `basic_auth_password` | Guards the web interface when it is published beyond ingress. |
| `vapid_subject` | Contact address for Web Push. |
| `disable_bots` | Turns off the bot system. |

## The radio proxy port

The proxy lets another MeshCore client — a phone app, or a second Meshloom — send
and receive through this radio without unplugging it.

Ingress does not carry it: ingress serves the web interface, and the proxy is a
plain TCP listener. So it is published on the host, and the published port is
changed in this add-on's **Network** panel, not inside Meshloom.
The field in Meshloom's own settings is read-only for that reason and says so: a
value set there would leave the proxy listening on a port nothing is forwarded to,
which looks exactly like a working proxy that nobody can reach.

## Reaching it from outside

Ingress authenticates with Home Assistant and needs nothing published, but the
address it serves under belongs to Home Assistant and is not durable — so **Web
Push notifications will not work through ingress alone**.

Giving Meshloom a hostname of its own takes two steps, because ingress cannot
provide one:

1. In this add-on's **Network** panel, give `8000/tcp` a host port. It is blank by
   default, and while it is blank there is no address for anything to point at.
2. Route that hostname to `http://<home-assistant-ip>:<the port you chose>`. The
   **Cloudflared** add-on does this without opening a port on your router.
3. Set `public_url` to the hostname you chose, so the links Meshloom builds —
   Web Push among them — name the address you actually reach it at rather than one
   inferred from whatever header survived the trip.

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
