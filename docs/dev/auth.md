# Local accounts

Meshloom 5.0.0 makes login mandatory (LAN included) with four roles: `admin`,
`member`, `viewer` and `kiosk`. This page covers the data layer (tables,
password hashing, opaque tokens, the repository) and the auth core: the gate
that resolves a principal for every request, the CSRF rule, the first-run
setup with its installation code, the `/api/auth/*` routes and the permission
registry (which role may call which route, and receive which WebSocket event).
The login and setup pages are in `frontend/src/auth/` (`frontend/AGENTS.md`,
"Session gate"). Home Assistant ingress identities are in "Ingress" below.
Device tokens, trusted networks and 2FA come with later Access tickets. Like every doc here
this is a hypothesis: the code wins. The recovery CLI (`meshloom-admin`, `app/cli.py`)
works on the same tables.

## Where things are

| Piece | File |
|---|---|
| Tables (existing databases) | `app/migrations/_101_auth_data_model.py` |
| Tables (fresh databases) | `SCHEMA_TABLES` / `SCHEMA_INDEXES` in `app/database.py`, textually identical |
| Typed models | `app/auth/models.py` |
| Password hashing (argon2id) | `app/auth/passwords.py` |
| Opaque secrets (sessions, setup code) | `app/auth/tokens.py` |
| Repository | `app/repository/auth.py` |
| Principal (who is calling) | `app/auth/principal.py` |
| The gate (ASGI middleware, HTTP + WebSocket) | `app/auth/middleware.py` |
| Permissions (roles, `require()`, default deny, WebSocket event classes) | `app/auth/permissions.py` |
| Sessions and the cookie | `app/auth/sessions.py` |
| Audit log (events, retention purge) | `app/auth/audit.py`, `tests/test_auth_audit.py` |
| Username + password provider | `app/auth/providers/password.py` |
| Login delays (per pair, per username, per IP) | `app/auth/ratelimit.py`, `tests/test_auth_ratelimit.py` |
| Home Assistant ingress provider | `app/auth/providers/ingress.py` |
| `Authorization: Basic` compatibility, Basic import | `app/security.py` |
| Installation code, first run | `app/auth/setup_code.py` |
| CSRF rule, https detection, `X-Forwarded-Host` for same-origin | `app/origin_policy.py` |
| Trusted proxies (the one interpreter of `X-Forwarded-For`/`-Proto`) | `app/middleware/proxy_headers.py`, `tests/test_proxy_headers.py` |
| Routes | `app/routers/auth.py`, `GET /api/health` in `app/routers/health.py` |
| Recovery CLI (`meshloom-admin`) | `app/cli.py`, `pkg/nfpm/meshloom-admin`, `tests/test_cli.py` |
| Tests | `tests/test_migrations/test_migration_101.py`, `tests/test_auth_passwords.py`, `tests/test_auth_repository.py`, `tests/test_auth_middleware.py`, `tests/test_auth_setup.py`, `tests/test_auth_sessions.py`, `tests/test_auth_session_hygiene.py`, `tests/test_auth_websocket.py`, `tests/test_auth_password_policy.py`, `tests/test_auth_basic.py`, `tests/test_auth_route_coverage.py`, `tests/test_auth_permissions.py`, `tests/test_auth_ingress.py`, `tests/test_auth_audit.py`, `tests/test_auth_ratelimit.py`, `tests/test_cli.py` |

Column lists: `docs/dev/database.md`, sections `users`, `sessions`, `auth_audit`
and `auth_settings`.

## Rules

- **Usernames** are unique case-insensitively. The repository stores the display
  form (NFC, trimmed, 1 to 64 characters, no control characters) in `username`
  and the key `NFKC(casefold(NFKC(name)))` in `username_key`, which has the `UNIQUE`
  constraint. SQLite's `NOCASE` is not used: it folds ASCII only, so `Élise` and
  `éLISE` would both be accepted. A clash raises `UsernameTakenError`.
- **User ids are never reused** (`AUTOINCREMENT`), so an audit row or a trusted
  network rule never ends up pointing at a later account.
- **Roles and providers** are CHECK-constrained in SQL and `Literal` types in
  Python. `password_hash` is NULL for identities that never log in with a
  password (`ingress`, the kiosk `system` account).
- **Passwords** are hashed with argon2id through PyNaCl (`nacl.pwhash.argon2id`,
  libsodium) at the INTERACTIVE level, `OPSLIMIT_INTERACTIVE` /
  `MEMLIMIT_INTERACTIVE` (2 passes, 64 MiB), set in one constant,
  `passwords.PARAMS` (lead decision on review: the targets include 512 MB
  Raspberry Pi boards, and the login path's semaphore of 2 caps the peak at
  about 128 MiB; MODERATE would be 256 MiB per call, 512 MiB at peak, reachable
  without authentication). The stored value is libsodium's encoded string,
  which carries its salt and parameters; `needs_rehash()` tells a caller to
  store a fresh hash after a successful login when `PARAMS` changes, so the cost
  can be raised later without a migration. Passwords are NFC
  normalized and capped at 1024 characters. `argon2-cffi` was not taken: a second
  CFFI build for the same algorithm, while PyNaCl already ships wheels for
  aarch64 and armv7.
- **Off the event loop.** Measured verify times on an Apple M4 Max (macOS,
  single call, 2026-10-18): INTERACTIVE 0.35 s on an idle machine (review
  measurement) and 0.53 to 0.86 s under heavy load (load average about 40);
  MODERATE 2.1 to 2.2 s. A Raspberry Pi 4 is estimated at several times the idle
  figure, roughly 1.5 to 3 s for INTERACTIVE, more on a Pi Zero 2 W (not
  measured). Each call allocates 64 MiB. From async code, call
  `hash_password_async()` / `verify_password_async()`, which run in a worker
  thread. Bounding how many run at once belongs to the caller: the login
  delays (`app/auth/ratelimit.py`) first, then the semaphore of the password
  provider ("Login delays" below).
- **Sessions and codes are opaque secrets** (`tokens.new_token()`, 256 bits from
  `secrets`). Only `hash_token()` (SHA-256 hex) is stored. The repository looks a
  session up by that hash and checks the stored value with `hmac.compare_digest`.
  No signed cookie, no JWT: revocation needs a table anyway.
- **Session lifecycle.** `create()` keeps at most 50 active sessions per user and
  revokes the oldest beyond that (`create_capped()` also returns their ids);
  `touch()` writes `last_seen_at` at most once a
  minute and can slide `expires_at` in the same write; `revoke()`,
  `revoke_all_for_user()` (returns the revoked ids), `filter_live()` (which of
  some session ids are still usable: live row, enabled user) and `purge()`
  (deletes expired and revoked rows).
  `touch()` never writes a revoked or expired session, so it can never revive
  one, and a new session never evicts itself (a clock that went backwards).
  User agents are capped at 512 characters. The
  lifetime comes from `auth_settings.session_lifetime_days` (30 by default).
  Revoking sessions on a role change or a disable is the caller's decision;
  server code revokes through `app/auth/sessions.py` (below), never the
  repository directly, so the sockets close too.
- **Audit.** `AuthAuditRepository.add()` appends a row; its `details` are JSON.
  Each key, at any depth, goes through NFKC and case folding, then is refused
  with `ValueError` when (1) it has a character outside `[a-z0-9_-]` (a
  Cyrillic look-alike, a space, a dot), (2) one of its words (split on `_`,
  `-`, camelCase and digits: `radio_ble_pin`, `blePin`, `newPassword1`) is a
  secret word such as `password`, `pass`, `secret`, `token`, `key`, `apikey`,
  `pin`, `code`, `otp`, `totp`, `hash`, `seed`, `cookie`, `session`, `auth` or
  `credential`, or (3) the key with separators removed contains a fragment
  such as `password`, `secret`, `token`, `apikey`, `privatekey` or
  `setupcode`. `ping`, `pinned`, `user_id` or `reason` pass; a harmless key
  that matches is refused, which is accepted. Failed
  logins are written without authentication, so every field is bounded:
  `action` 64, `outcome` 32, `target` 256, `ip` 64, `via` 32 characters
  (truncated), and `details` larger than 2 KiB serialised are stored as
  `{"bytes": <size>, "truncated": true}`. Server code never calls the repository
  directly: it writes through `app/auth/audit.py` (see "Audit log" below).
- **Settings** live in their own single-row table `auth_settings`, not in
  `app_settings`, so the setup code hash never reaches `AppSettings`, the v1
  backup settings or the settings API. `AuthSettings` exposes `setup_code_set`
  instead of the hash; `get_setup_code_hash()` / `set_setup_code_hash()` are the
  only accessors. Every read and write raises `AuthSettingsMissingError` when
  the row is missing instead of falling back to defaults (fail closed); the
  migration and `SCHEMA_TABLES` insert it at every start. CIDRs (`trusted_networks[].cidr`, `trusted_proxies`) are
  canonicalized with `ipaddress`; an invalid one is refused. The CORS list stays
  in `app_settings.auth_allowed_origins` (`app/cors.py`), where it shipped first.
- **Nothing secret in a log or an exception.** No password, hash or token is
  logged by this code, and the `aiosqlite` logger is clamped to INFO
  (`app/log_redaction.py`) because its DEBUG output prints every statement's
  parameters. `UserCredentials`, the one model that carries a hash, holds it as
  `SecretStr` with `hide_input_in_errors`.

## Audit log

`app/auth/audit.py` is the one way server code writes `auth_audit` (the CLI
too): `record(action, outcome, *, actor, target, details)`, `record_request()`
(the actor of a FastAPI request) and the `audited(actor, action)` context
manager (`success` when the block ends; on an `HTTPException` `refused` for a
403, `error` for a 5xx, `failure` otherwise, with `details.status`; `error` on
any other exception; always re-raised; a cancelled block writes nothing; the
block can set the target and add details through the yielded `AuditScope`). Routes take the actor as a dependency,
`actor: audit.RequestActor = audit.SYSTEM_ACTOR`, so a direct call in a test
needs no request. Writing never fails the action: a database error is logged
(action and outcome) and swallowed; a `details` key the secret filter refuses is
logged at ERROR and the row is not written (a bug at the call site).

- **Row.** `actor_user_id` (None for an anonymous caller, the CLI and the
  server), `actor_token_id` (bearer tokens, later), `ip` (`client_ip()`), `via`
  (`lan`, `ingress`, `trusted`, `relay`, plus `cli` for `meshloom-admin` and
  `system` for the server on its own), `outcome` (`success`, `failure`,
  `refused` for a 403, a CLI precondition or a login refused per IP, `delayed`
  for a login refused while a delay runs ("Login delays"), `error`), `target` (`user:<id>` for a known account, `username:unknown` for a
  typed name that matches no account (`typed_username_target()`: the typed text
  is never stored, so a password typed into the username field never reaches
  the log), `session:<id>`, `bot:<id>`,
  `restore_job:<id>`, `auth_settings.<field>`).
- **Actions** (`AuditAction`, a `Literal`, so pyright refuses a typo):

  | Action | Written by |
  |---|---|
  | `setup` | `POST /auth/setup` (`failure` + `details.reason` for a wrong code, `error` when first-run preparation failed) |
  | `login`, `logout` | `POST /auth/login` (`failure`, target the matching `user:<id>` or `username:unknown`), `POST /auth/logout` |
  | `session.revoked` | `DELETE /auth/sessions[/{id}]`, `meshloom-admin revoke-sessions` |
  | `user.created` | ingress first visit, the 4.x Basic import (`system`), `meshloom-admin create-admin` |
  | `user.changed` | `PATCH /auth/me` display name, a Home Assistant rename |
  | `user.disabled`, `user.enabled`, `users.listed`, `setup_code.issued` | `meshloom-admin` |
  | `password.changed` | `PATCH /auth/me` (`failure` for a wrong current password), `meshloom-admin reset-password` |
  | `settings.changed` | `PATCH /auth/settings`, one row per field changed |
  | `bot.created`, `bot.changed`, `bot.deleted`, `bots.disabled` | `/api/fanout` for `type = bot` only, refusals included (403 while bots are disabled: `refused`; name, enabled, changed field names; never the code), `POST /fanout/bots/disable-until-restart` |
  | `backup.restored` | `POST /settings/backup/restore`: the v1 merge or a refusal in the route, the job's end otherwise (`details.steps`) |
  | `private_key.imported`, `factory_reset` | `PUT /radio/private-key`, `POST /radio/factory-reset` (`details.identity`: the first 12 hex of the public key) |
  | `update.triggered` | `POST /updates/apply` (`trigger: manual`), the auto-update window (`trigger: auto`, `system`; `error` when the helper could not be started) |

  Reserved for the tickets that add their feature: `user.deleted` (user
  management), `token.created`, `token.revoked`, `pairing.used`,
  `totp.enrolled`, `totp.disabled`, `passkey.added`, `passkey.removed`,
  `plugin.created`, `plugin.changed`, `plugin.disabled`, `plugin.installed`,
  `plugin.updated`, `plugin.removed` (Store), `remote.linked`,
  `remote.enabled`, `remote.disabled` (Remote). A new sensitive action adds its
  name there and writes its row through this module.
- **Reads** (`GET /auth/audit`) are not audited (cadrage §4.5 asks only for an
  admin-only, cursor-paged API).
- **Retention.** `purge_audit()` runs in the hourly housekeeping loop
  (`app/services/stale_contacts.py`) and deletes rows older than
  `auth_settings.audit_retention_days`, **366 days** by default (owner decision
  Q-A14), changed through `PATCH /auth/settings`. It deletes 1000 rows per
  transaction (`AUDIT_PURGE_BATCH`), so a large retention drop never holds the
  write lock for long.
- Tests: `tests/test_auth_audit.py` (one row per event, the API, the purge, and a
  sweep that no row holds a password, the installation code, a session cookie or
  a private key typed in the flows).

## Backups

The new tables and settings are not in any JSON backup yet. The `users` and
`api_tokens` sections of the full backup are a later ticket. `sessions` and
the setup code are never exported; `auth_audit` is not in the JSON backup but
stays in the SQLite download (IPs, usernames). The SQLite download
(`GET /settings/backup/database`) keeps the accounts and the audit trail but
not their secrets: `prepare_sqlite_snapshot()` sets `users.password_hash`,
`users.totp_secret` and `auth_settings.setup_code_hash` to NULL and empties
`sessions` in the copy, under `secure_delete`. A restored snapshot therefore
has accounts that cannot log in with a password until an admin reset.

## The gate

`AuthMiddleware` (`app/auth/middleware.py`) is the innermost middleware of
`app/main.py` (inside CORS, so preflights never reach it). For every `http`
and `websocket` scope:

1. **Principal.** It resolves a `Principal` (user, role, `via`, credential,
   session or token id, scopes) and stores it in `scope["state"]["principal"]`;
   routes read it with `get_principal(request)`. Credentials are tried in
   this order, and the first one present decides:
   - `Authorization: Bearer`: device and API tokens (a later ticket,
     `resolve_bearer_token()` is the seam). No token is valid yet, so a bearer
     request is anonymous, with `auth_invalid_credentials` on a protected
     route.
   - `Authorization: Basic`: the compatibility credential (below).
   - The session cookie, under the name for the request's scheme (below).
     Never read under ingress.

   A Basic or login check that finds the argon2id queue full answers 503
   `auth_busy` with `Retry-After: 1` at once (below).
2. **CSRF.** Every `POST`, `PUT`, `PATCH` and `DELETE` not authenticated by a
   bearer token that resolved must pass `origin_policy.unsafe_request_allowed()`,
   else 403 `auth_origin_rejected`. The rule is Go's
   `net/http.CrossOriginProtection` plus the allowlist:
   `Sec-Fetch-Site: same-origin` or `none` passes; a request with neither
   `Sec-Fetch-Site` nor `Origin` passes (not a browser: curl, scripts, Home
   Assistant sensors; every browser sends `Origin` on a cross-origin unsafe
   request); otherwise `Origin` must be same-origin (`Host` or the first
   `X-Forwarded-Host`, as for the WebSocket) or listed in Allowed origins
   (`auth_allowed_origins`). `Origin: null` is refused. It applies to anonymous
   requests too (login CSRF), and GET is never blocked. A resolved bearer token
   is exempt (a browser page cannot add an `Authorization` header across
   origins without a CORS preflight, which only listed origins pass); a bearer
   header that does not resolve gets no exemption.
3. **Default deny.** Any path under `/api` that is not in `PUBLIC_ROUTES`,
   and the API docs (`/docs`, `/redoc`, `/openapi.json`), need a principal.
   Without one: 401 `auth_setup_required` while no account exists,
   else `auth_invalid_credentials` when a credential was sent and did not
   check, else `auth_required`. The other paths outside `/api` (the frontend,
   `NON_API_ACCESS`) stay public, so the login page can load. A database error
   while resolving fails closed (anonymous). Which role may then call the
   route is the permission registry's decision (below).
4. **WebSocket.** An upgrade to `/api/...` without a principal is refused
   before the route runs, so before `accept()`: close code 1008, which uvicorn
   sends as HTTP 403 (same as the foreign-Origin refusal of `app/websocket.py`,
   which still runs in the route).

Every answer under `/api/auth/` carries `Cache-Control: no-store`. The 401
carries `WWW-Authenticate: Basic realm="Meshloom", charset="UTF-8"` only while
the instance came from 4.x Basic (`auth_settings.basic_imported`), so a
browser that used the native dialog keeps working; never on `/api/auth/*`.

Public routes (`PUBLIC_ROUTES`): `GET /api/health`, `GET /api/auth/session`,
`POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/logout`.

**Tests.** `tests/conftest.py` signs every app request in as an admin
(`_auth_as_admin`, it replaces `middleware.resolve`), so route tests need no
login; the `sign_in_as("kiosk", scopes=...)` fixture signs them in with
another role. Tests of the gate itself are marked `@pytest.mark.real_auth`. The
`fast_argon2` fixture hashes at libsodium's minimum cost.

### `via`

How the request reached the instance (`request_via`, `app/auth/middleware.py`).
`relay` when the relay dispatched it (`scope["state"]["via"]`, set by the relay
client of a later ticket); `ingress` when `MESHLOOM_INSTALL_KIND` is `addon`,
the ASGI peer is the Supervisor (`SUPERVISOR_IP`, `172.30.32.2`) and the request
was not relayed; `lan` otherwise. `trusted` comes with a later ticket. Under
ingress **no cookie is ever set or read**: the Home Assistant origin is shared
by every add-on, so the gate drops any `Set-Cookie` from an ingress answer.

### Ingress

`app/auth/providers/ingress.py`. On an `ingress` request the Supervisor's
`X-Remote-User-Id`, `X-Remote-User-Name` and `X-Remote-User-Display-Name` name
the Home Assistant user (the Supervisor strips copies sent by the browser, and
only the Supervisor address is trusted: a published port 8000 reaches the
add-on from the Docker gateway, so the same headers sent there are ignored and
that address needs a normal login). They are read nowhere else; proxy headers
stay `app/origin_policy.py`'s. The provider uses no CIDR rule.

- The HA user maps to `users.provider = 'ingress'`, `external_id = <HA id>`
  (unique per provider), created on first sight with no password. The first
  ingress identity of the instance is `admin`, whatever local accounts exist
  (cadrage R3: a 4.18.x add-on with `basic_auth_*` has an imported local admin,
  or one made with `meshloom-admin`, and the sidebar, the add-on's only way in
  by default, must still reach an administrator); the next ones get
  `auth_settings.ingress_default_role` (`member` by default). This is safe
  because the manifest leaves `panel_admin` at its default (true): only Home
  Assistant administrators see the panel, so the first ingress user is one of
  them. `panel_admin` is a manifest key, not a user option; never set it to
  false without revisiting this rule. A lock makes the
  check and the insert one step. When it is the first account, the
  installation code is cleared, so a 4.18.x add-on lands signed in with no
  setup step. Audit row `user.created` (`via = ingress`, `details.role`); a
  display name Home Assistant changed writes `user.changed`
  (`details.source = home_assistant`).
- The username is HA's `X-Remote-User-Name` when free, else `<name>-ha`,
  `<name>-ha-<id8>`, then `ha-<id>`. The display name follows HA's on every
  request. Header values are re-decoded from latin-1 to UTF-8 (the Supervisor
  writes UTF-8).
- The principal has `credential = "ingress"`, which is ambient (it follows HA's
  own ingress cookie): unsafe methods pass the CSRF rule like a session. The
  HA frontend sends `Sec-Fetch-Site: same-origin`, and the Supervisor sets
  `X-Forwarded-Host` (`same_origin`, MSHL-274).
- A disabled ingress account, or an id the provider refuses (empty, over 128
  characters, not printable ASCII), gives no principal: 401
  `auth_invalid_credentials`. Without user headers (an anonymous ingress
  session, a direct link) the request is anonymous: `/api` answers 401
  `auth_required`, the frontend loads, and `GET /auth/session` says
  `methods: {password: false, ingress: true}`, which the frontend shows as a
  "sign in through Home Assistant" page instead of the login or setup form.
- `POST /auth/login` and `POST /auth/setup` answer 403 `auth_ingress_only`
  under ingress: no cookie could keep the session.
- Not yet: the ingress list with role editing and the default-role setting
  belong to the users API and the Users & Access section (MSHL-45, MSHL-47).
  Until then `meshloom-admin disable-user` / `enable-user` work on ingress
  accounts, and a role is changed with SQL only.

**Manual check on a real Home Assistant (to record before 5.0.0, owner check
MSHL-279).** Not run yet; record the result here.

1. Install the add-on from a `preversion-5` image on a fresh Home Assistant
   install, open it from the sidebar: Meshloom opens signed in, no setup page,
   and the user menu shows your HA display name with the Administrator role.
2. Temporary log line, or `docker logs` with `log_level: DEBUG`: confirm the
   peer address is `172.30.32.2` and the three `X-Remote-User-*` headers arrive
   (and `X-Forwarded-Host`); note a non-ASCII display name renders correctly.
3. Sign in to HA as a second Home Assistant administrator: the account gets
   the Member role.
4. Sign in to HA as a non-administrator and open the ingress URL copied from
   an administrator's session: no access (the panel is admin-only through
   `panel_admin`, which users cannot change).
5. Upgrade a 4.18.x add-on (with and without `basic_auth_*`): the sidebar lands
   signed in with no setup step.
6. Publish port 8000: `curl -H 'X-Remote-User-Id: x' http://<ha>:<port>/api/contacts`
   answers 401, and the login page asks for a password there.
7. After `meshloom-admin disable-user <name>` on an ingress account, the
   sidebar shows the Home Assistant sign-in page, with no browser password
   prompt.
8. Live updates through the sidebar, Nabu Casa and the Companion app (the
   WebSocket checks of MSHL-279).

### Proxy headers

`app/middleware/proxy_headers.py` is the only interpreter of `X-Forwarded-For`
and `X-Forwarded-Proto`. It is uvicorn's own `ProxyHeadersMiddleware` (and
`_TrustedHosts`: CIDRs, walk from the right), registered by `app/main.py` outside the auth
gate and CORS and rebuilt whenever `auth_settings.trusted_proxies`
changes (`AuthSettingsRepository.update()` calls `proxy_headers.apply()`;
`load()` at startup). The list is addresses or CIDRs in canonical network form,
empty by default; loopback is not trusted implicitly, a network wider than
`/8` (IPv4) or `/16` (IPv6) and an IPv4-mapped entry are refused (an IPv4 entry
also matches its `::ffff:` form, for a dual-stack listener)
(`normalize_proxy`; stored entries that fail it are dropped on read). It is
read and written through `GET/PATCH /api/auth/settings` (`admin`; a refused
entry is 400 `auth_trusted_proxies_invalid`; a change is audited as
`settings.changed`). From a listed peer only, `scope["client"]` becomes the
rightmost `X-Forwarded-For` address that is not itself trusted (all trusted:
the leftmost), and `scope["scheme"]` becomes `X-Forwarded-Proto` when it is
`http`, `https`, `ws` or `wss`. Everything after it reads the scope:
`client_ip()` (sessions, audit, login delays), `request_is_secure()` (the
`__Host-` cookie; no header read) and later the HSTS header. The TCP peer
before the rewrite stays in `scope["state"]["peer"]` (`peer_address()`): the
ingress check compares that one with `SUPERVISOR_IP`, so listing the
Supervisor as a proxy does not hide ingress, and a trusted proxy forwarding
`172.30.32.2` is not the Supervisor. `app/origin_policy.py` still reads
`X-Forwarded-Host`, for same-origin only (WebSocket and CSRF; a browser page
cannot set it).

Every launcher starts uvicorn with `--no-proxy-headers` (`meshloom/run.py`,
`Dockerfile`, `pkg/nfpm/meshloom.service`, `scripts/setup/install_service.sh`,
the snake-oil compose of `install_docker.sh`, and the documented commands):
with uvicorn's default (`forwarded_allow_ips=127.0.0.1`) a loopback caller's
`X-Forwarded-For: 172.30.32.2` would become the peer before the app sees it.
`tests/test_auth_ingress.py` parses the launchers with uvicorn's CLI and
`tests/test_proxy_headers.py` greps every tracked command line.
A 401 under ingress never carries the Basic challenge (the browser would
prompt inside the Home Assistant panel).

## Permissions

`app/auth/permissions.py`. Five permissions, granted by the four roles
(cadrage §4.2, owner decision Q-A7):

| Role | Permissions | In practice |
|---|---|---|
| `kiosk` | `read` | Channel messages (muted and pending channels included), contacts, channels, packets (control packets included), stats, map, health. Never a direct message; no settings, no profile, no sessions |
| `viewer` | `read`, `read_private` | `kiosk` plus direct messages and marking read; its own profile and sessions |
| `member` | `viewer` + `send` | Send, create and edit contacts, channels and contact groups, favourites, pins, mutes, the browser's push subscription, trace and path discovery |
| `admin` | all, `repeater_admin` included | Settings, radio, repeaters and rooms, fanout and bots, backup and restore, private key, updates, Community, push preferences, debug |

`admin` implies every other permission. `repeater_admin` is held only by
`admin`; a principal's `scopes` (token scopes, M3) narrow its role's
permissions, never extend them, so a token scoped to `repeater_admin` reaches
the repeater and room routes and nothing else.

**Declaring a route.** Every route declares exactly one rule in its decorator:

```python
@router.post("/channel", dependencies=[Depends(require("send"))])
```

`public()` marks a route reachable without a principal; it must also be in
`PUBLIC_ROUTES` (the test checks both lists agree). `own_account()` is for the
caller's own profile and sessions: every role but `kiosk`, and never through
a scoped principal (a scope only narrows, and none grants the account). A route outside
`/api` goes in `NON_API_ACCESS` (`public` or `authenticated`) in
`app/auth/middleware.py`.

**Default deny.** `enforce_declaration` is an app-wide dependency
(`FastAPI(dependencies=...)` in `app/main.py`): an `/api` route that declares
nothing answers 403 to everyone but an admin. `tests/test_auth_route_coverage.py`
walks `app.routes` (through FastAPI's lazy includes, plus the frontend routes a
build registers) and fails on a route without exactly one declaration, a
non-API path missing from `NON_API_ACCESS`, or a WebSocket event type missing
from `WS_EVENT_PERMISSIONS`.

**Refusal.** 403 `{"detail": {"code": "auth_forbidden", "message", "params":
{"permission"}}}` (`permission` is `account` for `own_account()`), translated
by the frontend (`errors.auth_forbidden`, slice `x`). A refused WebSocket
upgrade is closed with 1008 before `accept()`. The permission check runs before
body validation, so a refused caller never gets a 422.

**Direct messages (`read_private`).** Without it:

- `GET /messages` and `GET /messages/around/{id}` answer `CHAN` rows only
  (search `q` and `since_id` included); `type=PRIV`, any other type, or a
  `conversation_key` longer than a channel key (32 characters: a contact key)
  answers 403.
- `GET /read-state/unreads` keeps only the `channel-*` keys of every map.
- `GET /contacts` and the contact in `GET /contacts/analytics` have
  `last_contacted` and `last_read_at` emptied; the analytics count no DM
  (`dm_message_count` 0, `includes_direct_messages` false, channel-only
  activity).
- `GET /packets/{id}` and `GET /packets/history` show a packet decrypted from a
  DM as undecrypted: a `TEXT_MESSAGE` packet, or a decryption that names no
  channel (`packet_decryption_is_private()`, fail closed). Never `contact_key`:
  a channel packet keeps its resolved sender there, so channel and control
  packets stay whole for `kiosk`. The live `raw_packet` frame uses the same rule.
- The JSON and full backups, the debug snapshot and the database download are
  `admin` routes.

**WebSocket events.** `WS_EVENT_PERMISSIONS` classifies every `WsEventType`
(`app/events.py`); an unknown type goes to admins only. `ws_manager` keeps the
principal of each socket and delivers an event only to the clients holding its
permission. A `message` that is not `CHAN` is not sent to a client without
`read_private`, and a `raw_packet` decrypted from a DM reaches it without its
decryption. `radio_wifi` is `admin` and `plugin_status` is `send`; the other
types are `read`. `health` goes to every `read` client, but one without `send`
gets the plugins' `last_error` as `null` (also on the REST route;
`app/plugins/lifecycle.py`). Finer
per-event filtering (contact activity fields, toasts) is Access T26.

**4.x upgrade.** The account imported from Basic and the first administrator
are `admin`: a single-admin installation sees no change.

## Sessions and the cookie

- A login or a setup creates a new session row (never reuses one: no
  fixation) holding the SHA-256 of a 256-bit secret; the secret goes only in
  the cookie. Lifetime: `auth_settings.session_lifetime_days` (30).
- **Sliding expiry.** A request whose session was last seen more than a
  minute ago moves `expires_at` to now + lifetime (`touch()`, one write a
  minute at most) and gets the cookie again with the same `Max-Age`.
- **Cookie.** `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` = lifetime.
  Over https (`scope["scheme"]`, set from `X-Forwarded-Proto` only for a trusted
  proxy, see "Proxy headers") its name is
  `__Host-meshloom_session` with `Secure`; over http `meshloom_session`
  without `Secure` (browsers refuse a `Secure` cookie from an insecure origin).
  Only the name for the request's scheme is read, so a cookie planted over
  http is ignored on https.
- A disabled account, a revoked or expired session: anonymous at once.
- Logout revokes the session and answers `Set-Cookie` with `Max-Age=0` and
  `Clear-Site-Data: "cookies"`.
- **Throttled last-seen.** The middleware calls `touch()` only when the
  session it read was last seen a minute ago or more, and `touch()` itself
  writes only under the same condition, so a burst of requests costs one write
  a minute per session, not one per request. WebSocket frames (the frontend's
  30 s `ping`) slide the session too (`ws_manager.note_activity()` →
  `sessions.slide_session()`, at most one attempt a minute per socket), so a
  page that only keeps its socket open, such as a kiosk display, is not signed
  out at expiry; no cookie goes out on that path, the next HTTP request sends
  it again. There is no absolute lifetime: only the sliding
  `session_lifetime_days`.
- **Revocation closes the WebSockets.** `ws_manager` (`app/websocket.py`)
  indexes every socket by the session id and token id of its principal.
  `sessions.revoke_session()` (logout, `DELETE /auth/sessions/{id}`),
  `sessions.revoke_user_sessions()` (`DELETE /auth/sessions`, a password
  change) and the cap in `sessions.issue_session()` revoke the rows, then call
  `ws_manager.close_sessions()`: the sockets close at once with code 1008
  (`REVOKED_CLOSE_CODE`). `ws_manager.close_tokens()` does the same for API and
  device tokens (for the token ticket). While a session socket is open, the
  manager also rechecks every `SESSION_RECHECK_INTERVAL_SECONDS` (30 s) which
  of its sessions are still live (`filter_live()`, one query) and closes the
  others: that catches `meshloom-admin` (another process, writing the database),
  expiries and disabled accounts. Basic-auth sockets carry no session and are
  not rechecked. A socket keeps the role it opened with (the recheck checks
  liveness, not the role): a future role-change path must revoke the user's
  sessions through `revoke_user_sessions()` so the sockets reconnect with the
  new role.
- **Purge.** `sessions.purge_sessions()` runs in the hourly housekeeping loop
  (`app/services/stale_contacts.py`) and deletes expired and revoked rows.

## `Authorization: Basic` and the 4.x Basic auth

Up to 4.18 `MESHCORE_BASIC_AUTH_USERNAME` / `_PASSWORD` were the only gate.
Upgrade path (cadrage P3, `app/security.py`):

- At startup, when both are set and no account exists, they become an `admin`
  account (no password policy, no installation code: the credentials prove
  ownership) and `auth_settings.basic_imported` is set. The variables are then
  never read again; each start logs a warning while they are still set.
  A user name the account rules refuse is logged as an error and the normal
  first run applies.
- The header stays accepted in 5.0 and 5.1 (removed in 5.2), checked against
  any account with a password. It is ambient (a browser replays it), so the
  CSRF rule applies to it.
- A successful check is cached in memory for 5 minutes under
  a low-cost scrypt of the password (n=1024, r=8: 1 MiB, about a millisecond;
  salt = a random per-process secret + `username_key`; at most 256 entries): 100 requests, one
  argon2id verification. The scrypt runs in a worker thread. The account is
  read again on every request and the cache entry is used only while its
  stored password hash is the one it was verified against, so a disabled
  account or a password reset applies at once, even from another process and
  in the same second. A password change through `PATCH /api/auth/me` drops the
  entries.
- **Failures.** After a failed check, Basic attempts from that client IP that
  are not already cached fail at once for 3 seconds, without argon2id (at most
  1024 IPs tracked). The IP is `client_ip()`: behind a trusted proxy each
  forwarded client has its own cooldown, otherwise the proxy's address is
  shared by all of them. A script sharing the IP of a failing client (Docker
  gateway, an untrusted proxy) waits those seconds too. Basic does not go
  through the login delays.
- **Load cap** (`app/auth/providers/password.py`). At most 2 argon2id checks
  run and 8 wait; one more answers 503 `auth_busy` at once, on any route and
  on `POST /api/auth/login`, instead of queueing behind a flood. On the login
  route the login delays run before it ("Login delays").
- A user name longer than 64 characters (the account limit) cannot be
  imported: the error is logged and the first run continues without it.

## Login delays

`app/auth/ratelimit.py` (cadrage, "Tentatives et délais"). `POST /api/auth/login`
calls `login_limiter.check(ip, username)` before any password work, then
`record_failure()` or `record_success()`. A refused attempt answers 429
`auth_rate_limited` with `params.retry_after` and `Retry-After` (whole
seconds, at least 1), never reaches the argon2id semaphore, and the frontend
shows `errors.auth_rate_limited` (slice `aa`). No hard lockout by username,
and none by IP alone behind Docker:

| Rule | Key | Behaviour |
|---|---|---|
| Progressive delay | `(address_key, username_key)` | 5 free failures, then each failure closes the pair for 1, 2, 4, ... seconds, capped at 60. The first attempt after the delay is checked; a success forgets the pair |
| Global slow-down | `username_key` | After 5 failures from any IP, at most one attempt per second on that name (`check()` takes the slot). Never more than one second to wait, so the right password always gets in |
| Hard refusal | `address_key` | Beyond 100 failures in 15 minutes (`aiolimiter.AsyncLimiter(100, 900)` per address key, read with `has_capacity()`: one more every 9 s) |

- **Address key** (`LoginLimiter.address_key(scope)`). `client_ip()` (the TCP
  peer, or what a trusted proxy forwarded, "Proxy headers"; via the relay,
  later, the address in the frame), IPv4 as is, IPv6 by its `/64` (a host
  usually holds a whole /64 and could rotate inside it; an IPv4-mapped address
  is its IPv4). **None** when the address does not identify a client: the pair
  and per-IP rules are then skipped and only the username rule applies:
  - `install_kind` `compose` or `container` with `auth_settings.trusted_proxies`
    empty (read live): every client may arrive from the Docker gateway;
  - `X-Forwarded-For`, `X-Real-IP` or `Forwarded` present from a peer that is
    not a trusted proxy and whose address is not global (loopback, private,
    CGNAT): an undeclared reverse proxy, all its clients on one address. Only
    the headers' presence is read; `proxy_headers.py` stays their one interpreter.

  Otherwise one guesser sharing the owner's address would delay the owner by up
  to a minute per attempt (review r1 of #217). Guarantee per deployment: with a
  client address of its own (package, source, a declared trusted proxy, a
  direct LAN client) all three rules apply and the owner, on another address,
  only meets the one-second username slot; on a shared address the owner waits
  at most one second behind a slow guesser. Under a flood on one name from many
  addresses, the owner competes for that one slot a second in both cases.
- **Memory.** Pair and username entries go `FORGET_AFTER_S` (15 min) after
  their last failure once no delay runs; an IP bucket goes once it has leaked
  empty. A sweep runs at most once a minute from `check()`; each table holds
  at most 4096 keys (the least recently failed goes). Restarting empties it.
- **Audit.** One `login` row per delay, not per refused attempt: outcome
  `delayed` (`details.reason` `pair` or `username`) or `refused` (`ip`), with
  `details.retry_after`; the typed name follows `typed_username_target()`.
- **Clock.** The limiter takes a clock; `_FailureWindow` hands it to
  aiolimiter in place of the loop's time, so the tests use a fake clock.
- **Not covered.** Concurrent attempts on one pair can each pass `check()`
  before the first failure is recorded (bounded by the argon2id queue of 10).
  Under a sustained flood on one name from many addresses (or many /64s), the
  owner competes for the one slot a second; the per-IP rule and the relay's
  edge lock answer that, not a lock on the name.

## First run and the installation code

`prepare_first_run()` (`app/auth/setup_code.py`) runs in the lifespan, while
no account exists:

1. The Basic variables, when set, are imported (above). Done.
2. On a database that existed before this start (the file was there, or the
   radio history says so: the same `existing_database` as the legacy imports)
   an 8-digit installation code is generated, its SHA-256 stored in
   `auth_settings.setup_code_hash`, and the code logged at WARNING. A new code
   replaces the old one at every start. `meshloom-admin setup-code` prints a
   fresh one (`issue_setup_code()`).
3. On a database created by this start nothing is asked: whoever installed it
   is in front of it. A restart before the setup makes the database existing,
   and the code is then asked.

**Fail closed.** `POST /api/auth/setup` answers 503 `auth_setup_unavailable`
until `prepare_first_run()` has succeeded in this process: when it raised at
startup (a locked database), the setup route runs it again and keeps refusing
while it fails. Setup is also refused when the database existed before this
start and no code hash is stored, whatever code is sent. `GET
/api/auth/session` then says `setup_code_required: true`.

Once an account exists the hash is cleared. `POST /api/auth/setup` checks the
code with digits only (`1234 5678` and `1234-5678` match); wrong codes are
answered one at a time, one second apart (a lock around setup): one guess a
second against 10^8 codes, so the login delays do not apply to setup. When the
setup route retries a failed `prepare_first_run()`, it keeps a code hash that
is already stored (`keep_stored_code`): it may be the one `meshloom-admin
setup-code` printed while the startup preparation was failing. The code is
never stored in clear and never in an audit row.

## Password policy

`password_policy_violation()` (`app/auth/passwords.py`): at least 12
characters (after NFC), at most 1024, not equal to the user name (compared
after NFKC and case folding), not in `COMMON_PASSWORDS`. The usual top-100
lists are almost entirely shorter than 12 characters, so the length rule
already refuses them; the embedded list is 100 common passwords of 12
characters or more (keyboard walks in QWERTY, AZERTY and QWERTZ, digit runs,
breach-list words with digits, English and French, plus names a Meshloom user
reaches for). Refusal: 400 `auth_password_policy` with
`params.reason` (`too_short`, `too_long`, `same_as_username`, `too_common`)
and `params.min_length`. Applied at setup and on a password change, not to
the Basic import.

## Routes (`/api/auth`)

Errors are `{"detail": {"code", "message", "params"?}}`; the frontend
translates `errors.<code>` (slice `x`). Bodies with a password use
`SecretStr` and `hide_input_in_errors`; the app-wide 422 handler drops inputs.

| Route | Access | Behaviour |
|---|---|---|
| `GET /auth/session` | public | Anonymous: `{authenticated: false, setup_required, setup_code_required, methods: {password, ingress}, auth_compat: {basic}, secure}` (`secure`: the server saw https, the frontend warns when the browser is on https and it is false; `methods` is `{password: false, ingress: true}` under ingress, else the reverse), **no instance name or version**. Signed in: plus `user {id, username, display_name, role}`, `via`, `instance {name, version}` (name: the radio's name, or absent) |
| `POST /auth/setup` | public while no account exists | `{username, password, display_name?, setup_code?}`. 409 `auth_setup_done` once an account exists; 403 `auth_setup_code_invalid` when a code is required and wrong or missing; 400 `auth_password_policy`, `auth_username_invalid`; 503 `auth_setup_unavailable` (fail closed, above). Creates the `admin`, clears the code, opens a session (cookie), answers like `GET /auth/session`. 403 `auth_ingress_only` under ingress (the first HA user becomes the admin there) |
| `POST /auth/login` | public | `{username, password}`. One generic 401 `auth_invalid_credentials` for an unknown name, a wrong password, a disabled account or one without a password (ingress, kiosk); each runs one argon2id check, against a fixed hash when there is none, so the time does not tell them apart. At most two checks run at once, eight wait, then 503 `auth_busy`. 429 `auth_rate_limited` (`params.retry_after`, `Retry-After`) while a login delay runs, before any password check ("Login delays"). Rehashes on success when `PARAMS` changed. Answers like `GET /auth/session`. 403 `auth_ingress_only` under ingress, before any password check |
| `POST /auth/logout` | public, idempotent | Revokes the current session, clears the cookie |
| `GET /auth/sessions` | own account (not `kiosk`) | The user's active sessions, newest first, `current` marks this one |
| `DELETE /auth/sessions` | own account (not `kiosk`) | Revokes all the user's sessions except this one: `{revoked}` |
| `DELETE /auth/sessions/{id}` | own account (not `kiosk`) | Revokes one of the user's sessions; 404 `auth_session_not_found` for anyone else's |
| `GET /auth/me` | own account (not `kiosk`) | Profile: `id, username, display_name, role, provider, has_password, must_change_password, created_at` |
| `PATCH /auth/me` | own account (not `kiosk`) | `display_name`; or `current_password` + `new_password` (403 `auth_invalid_credentials` when the current one is wrong, 400 `auth_password_policy`), which revokes the user's other sessions |
| `GET /auth/settings` | `admin` | Access settings: `{trusted_proxies, audit_retention_days}` (canonical CIDRs; days, 366 by default) |
| `PATCH /auth/settings` | `admin` | Change them; applies to the next request. 400 `auth_trusted_proxies_invalid` for a non-IP entry, a network wider than `/8` (IPv4) or `/16` (IPv6), an IPv4-mapped entry or more than 32. Recommend the proxy's own address only: a Docker network or its gateway would trust clients reaching a published port through it. `audit_retention_days` 1 to 36500 (422 otherwise) |
| `GET /auth/audit?cursor=&limit=` | `admin` | The audit log, newest first: `{entries, next_cursor}`. `limit` 1 to 500 (100 by default); `next_cursor` is opaque, sent back as `cursor` for the older page, null on the last; a malformed cursor is 400 `auth_audit_cursor_invalid`. Reading writes no row |

Every route above that changes something writes one audit row ("Audit log").

`GET /api/health` is public: anonymous callers get exactly `{"ok": true}`
(probes, the kiosk launcher), a signed-in caller the full payload.

### Setting up without the UI

The setup page does this in a browser; from a shell (scripts, tests) it is:

```bash
curl -c jar -H 'Content-Type: application/json' \
  -d '{"username":"owner","password":"a long passphrase","setup_code":"12345678"}' \
  http://HOST:8000/api/auth/setup        # setup_code only on an existing database
curl -c jar -H 'Content-Type: application/json' \
  -d '{"username":"owner","password":"a long passphrase"}' http://HOST:8000/api/auth/login
curl -b jar http://HOST:8000/api/auth/session
```
