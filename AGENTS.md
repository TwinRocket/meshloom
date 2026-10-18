# Meshloom

A FastAPI backend drives one MeshCore radio (serial, TCP or BLE, through the
pinned `meshcore` library), stores everything in SQLite and serves a React
frontend, a REST API under `/api` and a WebSocket at `/api/ws`. It can talk to
Meshloom Community (`TwinRocket/meshloom-community`).

This file is the entry point for every coding agent. Area files, read only when
you work there:

- `app/AGENTS.md`: backend (radio lifecycle, sending, migrations, Community client, updates, telemetry, push).
- `app/net/AGENTS.md`: the egress guard for user-chosen destinations (SSRF, IP pinning).
- `app/fanout/AGENTS.md`: the fanout bus (MQTT, bots, webhooks, Apprise, SQS, map upload).
- `frontend/AGENTS.md`: frontend (state, WebSocket client, i18n, layout).
- `frontend/src/components/visualizer/AGENTS.md`: the 3D packet visualizer.

Developer reference: `docs/dev/` (database schema, backup sections, Community client, frontend
Community state, releasing, BLE pairing, radio bench). User docs: `docs/user/` (en, fr). Environment variables for users:
`docs/user/en/deep/environment.md`.

## Working rules

- **Docs and contracts are not authoritative.** Every `AGENTS.md`, README, user
  doc and contract was written by an LLM and may be wrong. Check every claim against
  the code before you rely on it. If a doc is wrong, fix it in the same PR.
- **Keep the docs in sync with every change** (code, config, CI, scripts, infra):
  check the `AGENTS.md` files, READMEs, `CONTRIBUTING.md`, `docs/dev/`, `docs/user/`
  (both languages; the website publishes it), the shared contracts and header
  comments, fix them in the same PR, and list the docs checked or updated in the PR body.
- **Contracts shared with meshloom-community.** The source of truth is that
  repository's `docs/contracts/`. Meshloom holds copies, such as
  `tests/fixtures/community_golden_vectors.json`, a byte-for-byte copy of
  `docs/contracts/vectors/golden.json` pinned by sha256 in
  `tests/test_community_golden_vectors.py`. Change a contract, or its copy here,
  in a dedicated commit prefixed `CONTRACTS:` and call it out in the PR description.
  Refresh copies, never edit them to make a test pass.
- **Out of scope until explicitly launched.** Do not start these, and do not "fix"
  them on the side:
  - Access: accounts, login, credentials, Origin/Host checks. (CORS is done:
    same-origin plus `auth_allowed_origins`, `app/cors.py`.)
  - Plugins: a marketplace that will replace the bots' `exec`.
  - Radio proxy authentication. The proxy must keep binding to `0.0.0.0`.

  The current posture (CORS same-origin plus a user list, optional Basic auth only, bots
  running `exec()`, an unauthenticated proxy) is described in `SECURITY.md`,
  "Known limits". It is a fact, not a recommendation.
- **No environment variable for anything a user tunes.** Settings live in
  `app_settings` and the UI. Retired variables are imported once into an existing
  database, then only warned about (`app/services/legacy_env_settings.py`,
  `app/services/radio_transport.py`). Developer-only variables (table in
  `app/AGENTS.md`) never appear in user docs, READMEs, `.env.example` or example composes.
- Prefer fewer, stronger modules over thin wrappers. Use typed contracts (Pydantic
  models, TS types) at API, WebSocket and repository boundaries. Keep refactors
  behaviour-preserving, with tests around the moved seam.

## Repository map (non-obvious parts)

```
meshloom/             the Home Assistant add-on (not a Python package): config.yaml, run.py,
                      Dockerfile FROM the GHCR image
pkg/keys/             public release key and its procedure (README.md)
pkg/nfpm/             .deb/.rpm packaging, systemd units, root update helper apply-update
scripts/quality/      all_quality.sh (the gate)
scripts/build/        publish.sh (release), tag_release.sh (recovery); see CONTRIBUTING.md
scripts/setup/        install.sh and the embeds it carries (sync_installer_embeds.py)
docs/user/            user docs, published by meshloom.app from the latest X.Y.Z tag, not from main
docker-compose.dev.yaml  runs ghcr.io/twinrocket/meshloom:edge (never builds; see below)
.github/workflows/    docker.yml (:edge from main, :next from preversion-5, release images),
                      forward-merge.yml (main into preversion-5); see docs/dev/releasing.md;
tests/radio_bench/    opt-in live-radio suite (marker `radio`), docs/dev/radio-bench.md
```

## Architecture invariants

- **Radio in.** `meshcore` events reach `app/event_handlers.py`. Raw RF
  (`RX_LOG_DATA`) goes through `app/packet_processor.py`: parse, decrypt, dedupe,
  store. `CONTACT_MSG_RECV` and similar events are a fallback or supplement.
- **Fan-out.** `broadcast_event()` (`app/websocket.py`) always sends to the
  WebSocket clients. With `realtime=True` it also notifies the radio proxy, the
  fanout modules and Web Push; historical decryption passes `realtime=False`.
- **Radio out.** Routers call services; services take the radio lock
  (`radio_operation()`) and call `meshcore`. Outgoing messages are stored with `outgoing=1`.
- **Background work.** Fire-and-forget work goes through
  `app.background_tasks.spawn()`, never a bare `asyncio.create_task`: `spawn()`
  keeps a strong reference, logs exceptions and is drained at shutdown. A
  long-lived loop may use `create_task` only if it keeps the task in an
  attribute and cancels it at shutdown.
- **Community egress.** Opted out means no connection at all to the Community
  hosts. Every egress goes through the guard in
  `app/services/meshloom_community.py` (`community_egress_state()`); never add a
  call path around it. The few bounded exceptions and the "waiting for a
  position" level are in `app/AGENTS.md`. The radio's position leaves only in
  the IATA bind and the on-click IATA suggestion.
- **User-chosen destinations.** The notification webhook, Web Push, their
  test routes and the fanout outputs (webhook, Apprise, SQS, map upload) go
  through the egress guard (`app/net/egress_guard.py`, see `app/net/AGENTS.md`):
  validated before any connection, then sent pinned to a validated IP where
  the client allows it (not Apprise, not boto3). Apprise URLs also pass the
  scheme allowlist of `app/net/apprise_urls.py`. A new outbound path to a
  user-chosen URL must use the guard.

## Commands

```bash
uv sync                                   # backend deps (Python >= 3.11; CI: 3.11, 3.12, 3.14)
uv run uvicorn app.main:app --reload      # backend on :8000
cd frontend && npm ci && npm run dev      # Vite on :5173, proxies /api to :8000 (Node 26)

./scripts/quality/all_quality.sh          # THE gate: must be green before any PR
```

`all_quality.sh` autofixes first (`ruff check --fix`, `ruff format`,
`eslint --fix`, `prettier --write`), so it may leave edits behind: commit them.
Then it runs `pyright app/`, `pytest tests/`, `vitest run`, `tsc` and `vite build`.
CI (`.github/workflows/all-quality.yml`, aggregate job `all-quality`) runs the
same checks in check mode, so an unformatted file passes locally and fails in CI
if you do not commit the autofix. Prettier checks every file under
`frontend/src/`, Markdown included.

Targeted runs:

```bash
PYTHONPATH=. uv run pytest tests/test_x.py -q
cd frontend && npm run test:run && npm run lint && npm run typecheck && npm run build
uv run python scripts/setup/sync_installer_embeds.py --check   # install.sh embeds in sync
scripts/build/check_signing_keys.sh                              # pkg/keys consistent
```

The e2e tests (`tests/e2e`, Playwright) need a real radio and are not part of
the gate (`CONTRIBUTING.md`). Neither is the radio bench (`tests/radio_bench`,
skipped unless `MESHLOOM_RADIO_BENCH_URL` is set): it drives a running instance
and its radio through the API and the radio proxy, restores every setting it
changes and checks they are back (`docs/dev/radio-bench.md`):

```bash
MESHLOOM_RADIO_BENCH_URL=http://host:8010 MESHLOOM_RADIO_BENCH_PROXY=host:5011 \
  uv run pytest tests/radio_bench -m radio -n 0
```

## Conventions and pitfalls

- **Packet identities.** `raw_packets.id` is deduplicated by payload hash, path
  bytes excluded. The WebSocket-only `observation_id` is unique per RF arrival;
  the frontend renders and dedupes on it. The packet hash rule
  (`app/path_utils.py`, `calculate_packet_hash`) is shared with Community through
  the golden vectors, which must all pass.
- **Message dedup** is done by unique indexes, not time windows:
  `idx_messages_dedup_null_safe` on `(type, conversation_key, text,
  COALESCE(sender_timestamp,0))` for `CHAN`, `idx_messages_incoming_priv_dedup`
  on incoming `PRIV` plus `sender_key`. A duplicate channel insert adds a path,
  and for an outgoing message bumps the echo count. Same sender + text + second
  on a channel is indistinguishable from an echo: accepted, not a bug.
- **Hashtag rule (decided, #71/#73).** Key derivation hashes the exact name,
  `SHA256("#name")[:16]` (`hashtag_key_from_name`, `app/data/meshcore_channels.py`):
  no trim, no lowercasing, only one leading `#` dropped then re-added. The official
  MeshCore app normalisation (trim, lowercase, `^#[a-z0-9-]+$`, 30 bytes) applies
  **only to user input in the UI** (`frontend/src/utils/hashtagInput.ts`). Names
  from the Community catalogue, the cracker, imports or the API are never normalised.
- **Keys.** Channel keys are 16 bytes, stored as 32 uppercase hex characters.
  Public keys are 64 hex characters; prefix lookups (`LIKE 'prefix%'`) must be unambiguous.
- **Deployments that never delete files.** A Portainer GitOps stack copies each
  new commit over its old tree and never removes a file deleted upstream. That is
  why `docker-compose.dev.yaml` pulls `:edge` instead of building: once, the old
  `useCommunityLeave.ts` shadowed the new `.tsx` and broke `tsc` while CI was
  green. Anyone may still build from such a tree, so:
  - `npm run build` type-checks only what `src/main.tsx` reaches
    (`frontend/tsconfig.build.json`); `npm run typecheck` checks everything.
  - Never rename or delete a file discovered on disk: `app/migrations/_NNN_*.py`
    (the runner refuses two files with one number) and
    `frontend/src/i18n/locales/slices/*.json` (read by `import.meta.glob`).
  - Never change a module's extension (`.ts` to `.tsx`): resolution prefers `.ts`.

## Delivery flow

1. Outside contributors open or reuse an issue first (`CONTRIBUTING.md`). The
   repository owners may open a PR directly.
2. Branch off up-to-date `origin/main`, one branch per lot (`fix/…`, `docs/…`, `feat/…`).
   **5.0.0 work** branches off `origin/preversion-5` and its PR targets
   `preversion-5`, the integration branch; `main` stays the 4.18.x line
   (hotfixes, CI, docs). At the 5.0.0 cut, `preversion-5` merges into `main` and
   is deleted. Branches and images: `docs/dev/releasing.md`, "Branches".
3. Small, atomic commits that each pass the tests: history is kept, bisect relies on it.
4. Get `./scripts/quality/all_quality.sh` green locally.
5. Open a PR that links the issue (`Closes #N`, when there is one) and states
   what was left out on purpose.
6. `main` is protected by a ruleset: PR required, checks `all-quality`,
   `analyze (python)` and `analyze (javascript-typescript)` green, merge commit
   only, no deletion or force-push. Admins bypass it (that is how `publish.sh`
   pushes the release commit). `preversion-5` gets the same rules. The
   `release-tags` ruleset reserves `X.Y.Z` tags to admins. No workflow pushes
   `main` or creates a tag; the only workflow that writes a branch is
   `forward-merge.yml`: every push to `main` reaches `preversion-5` through a PR
   `main → preversion-5` it opens (or reuses) and auto-merges (merge commit,
   never a rebase or a force push), then it dispatches `docker.yml` and
   `codeql.yml` on `preversion-5`. A conflict leaves that PR open and the run red.
7. Merge with a **merge commit**, only after a human approves (a convention: the
   ruleset requires no review).

## Releases and signing

- A release is `scripts/build/publish.sh X.Y.Z`, run by an admin on `main`
  equal to `origin/main`, after writing the `[X.Y.Z]` section of `CHANGELOG.md`.
  It bumps the version sources, pushes `main`, then the tag; CI builds, signs and
  publishes everything. Never bump versions or push tags by hand
  (`scripts/build/tag_release.sh` is the recovery tool). The whole chain is in
  `docs/dev/releasing.md`; the key procedure is in `pkg/keys/README.md`.
- Clients trust only the public key committed in `pkg/keys` (fingerprint
  `D852F2F0892ABF379F52D110FB3EB7BBC43935C8`) or embedded in `install.sh`, never
  a key fetched at install time. CI holds only the signing subkey.
- `install.sh` embeds the public key, its fingerprint, the compose helper and
  the apt pin. After changing any of them, run
  `scripts/setup/sync_installer_embeds.py`; the tests run it with `--check`.
- A doc change in `docs/user/` reaches meshloom.app only with the next release.
- **Updater: the root side never reads anything the app writes.** The app only
  writes a trigger file; the root helper upgrades only from the signed source and
  publishes a status file the app only reads. Details in `app/AGENTS.md`, "Updates".
