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

1. Plug the radio into the machine running Home Assistant, leave it on the
   network if you reach it over TCP, or keep it within Bluetooth range of that
   machine.
2. Install and start the add-on, then open it from the sidebar. The first install
   builds one small layer on top of the published Meshloom image, so it takes a
   little longer than starting it again later does. Home Assistant signs you in
   there. Port 8000 is not published by default: to reach Meshloom, or create its
   first account, without the sidebar, give `8000/tcp` a host port in the
   add-on's **Network** panel first (see *Reaching it from outside*).
3. Go to **Settings → Radio** and choose the transport — the serial device, the
   host and port of a radio reachable over the network, or a Bluetooth radio
   (**Scan**, then its PIN).

## Bluetooth

The add-on reaches Bluetooth through the host's BlueZ service over D-Bus, so it
uses the adapter of the machine running Home Assistant, built-in or USB. Nothing
needs turning on, and Home Assistant's own Bluetooth integration can stay enabled:
it shares the adapter. Bluetooth proxies (ESPHome, Shelly) are not used: the radio
has to be within range of the Home Assistant machine itself.

**Scan** should list nearby radios. Meshloom then pairs the radio with its PIN by
itself, through a pairing agent of its own used for that request only. It never
becomes the host's default agent, so Home Assistant keeps handling its own
pairing requests. **Settings → Radio** shows the pairing state under the PIN
field, with **Pair now** (or **Pair again**) and **Forget pairing**. This is not
confirmed on Home Assistant hardware yet: if pairing keeps failing, connect the
radio over USB or TCP instead.

If the Bluetooth choice in **Settings → Radio** is marked unavailable with "No
Bluetooth adapter", the add-on found no adapter on the host: check that Home
Assistant lists one in **Settings → System → Hardware**. The same words after a
**Scan** only mean the scan failed; the add-on log has the details.

**Security.** The host's D-Bus system bus is shared with the add-on, which runs
as root. Code running in the add-on can therefore ask the host's system services
to act as root on the Home Assistant machine, not only inside the add-on. Bots
are such code (see `disable_bots` below): if Meshloom is reachable from outside
Home Assistant, or if anyone you do not fully trust can sign in, turn bots off.
Plugins run apart, each copy under its own unprivileged user that cannot reach the bus;
`disable_plugins` locks them off entirely if you want no third-party code at all.

## Signing in

Opened from the sidebar, Meshloom signs you in with your Home Assistant user:
no Meshloom password and no account creation page, also after an update from
4.18. The first Home Assistant user to open it becomes its administrator, even
if a Meshloom administrator already exists (one made from `basic_auth_*` or with
`meshloom-admin`): the sidebar is the add-on's way in and must always reach an
administrator. The next ones get the **Member** role, which can read and send
but not change settings. Only Home Assistant administrators see the Meshloom
panel (the add-on keeps it admin-only), so these are always people who
already administer your Home Assistant. Meshloom keeps one account per Home Assistant user (its display name
follows Home Assistant's), and sets no cookie of its own under ingress: Home
Assistant's address is shared by every add-on.

Meshloom trusts these identities only when the request comes from Home
Assistant's Supervisor (`172.30.32.2`). The same headers sent to a published
port 8000 are ignored there, and that address asks for a Meshloom account and
password. Like every add-on under ingress, Meshloom trusts Home Assistant and
its other add-ons: do not run an add-on you would not trust with Home Assistant
itself.

## Options

| Option | What it does |
|---|---|
| `log_level` | How much the add-on writes to its log: `DEBUG`, `INFO` (default), `WARNING` or `ERROR`. The private key and Wi-Fi passwords are redacted even at `DEBUG`. Channel names and channel keys may appear at any level; repeater and room login passwords and the Bluetooth PIN may also appear at `DEBUG`. |
| `proxy_port` | Container-side port of the radio proxy, `5051` by default. Applied at every start, and it has to match the port the add-on forwards: leave it alone. See below. |
| `basic_auth_username` / `basic_auth_password` | **Deprecated, removed in the next release.** Home Assistant now signs you in through the sidebar (see *Signing in* below). If both are set, Meshloom turns them into an administrator account once, at its first start while it has no account, for the published port; it never reads them again. Empty them afterwards. |
| `vapid_subject` | **Deprecated, removed in the next release.** The contact address for Web Push now lives in Meshloom's **Settings → Notifications**, VAPID subject field. If you set it here, Meshloom copies it there once, at its first start, when that field is empty, and never reads this option again: change the address in Settings from now on. |
| `disable_bots` | Turns off the bot system. Bots run Python code you write, with full access to the add-on, and through the host's D-Bus (needed for Bluetooth) with root access to the Home Assistant machine. Turn them off if the instance is reachable from outside Home Assistant or shared with people you do not fully trust. |
| `disable_plugins` | Locks the plugin system off: no plugin can be installed, changed or started, whatever Meshloom's settings say; you can still view and delete them, and pause them all, and plugins restored from a backup come back turned off. Each plugin copy runs under its own unprivileged user, without the add-on's root rights nor its D-Bus access, but if you want no third-party code in the add-on at all, turn this on. `false` by default. |

Everything else (radio, notifications, integrations) is set in Meshloom's own **Settings**.

### Notifications

Meshloom notifies you through **channels** that you add in its own **Settings → Notifications**, nothing in this panel: Web Push (browsers), a **Home Assistant** channel for the Companion app on your phone, ntfy, Telegram, Discord, Gotify, e-mail, a webhook, or any Apprise service. The Home Assistant channel needs a long-lived access token from your Home Assistant profile and the address of Home Assistant on your network, with **Allow a private-network destination** ticked (a destination on your network is refused until you do). Channels and rules are in the full backup, but their secrets (tokens, passwords, addresses that carry a token) never are: after a restore, a channel comes back switched off until you type them again. Web Push needs HTTPS on a name of your own and does not work through ingress alone (see below); the other channels do not. The full guide is Meshloom's *Notifications* page (`docs/user/en/deep/notifications.md` in the repository).


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
**Settings → Proxy**. It has no authentication: anyone who can reach the
published port can use the radio. Leave `proxy_port` at `5051`: it has to equal the container
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

Tunnels, VPNs and reverse proxies are covered step by step in
*Remote access on your own* (published on meshloom.app, in the Go deeper section).

The hostname does not have to resemble Home Assistant's: the tunnel connects to an
address and a port. Meshloom looks at the name in the browser's address (DNS
rebinding): in 5.0.0 an unknown name is only logged, and from 5.1 it is refused
unless declared in **Settings → Users & Access → Public addresses**, so declare
yours (and list the tunnel or proxy in **Trusted proxies**, so Meshloom sees
HTTPS and the real visitor). Ingress is never refused for its name and keeps
working meanwhile; the two ways in are independent. A published port is
reachable by anything that can route to that machine: it asks for a Meshloom
account and password (the one made from `basic_auth_*`, or one created with
`meshloom-admin`, see *Recovering access*).

## Meshloom Community

A new install starts with Meshloom Community off; the add-on has no option for
it. Joining is your choice: in **Settings → Meshloom Community**, enter the
3-letter IATA code of the nearest airport and click **Join Meshloom Community**.
Community then receives the raw packets your radio hears, and in return names
hops, draws RF locate zones and feeds the Live page. To leave, click **Leave
Meshloom Community** in the same place and confirm: every connection to
Community stops at once, without a restart, and the update check then asks
GitHub only, every 6 hours. The one exception: if Community surely did not get
the opt-out and the broker could not be reached at that moment, Meshloom
reconnects, only to delete your node's leftover status, a few tries within
24 hours at most, then never again.
Details: <https://meshloom.app/en/docs/deep/community/>.

## Where the data lives

The database, keys and settings are kept in the add-on's own configuration
folder (`addon_config`), which survives updates and is included in Home Assistant
backups. Updating Meshloom is done from Home Assistant's add-on page: the
in-app updater only tells you a new version exists.

## Recovering access

Home Assistant signs you in, so a lost Meshloom password does not lock you out of
the add-on (it matters only for the published port, where the lost password of
an account is reset with `meshloom-admin reset-password <user>`). The image still carries `meshloom-admin` for the rare case where you
need to list, disable or reset accounts. With the *Advanced SSH & Web Terminal*
add-on (Protection mode off):

```
docker exec -it $(docker ps --format '{{.Names}}' | grep meshloom) meshloom-admin list-users
```

It finds the add-on's database (`/config/meshcore.db`) by itself. Details:
<https://meshloom.app/en/docs/deep/access/>.

## The radio identity

Meshloom binds to the radio's public key the first time it connects. Moving the
radio to another machine, or pointing this instance at a different radio, is
treated as a different node and Meshloom will say so rather than mixing two
histories together.
