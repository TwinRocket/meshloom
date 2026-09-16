# Meshloom

A MeshCore client: conversations, a map of what your node hears, packet tools, and
MQTT discovery so the mesh appears in Home Assistant.

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
plain TCP listener. So the proxy is the one thing published on the host, and the
published port is changed in this add-on's **Network** panel, not inside Meshloom.
The field in Meshloom's own settings is read-only for that reason and says so: a
value set there would leave the proxy listening on a port nothing is forwarded to,
which looks exactly like a working proxy that nobody can reach.

## Reaching it from outside

Ingress authenticates with Home Assistant and needs nothing published, but it has
no durable external address — so **Web Push notifications will not work through
ingress alone**.

If you want them, give the instance a real hostname. The **Cloudflared** add-on
does this without opening a port on your router; point it at this add-on and set
`public_url` to the hostname you chose.

## The radio identity

Meshloom binds to the radio's public key the first time it connects. Moving the
radio to another machine, or pointing this instance at a different radio, is
treated as a different node and Meshloom will say so rather than mixing two
histories together.
