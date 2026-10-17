# Radio bench

An opt-in pytest suite (`tests/radio_bench/`, marker `radio`) that drives a
running Meshloom instance and its radio: the REST API, the WebSocket at
`/api/ws` and the radio proxy (through the pinned `meshcore` client). It gives
real-radio coverage to changes that the unit tests fake. The reference target
is `meshloom-next` and its dedicated test radio.

It is not part of the gate: without `MESHLOOM_RADIO_BENCH_URL` every test is
skipped, so `pytest tests/` and `all_quality.sh` report them as skipped.

## Running it

```bash
MESHLOOM_RADIO_BENCH_URL=http://192.168.42.226:8010 \
MESHLOOM_RADIO_BENCH_PROXY=192.168.42.226:5011 \
uv run pytest tests/radio_bench -m radio -n 0 -v
```

| Variable | Required | Meaning |
|---|---|---|
| `MESHLOOM_RADIO_BENCH_URL` | yes | Base URL of the instance (with or without `/api`). Setting it is what turns the bench on. |
| `MESHLOOM_RADIO_BENCH_PROXY` | no | `host:port` of the instance's radio proxy, as reachable from the bench. Without it the proxy tests are skipped. |
| `MESHLOOM_RADIO_BENCH_CHANNEL` | no | Adopted channel used for sends, default `#meshloom-next-test`. |

These are test variables, read only by `tests/radio_bench/bench.py`: never
document them in `docs/user/`.

`-n 0` is required (the `addopts` default is `-n auto`): the tests share one
radio, and the bench fails at once under an xdist worker. A run takes about a
minute (sends are spaced 20 s apart, see below). The last line of the terminal
summary says whether the settings were identical before and after the run.

There is no CI workflow for the bench: GitHub-hosted runners cannot reach a
LAN-only instance, self-hosted runners are not allowed on this public
repository, and exposing an instance to the internet is not an option (no API
authentication, unauthenticated radio proxy). Run it locally, from the LAN.

## What it touches

- **Sends** (`test_channel_roundtrip.py`): a channel message on the bench
  channel through `POST /messages/channel`, and one through the proxy
  (`SEND_CHANNEL_TXT_MSG` on the slot the proxy shows for that channel). With
  one radio, the receive side is the mesh repeating that flood message back:
  each repeat is a real RF arrival. The tests check the row, its timestamps
  against the local clock (both sides on NTP, 120 s of skew allowed), the
  `message` and `message_acked` WebSocket events, that the proxy client hears
  the repeat as RX log data (matched by `calculate_packet_hash`), and that each
  text is stored exactly once. The messages stay in the instance's history;
  each carries `bench <run id>` so runs can be told apart.
- **Radio settings** (`test_settings_roundtrip.py`): flips `multi_acks_enabled`
  and changes `telemetry_mode_env` with `PATCH /radio/config`, reads them back,
  then restores them. Nothing else is written to the radio.
- **Reconnection** (`test_reconnect.py`): `POST /radio/disconnect` then
  `POST /radio/reconnect` (twice per run: once alone, once with a proxy client
  connected, whose TCP session must still answer afterwards); WebSocket and proxy
  clients that disconnect and come back. A reconnect runs the instance's normal
  post-connect setup. `POST /radio/disconnect` pauses reconnection in memory
  only: if the instance is killed hard (SIGKILL) between the disconnect and the
  `radio_link` finalizer, the radio stays paused until someone calls
  `POST /radio/reconnect` or restarts the instance.

It never factory-resets the radio and never reads, exports or changes its
identity: the API client refuses, before anything is sent, the private key,
factory reset, identity, backup and reboot routes, and every write to the
transport, the proxy settings, the custom variables and the Wi-Fi routes
(`/api/radio/wifi/*`: credentials, enabled, reboot; a bad call would cut the
radio's network link). `PATCH /radio/config` is accepted only with keys from
`MUTABLE_RADIO_FIELDS`, the only ones the guard restores
(`_refuse_forbidden` in `bench.py`). The public key is dropped from every
config read. The proxy itself answers `EXPORT_PRIVATE_KEY` with "disabled".

## The settings guard

The session fixture `settings_guard` (`conftest.py`) runs around the whole run:

1. waits until the radio is connected, then records a snapshot: the radio config
   (`GET /radio/config` without the public key), the app settings
   (`GET /settings` minus `last_message_times` and `last_advert_time`), the
   proxy settings, the transport settings and the channel list;
2. after the last test, whatever happened, reconnects the radio if a test left
   it down, PATCHes back any of `MUTABLE_RADIO_FIELDS` that differ, takes the
   snapshot again and fails the run, naming the paths that changed (never their
   values), if anything differs.

With its GPS on, the radio moves its own `lat`/`lon` by a few metres between two
reads: differences up to `GPS_JITTER_DEG` (0.001°) are not a change.

## Flood collisions

A flood message sent while repeaters still relay the previous one often
collides with it and is never repeated. `AirTime` spaces the bench's own sends
by `SEND_SPACING` (20 s), and a send nobody repeated within `ECHO_TIMEOUT`
(45 s) is tried again with a new text, up to `ECHO_ATTEMPTS` (3); each attempt
must still be stored exactly once. If all three go unrepeated, check the
repeaters before suspecting the code.

## Adding a test

- Put it in `tests/radio_bench/`, with `pytestmark = pytest.mark.radio`.
- Use the fixtures: `api` (sync `httpx.Client`), `aapi` (async), `bench_config`,
  `channel_key`, `run_id`, `airtime` (call `await airtime.wait_turn()` before
  any transmission), `proxy_endpoint` (skips without a proxy) and `radio_link`
  (for a test that drops the link: it is brought back even on failure).
- A test that changes a radio setting adds the field to `MUTABLE_RADIO_FIELDS`
  and restores it in a fixture finalizer, like `original_radio_config`; the
  guard is the second line, not the first.
- A test that changes anything else in the snapshot restores it itself: the
  guard only reports it. Anything the snapshot does not cover yet goes into
  `settings_snapshot()` first.
- Never add a route that touches the identity, the transport or the firmware
  to the client's allow list; the offline tests of `bench.py`
  (`tests/test_radio_bench_helpers.py`) pin the refused routes.
- Keep a test idempotent: run the suite three times in a row before a PR.
