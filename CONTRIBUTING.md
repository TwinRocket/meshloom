# Contributing

## Guiding Principles

- In all your interactions with developers, maintainers, and users, be kind.
- Prefer small, comprehensible changes over large sweeping ones. Individual commits should be meaningful atomic chunks of work. Pull requests with many, many commits instead of a phased approach may be declined.
- Pull requests must be fully understood and explicitly endorsed by a human before merge. AI assistance is great, and this repo is optimized for it, but we keep quality by keeping our agents on track to write clear code, useful (not useless) tests, good architecture, and big-picture thinking.
- No pull request should introduce new failing lint, typecheck, test, or build results.
- Every pull request from a contributor should have an associated issue or discussion thread; a brand new feature appearing first in a PR is an antipattern. The repository owners may open a PR directly.
- Security vulnerabilities are the exception: never open a public issue or PR for one. Report it privately as described in [SECURITY.md](SECURITY.md).
- No truly automated radio traffic. Bot replies are already the practical edge of what this project wants to automate; any kind of traffic that would be intervalized or automated is not what this project is about.
- No ingestion from the internet onto the mesh. This project is a radio client, not a bridge for outside traffic to enter the network. The mesh is strong because it is a radio mesh, not the internet with some weird wireless links.

## Local Development

You need [uv](https://docs.astral.sh/uv/) with Python 3.11 or later (CI tests 3.11, 3.12 and 3.14) and Node.js 24 (`.nvmrc`; CI uses 24 too).

### Backend

```bash
uv sync
uv run uvicorn app.main:app --reload
```

Radio transport is configured in the web UI after startup (`app_settings.radio_transport`). An empty serial port means auto-detect. Do not set `MESHCORE_SERIAL_PORT` / `MESHCORE_TCP_HOST` / `MESHCORE_BLE_ADDRESS` to choose a transport: an existing database with no stored transport imports them once (upgrade path); a new database ignores them (`app/services/radio_transport.py`).

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Run both the backend and `npm run dev` for hot-reloading frontend development.

## Quality Checks

Run the full quality suite before proposing or handing off code changes:

```bash
./scripts/quality/all_quality.sh
```

That runs linting, formatting, type checking, tests, and builds for both backend and frontend.

If you need targeted commands while iterating:

```bash
# backend
uv run ruff check app/ tests/ --fix
uv run ruff format app/ tests/
uv run pyright app/
PYTHONPATH=. uv run pytest tests/ -v

# frontend
cd frontend
npm run lint:fix
npm run format
npm run test:run
npm run build
```

## Quality + Publishing Scripts

<details>
<summary>scripts/quality/</summary>

| Script | Purpose |
|--------|---------|
| `all_quality.sh` | Repo-standard gate: autofix (ruff, eslint, prettier), then pyright, pytest, vitest, and frontend build. Run before finishing any code change. |
| `extended_quality.sh` | `all_quality.sh`, then `e2e.sh` (needs a radio), then `docker_ci.sh`. Manual; no workflow calls it. |
| `e2e.sh` | Thin wrapper that runs Playwright e2e tests from `tests/e2e/`. |
| `docker_ci.sh` | Builds the frontend (`npm ci && npm run build`) in `node:*-slim` containers across a Node/npm version matrix. It does not build the Meshloom image. |

</details>

<details>
<summary>scripts/build/</summary>

| Script | Purpose |
|--------|---------|
| `publish.sh` | Cuts a release in one command (`scripts/build/publish.sh X.Y.Z`), run by an admin on `main` equal to `origin/main`. The only local change allowed is an uncommitted `CHANGELOG.md` edit (the `[X.Y.Z]` section written by hand), which is committed with the release. Refuses a tag that already exists locally or on origin. Runs the quality gate, regenerates `LICENSES.md`, bumps the version (`pyproject.toml`, `uv.lock`, `frontend/package.json`, `meshloom/config.yaml`, `meshloom/Dockerfile`), checks it with `check_version_consistency.sh`, commits exactly those files, pushes `main`, then pushes the annotated (unsigned) `X.Y.Z` tag. Everything else (frontend zip, packages, GitHub release, signing, image) is done by CI on that tag. |
| `tag_release.sh` | Recovery only, when `publish.sh` pushed `main` but not the tag: on a clean `main` equal to `origin/main`, checks the version sources, the CHANGELOG section, the `all-quality` check on HEAD and that the tag is new, then pushes the annotated `X.Y.Z` tag after confirmation (`--dry-run` checks only). |
| `release_common.sh` | Shared shell helpers (version validation, formatting) sourced by other build scripts. |
| `check_version_consistency.sh` | Fails unless the four version sources (`pyproject.toml`, `frontend/package.json`, `meshloom/config.yaml`, the `FROM` tag of `meshloom/Dockerfile`) equal the tag (Release `preflight`, `publish.sh`, `tag_release.sh`). `uv.lock` is updated by `publish.sh` but not checked. |
| `check_signing_keys.sh` | Checks that `pkg/keys/` and the CI signing secret describe the same key. |
| `package_release_artifact.sh` | Builds the prebuilt-frontend release zip (called by `release.yml`). |
| `create_github_release.sh` | Creates the GitHub release with changelog notes (called by `release.yml`). Never creates a tag: fails unless `X.Y.Z` exists on origin and points at the built commit. |
| `build_nfpm_packages.sh` | Builds Meshloom `.deb` and `.rpm` packages with nFPM. |
| `check_rpm_signed.py` | Fails unless each `.rpm` carries a signature header. |
| `neutralize_project_version.py` | Pins the project's own version in the dependency manifests so the Docker dependency layer stays cached across releases. |
| `build_rpi_image.sh` / `build_rpi_kiosk_image.sh` | Raspberry Pi Lite image (built by `release.yml`) / Desktop kiosk image (manual). |
| `collect_licenses.sh` | Gathers third-party license attributions into `LICENSES.md`. |
| `print_frontend_licenses.cjs` | Helper that extracts frontend npm dependency licenses. |
| `dump_api_specs.py` | Writes `openapi.json` and `ws_events.json` generated from the code (imports the app; no server needed). |

</details>

## E2E Testing

E2E tests exercise the full stack (backend + frontend + real radio hardware) via Playwright.

> [!WARNING]
> E2E tests are **not part of the normal development path** — most contributors will never need to run them. They exist to catch integration issues that unit tests can't and generally only need to be run by maintainers.

### Hardware requirements

- A MeshCore radio connected via serial (the harness seeds `radio_transport=serial`; empty `radio_serial_port` auto-detects)
- The radio must be powered on and past its startup sequence before tests begin

### Running

```bash
cd tests/e2e
npm install
npx playwright install chromium  # first time only
npx playwright test              # headless
npx playwright test --headed     # watch it run
```

The test harness starts its own uvicorn instance on port 8001 with a fresh temporary database (it builds `frontend/dist` first when that folder is missing). Your development server (port 8000) is unaffected.

### Test tiers

**Most specs are fully self-contained.** They seed their own data via API calls or direct DB writes and need only a connected radio. These cover messaging, pagination, search, favorites, settings, fanout integrations, historical decryption, and all UI-only views.

**Mesh-traffic tests (tagged `@mesh-traffic`)** wait up to 3 minutes for an incoming message from another node on the network. If no traffic arrives, they fail with an advisory that the failure may be RF conditions, not a bug. These are `incoming-message` and the second test of `packet-feed`.

**The partner-radio DM ACK test (tagged `@partner-radio`)** validates direct-route learning by sending a DM and waiting for an ACK. It requires a second radio in range that has your test radio in its contacts. Configure the partner node's public key and name via `E2E_PARTNER_RADIO_PUBKEY` and `E2E_PARTNER_RADIO_NAME`.

### Making mesh-traffic tests reliable: the echo bot

The most practical way to guarantee incoming traffic is to run an **echo bot on a second radio** monitoring a known channel. When the test suite starts a `@mesh-traffic` test, it sends a trigger message to that channel. If a bot on another radio is listening, it replies — generating the incoming RF packet the test needs within seconds instead of waiting for organic mesh traffic.

The test suite sends `!echo please give incoming message` to the echo channel (default `#flightless`) at the start of each `@mesh-traffic` test. The trigger message is configurable via `E2E_ECHO_TRIGGER_MESSAGE`.

Setup:
1. Set up a second MeshCore radio within RF range of your test radio
2. Run a Meshloom instance on the second radio
3. Configure a bot on the second radio that monitors the echo channel and replies when it sees the trigger. Example bot code:
   ```python
   def bot(sender_name, sender_key, message_text, is_dm,
           channel_key, channel_name, sender_timestamp, path):
       if "!echo" in message_text.lower():
           return f"[ECHO] {message_text}"
       return None
   ```
4. The test suite calls `nudgeEchoBot()` automatically — no manual intervention needed

Without the echo bot, `@mesh-traffic` tests rely on organic traffic from other nodes. In a quiet RF environment they will time out.

### Environment variables

All E2E environment configuration is centralized in `tests/e2e/helpers/env.ts` with defaults that work for the maintainer's test rig. Override via environment variables:

| Variable | Default | Purpose |
|----------|---------|---------|
| `MESHCORE_SERIAL_PORT` | *(optional leftover)* | Copied into `radio_serial_port` by the e2e seed before the server starts; not read by the server as transport config |
| `E2E_ECHO_CHANNEL` | `#flightless` | Channel the echo bot monitors for traffic generation |
| `E2E_ECHO_TRIGGER_MESSAGE` | `!echo please give incoming message` | Message sent to nudge the echo bot |
| `E2E_PARTNER_RADIO_PUBKEY` | *(maintainer's test node)* | 64-char hex public key of a node that will ACK DMs from your radio |
| `E2E_PARTNER_RADIO_NAME` | *(maintainer's test node)* | Display name of that node (used in UI assertions) |

Example for a contributor with their own two-radio setup:

```bash
E2E_ECHO_CHANNEL="#mytest" \
E2E_PARTNER_RADIO_PUBKEY="abcd1234...full64charhexkey..." \
E2E_PARTNER_RADIO_NAME="MyTestNode" \
npx playwright test
```

## Pull Request Expectations

- Keep scope tight.
- Explain why the change is needed.
- Link the issue or discussion where the behavior was agreed on (contributors; not required of the owners).
- Call out any follow-up work left intentionally undone.
- Do not treat code review as the place where the app's direction is first introduced or debated

## Notes For Agent-Assisted Work

Agents start from [`AGENTS.md`](AGENTS.md) at the repository root: it is the canonical guide (map, commands, delivery flow, working rules) and points to the area-specific files. Agent output is welcome, but human review is mandatory.
