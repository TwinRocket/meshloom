# Meshloom

Meshloom is a web client for MeshCore radio meshes. A FastAPI backend drives one
MeshCore radio over serial, TCP or BLE (`meshcore` Python library, pinned in
`pyproject.toml`). It stores everything in SQLite and serves a React frontend, a
REST API under `/api` and a WebSocket at `/api/ws`. It can also talk to Meshloom
Community, a separate service in the `TwinRocket/meshloom-community` repository
that provides the directory, stats, hashtags, the Live feed and the MQTT broker.

This file is the canonical entry point for every coding agent and tool.
`CLAUDE.md` only imports it. The subdirectory files cover their own scope only:

- `app/AGENTS.md`: the backend (modules, radio lifecycle, database and migrations, the Community client, updates, the API route table, backend tests).
- `frontend/AGENTS.md`: the frontend (state, hooks, WebSocket client, components, i18n, frontend tests).
- `app/fanout/AGENTS_fanout.md`: the fanout bus (MQTT, Community MQTT, bots, webhooks, Apprise, SQS, map upload). Read it only for fanout work.
- `frontend/src/components/visualizer/AGENTS_packet_visualizer.md`: the packet visualizer. Read it only for visualizer work.

## Working rules

- **Docs and contracts are not authoritative.** Every `AGENTS.md`, README, user
  doc and contract was written by an LLM and may be wrong. Check every claim against
  the code before you rely on it. If a doc is wrong, fix it in the same PR.
- **Contracts shared with meshloom-community.** The source of truth lives in that
  repository (`docs/contracts/`). Meshloom holds copies, such as
  `tests/fixtures/community_golden_vectors.json`, a byte-for-byte copy of
  `docs/contracts/vectors/golden.json` pinned by sha256 in
  `tests/test_community_golden_vectors.py`. Change a contract, or its copy here,
  in a dedicated commit and call it out in the PR description. Refresh copies,
  never edit them to make a test pass.
- **Out of scope until explicitly launched.** Do not start these, and do not "fix"
  them on the side:
  - Access: accounts, login, CORS and credentials, Origin/Host checks.
  - Plugins: a marketplace that will replace the bots' `exec`.
  - Radio proxy authentication. The proxy must keep binding to `0.0.0.0`.
- Prefer fewer, stronger modules over thin wrappers. Use typed contracts (Pydantic
  models, TS types) at API, WebSocket and repository boundaries. Keep refactors
  behaviour-preserving, with tests around the moved seam.

## Repository map

```
app/                  FastAPI backend (see app/AGENTS.md)
  main.py             app, lifespan, middleware (CORS, Basic auth, security headers), router mounting
  config.py           Settings (env prefix MESHCORE_)
  routers/            one module per API area, all mounted under /api
  services/           orchestration: radio lifecycle/runtime, message send, Community client, updates
  repository/         SQLite access (aiosqlite)
  migrations/         numbered migrations _NNN_*.py
  radio_proxy/        virtual companion TCP listener (default 0.0.0.0:5001)
  fanout/             fanout bus (see app/fanout/AGENTS_fanout.md)
  push/               Web Push (VAPID), separate from fanout
frontend/             React + Vite + TypeScript (see frontend/AGENTS.md)
tests/                pytest suite; tests/e2e/ is Playwright (needs real radios)
meshloom/             Home Assistant add-on (config.yaml, run.py, Dockerfile FROM the GHCR image)
pkg/keys/             public release key, fingerprint, key procedure (README.md)
pkg/nfpm/             .deb/.rpm packaging, systemd units, root update helper apply-update
pkg/rpi/              Raspberry Pi image and kiosk files
scripts/quality/      all_quality.sh (gate), extended_quality.sh, e2e.sh, docker_ci.sh
scripts/build/        publish.sh, nFPM, Pi images, signing and version checks
scripts/setup/        install.sh (public one-liner), compose update helper, embed sync
docs/user/            user docs (en, fr), built by the meshloom.app site from this folder
.github/workflows/    CI, release, packages, repo publication
```

## Architecture

- **Radio in.** `meshcore` events reach `app/event_handlers.py`. Raw RF
  (`RX_LOG_DATA`) goes through `app/packet_processor.py`: parse, decrypt with
  stored channel keys or the in-memory private key (`app/keystore.py`), dedupe,
  store. `CONTACT_MSG_RECV` and similar events are a fallback or supplement.
- **Fan-out.** `broadcast_event()` (`app/websocket.py`) sends each event to the
  WebSocket clients. It also forwards to the radio proxy, the fanout modules
  (`message`, `raw_packet`, `contact`) and Web Push (`message`). Fanout and push
  are skipped when `realtime=False`, as in historical decrypt.
- **Radio out.** The routers call the services. The services take the radio lock
  and call `meshcore` commands. Outgoing messages are stored with `outgoing=1`.
- **Background work.** Fire-and-forget work goes through
  `app.background_tasks.spawn()`, never a bare `asyncio.create_task`. `spawn()`
  keeps a strong reference, logs exceptions and is drained at shutdown.
- **Storage.** SQLite, by default at `data/meshcore.db`. Runtime settings,
  including the radio transport, live in `app_settings` and are edited in the UI,
  not through environment variables. Fanout configs live in `fanout_configs`.
- **Frontend.** In production FastAPI serves `frontend/dist`. When it is missing,
  it falls back to `frontend/prebuilt`. With neither, it serves the API only and
  logs an error.
- **Community.** `app/services/meshloom_community.py` is the HTTP client. It has a
  circuit breaker, and a timeout or 5xx becomes a 503 for the browser.
  `app/services/community_live.py` is the Live relay. Community MQTT is a fanout
  module. Details are in `app/AGENTS.md`.

## Commands

```bash
uv sync                                   # backend deps (Python >= 3.11; CI: 3.11, 3.12, 3.14)
uv run uvicorn app.main:app --reload      # backend on :8000
cd frontend && npm ci && npm run dev      # Vite on :5173, proxies /api to :8000 (Node 24 in CI)

./scripts/quality/all_quality.sh          # THE gate: must be green before any PR
```

`all_quality.sh` autofixes first, so it may leave edits behind: `ruff check
--fix`, `ruff format`, `eslint --fix`, `prettier --write`. Then it runs `pyright
app/`, `pytest tests/`, `vitest run`, `tsc` and `vite build`. The CI workflow
`.github/workflows/all-quality.yml` runs the same checks in check mode on every
push and PR: `ruff format --check`, `npm run lint`, `npm run format:check`.
Its aggregate job is named `all-quality`.

Targeted runs:

```bash
PYTHONPATH=. uv run pytest tests/test_x.py -q
uv run pyright app/
cd frontend && npm run test:run && npm run lint && npm run build
uv run python scripts/setup/sync_installer_embeds.py --check   # install.sh embeds in sync
scripts/build/check_signing_keys.sh                              # pkg/keys consistent
scripts/build/check_version_consistency.sh X.Y.Z                 # release version sources
```

The e2e tests (`tests/e2e`, Playwright, port 8001) need a real serial radio. They
are not part of the gate. See `CONTRIBUTING.md`.

## Conventions and pitfalls (verified)

- **Packet identities.** The `raw_packets.id` row is deduplicated by payload hash,
  with path bytes excluded. The WebSocket-only `observation_id` is unique per RF
  arrival. The frontend feed renders and dedupes on `observation_id`. The packet
  hash rule is shared with Community through golden vectors (`app/path_utils.py`,
  `calculate_packet_hash`).
- **Message dedup** is done by unique indexes, not time windows.
  `idx_messages_dedup_null_safe` is on `(type, conversation_key, text,
  COALESCE(sender_timestamp,0))` for `CHAN`. `idx_messages_incoming_priv_dedup`
  is on incoming `PRIV`, plus `sender_key` (migration 056). A duplicate
  channel insert adds a path. For an outgoing message it also bumps the echo
  count. Same rendered sender + text + second on a channel is
  indistinguishable from an echo; this is accepted, not a bug.
- **Path hash modes**: `0`/`1`/`2` mean 1-, 2- and 3-byte hop IDs. `path_len` in
  the API is always a hop count. DM route precedence is override, then learned
  direct route, then flood. The last DM retry always goes out as flood.
  Advertisement paths (`contact_advert_paths`, 10 per contact) are never used
  as send routes.
- **Channel keys** are 16 bytes, stored as 32 uppercase hex characters. The key
  of a hashtag channel is `SHA256("#name")[:16]`.
- **Hashtag rule (decided, #71/#73).** Key derivation always hashes the exact
  name (`hashtag_key_from_name`, `app/data/meshcore_channels.py`): no trim, no
  lowercasing, only one leading `#` is dropped then re-added. The official
  MeshCore app normalisation (trim, lowercase, add `#`, `^#[a-z0-9-]+$`,
  30 bytes) applies **only to user input in the UI**
  (`frontend/src/utils/hashtagInput.ts`). Names from the Community catalogue,
  the cracker, imports or the API are never normalised. The opt-in "extended
  names" mode hashes the typed name (32-byte radio limit). Golden vectors in
  `tests/test_community_golden_vectors.py` must all pass.
- **Public keys** are 64 hex characters. Prefix lookups use `LIKE 'prefix%'` and
  must be unambiguous.
- **Radio transport** is set in the UI (`app_settings.radio_transport`). Legacy
  `MESHCORE_SERIAL_PORT` / `MESHCORE_TCP_HOST` / `MESHCORE_BLE_ADDRESS` (with
  `_BAUDRATE`, `_TCP_PORT`, `_BLE_PIN`) are imported once into an existing
  database with no transport set, then never read again. A new database ignores
  them (`app/services/radio_transport.py`).
- **`MESHLOOM_COMMUNITY_IATA`, `_BROKER_HOST`, `_API_BASE` override the database
  on every read** (`get_community_effective`, `app/services/meshloom_community.py`).
  They are not only seeds. A value set in the UI is ignored while the env var is
  set. `MESHLOOM_COMMUNITY` only seeds a new database.
- **Current security posture.** This is a description, not a recommendation;
  changing it belongs to the Access and Plugins work.
  - CORS is `allow_origins=["*"]` with `allow_credentials=True` (`app/main.py`).
  - Access control is optional app-wide HTTP Basic auth only
    (`MESHCORE_BASIC_AUTH_USERNAME` and `_PASSWORD`, set together).
  - Bots run user Python through `exec()` with full builtins
    (`app/fanout/bot_exec.py`). `MESHCORE_DISABLE_BOTS=true` turns them off;
    the `.deb`/`.rpm` sets it by default in `/etc/meshloom/meshloom.env`.
  - The radio proxy listens on `0.0.0.0:5001` by default, without
    authentication.
- **The `meshcore` reader can raise `IndexError`** on a truncated advert in
  `LOG_DATA`. This is a library parsing gap. One packet's task fails and later
  packets still process.

## Known code bugs (open, not yet fixed)

Do not document these as features. Fix them only in a lot launched for that.

- **Home Assistant add-on radio proxy port.** `meshloom/run.py` exports
  `MESHCORE_RADIO_PROXY_PORT` (5051), but nothing in `app/` reads it. The proxy
  listens on the database port (5001 by default), and `MESHCORE_MANAGED_PORTS=true`
  makes `PATCH /api/radio/proxy` refuse any port change.
- **Community Live.** The `gate` state and close code 4002 are dead code:
  `_gate_blocked` is never set to true (`app/services/community_live.py`,
  `app/models.py`). The frontend `CommunityLiveStatus.reconnecting` boolean is
  never sent by the backend, which sends `state` instead. The frontend rebuilds
  its own state and reads `state` only for `auth_rejected`
  (`frontend/src/stores/livePacketStore.ts`).
- `MESHCORE_PUBLIC_URL` is not used by production code.
  `/api/community/iata/{code}/hashtags` ignores `code`.
- The docstring of `app/fanout/community_mqtt.py` is stale: it says
  "LetsMesh only" and cites `app/mqtt.py`, which does not exist.

## Environment variables

Read by `app/config.py` (`MESHCORE_` prefix) and a few services. Everything else is
in `app_settings`, edited in the UI.

| Variable | Default | Effect |
|---|---|---|
| `MESHCORE_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | SQLite file; update job files live next to it |
| `MESHCORE_DISABLE_BOTS` | `false` | disables bot execution and bot config (403) |
| `MESHCORE_BASIC_AUTH_USERNAME` / `_PASSWORD` | empty | app-wide Basic auth; both or neither (startup error otherwise) |
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | `false` | radio audit every 10 s instead of hourly |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | `false` | `set_channel` before every channel send |
| `MESHCORE_LOAD_WITH_AUTOEVICT` | `false` | contact loading with `AUTO_ADD_OVERWRITE_OLDEST` |
| `MESHCORE_SKIP_POST_CONNECT_SYNC` | `false` | debug: skip post-connect sync/offload and periodic loops |
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | `false` | enables `GET /api/radio/private-key` |
| `MESHCORE_VAPID_SUBJECT` | `mailto:noreply@meshcore.local` | fallback when `app_settings.vapid_subject` is empty; Apple rejects `.local` |
| `MESHCORE_EMBEDDABLE_SAME_ORIGIN` | `false` | `frame-ancestors 'self'` instead of `'none'` (HA ingress) |
| `MESHCORE_MANAGED_PORTS` | `false` | the host owns the proxy port; `PATCH /api/radio/proxy` answers 409 to a port change |
| `MESHCORE_PUBLIC_URL` | empty | **no effect today**: only `_resolve_request_base` (`app/frontend_static.py`) reads it, and only tests call that |
| `MESHLOOM_COMMUNITY` | on | seeds Community on a new DB; `0`/`false`/`off`/`no` seeds it off |
| `MESHLOOM_COMMUNITY_IATA` / `_BROKER_HOST` / `_API_BASE` | empty | **override the DB on every read** (defaults `mqtt.meshloom.app`, `https://api.meshloom.app`) |
| `MESHLOOM_COMMUNITY_LOCKED` | unset | `1` prevents enabling Community from the UI |
| `MESHLOOM_INSTALL_KIND` | detected | `package`/`compose`/`addon`/`container`/`source` |

## Delivery flow

1. Open or reuse a GitHub issue first (`CONTRIBUTING.md`). A feature must not
   appear first in a PR.
2. Create a branch off up-to-date `origin/main`, one branch per lot (`fix/…`,
   `docs/…`, `feat/…`).
3. Make small, atomic commits. Each commit should pass the tests on its own:
   history is kept, and bisect relies on it.
4. Run `./scripts/quality/all_quality.sh` and get it green locally.
5. Open a PR that links the issue (`Closes #N`) and states what was left out
   on purpose.
6. Wait for green CI. `main` has no branch protection, so this is a discipline.
7. Merge with a **merge commit** (not squash, not rebase), only after a human
   approves.

## Signed release chain

The key is `pkg/keys/`: primary fingerprint
`D852F2F0892ABF379F52D110FB3EB7BBC43935C8`, RSA-4096, certify only, kept offline.
CI only holds the signing subkey, in the secret `MESHLOOM_REPO_GPG_PRIVATE_KEY`.
Clients and installers trust the public key committed in `pkg/keys` (or embedded
in `install.sh`), never a key fetched at install time. The procedure and the
rotation steps are in `pkg/keys/README.md`.

1. `scripts/build/publish.sh` runs the gate, bumps the four version sources
   (`pyproject.toml`, `frontend/package.json`, `meshloom/config.yaml`, the `FROM`
   tag in `meshloom/Dockerfile`), updates `uv.lock` and `CHANGELOG.md`, then
   runs `git add .`, commits, **pushes the current branch** and pushes the
   `X.Y.Z` tag. Run it from a clean, up-to-date `main` checkout. It builds and
   publishes nothing itself. The header of `release.yml` still says to tag by
   hand afterwards; ignore that, `publish.sh` already does it.
2. A tag `X.Y.Z` triggers `release.yml`. That workflow runs the quality gate,
   then `preflight`: `check_version_consistency.sh` and a check that the secret
   matches `pkg/keys`. Then it builds the frontend zip and the nFPM `.deb`/`.rpm`
   for amd64 and arm64, signed by nFPM, and publishes the GitHub release with
   `install.sh`. Then it calls:
   - `publish-linux-repo.yml`: rebuilds a **signed apt + dnf repository** and
     deploys it to GitHub Pages (`https://twinrocket.github.io/meshloom`), with
     `InRelease`/`Release.gpg`, `repomd.xml.asc`, `meshloom.gpg` and
     `meshloom.asc`. It fails closed on a missing key, a missing package or an
     unsigned rpm.
   - `sign-manifest.yml`: attaches `SHA256SUMS(.asc)` (debs, rpms, zip,
     `install.sh`) and `OCI-DIGESTS(.asc)`
     (`ghcr.io/twinrocket/meshloom:X.Y.Z sha256:<index digest>`). It fails
     closed.
   - the Raspberry Pi image job, and a dispatch of `nfpm-armhf.yml`. The armhf
     job reruns the repo publication and the manifest once its package is
     attached.
3. `docker.yml` builds the multi-arch GHCR image: `:edge` on `main`, `:latest`
   and `:X.Y.Z` on tags.
4. `install.sh` embeds the public key, its fingerprint, the compose helper and
   the apt pin. After changing any of them, run
   `scripts/setup/sync_installer_embeds.py`; the tests run it with `--check`.

## Updater security principle

**The root side never reads anything the app writes.** The app runs as the
unprivileged user `meshloom`. Only these paths exist between the app and root:

- **Trigger, app to root.** The app writes `/var/lib/meshloom/request-update`.
  `meshloom-update.path` (`PathChanged=`) starts `meshloom-update.service`. As a
  fallback, polkit lets `meshloom` start only that unit. The helper never reads,
  deletes or chowns the request file.
- **Target.** `pkg/nfpm/apply-update` upgrades only the `meshloom` package, from
  the signed repository (`twinrocket.github.io/meshloom`, `signed-by=` /
  `gpgcheck=1`, apt pin). It fails closed if that source is not
  signature-checked, and has a 120 s cooldown.
- **Status, root to app.** The helper writes
  `/var/lib/meshloom-update/status.json` (root-owned `StateDirectory`, 0644).
  The app only reads it. The app keeps its own memory in `update-attempt.json`,
  in its data directory.
- **Docker Compose installs** use `scripts/setup/helpers/compose-update` on the
  host, installed by `install.sh`. It has the same trigger model. The target is
  the latest GitHub release, pinned by digest from the signed `OCI-DIGESTS`
  (checked with `gpgv` against the embedded keyring). It refuses downgrades. Its
  status is mounted read-only into the container. The pre-4.18 compose helper
  did read `update-job.json` from app data: the app deletes that file before
  triggering such a helper (`start_compose_helper`).
- **No apply support.** The Home Assistant add-on, plain containers and source
  installs only report the available version.

App-side details (`install_kind`, `oss_updates`, `update_apply`) are in
`app/AGENTS.md`, "Updates".
