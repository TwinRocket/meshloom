# Local accounts

Meshloom 5.0.0 makes login mandatory (LAN included) with four roles: `admin`,
`member`, `viewer` and `kiosk`. This page covers the data layer (tables,
password hashing, opaque tokens, the repository) and the auth core: the gate
that resolves a principal for every request, the CSRF rule, the first-run
setup with its installation code, the `/api/auth/*` routes and the permission
registry (which role may call which route, and receive which WebSocket event).
The login and setup pages are in `frontend/src/auth/` (`frontend/AGENTS.md`,
"Session gate"). Home Assistant ingress identities are in "Ingress" below,
trusted networks in "Trusted networks", API and device tokens in "API and
device tokens" (the tokens page is
`frontend/src/components/settings/AccessTokensPanel.tsx`), the TOTP second
factor in "Second factor (TOTP)" (its pages: `frontend/src/auth/TotpEnrolment.tsx`,
`SecondFactorStep.tsx`, `RecoveryCodesView.tsx`, `EnrolmentRequiredPage.tsx` and
`frontend/src/components/settings/AccessSecurityPanel.tsx`), passkeys in
"Passkeys" (their pages: `frontend/src/auth/passkeys.ts`, the login button in `LoginPage.tsx`, `SecondFactorStep.tsx` and `frontend/src/components/settings/AccessPasskeysPanel.tsx`; `frontend/AGENTS.md`, "Session gate"). Device pairing (a single-use code,
shown as a QR, traded for a device token) has its own contract page,
`docs/dev/device-pairing.md`; its settings screen comes with a later Access
ticket. The manual checklist run before the 5.0.0 tag is the last
section ("Manual checklist before the 5.0.0 tag"). Like every doc here
this is a hypothesis: the code wins. The recovery CLI (`meshloom-admin`, `app/cli.py`)
works on the same tables.

## Where things are

| Piece | File |
|---|---|
| Tables (existing databases) | `app/migrations/_101_auth_data_model.py` |
| Tables (fresh databases) | `SCHEMA_TABLES` / `SCHEMA_INDEXES` in `app/database.py`, textually identical |
| Typed models | `app/auth/models.py` |
| Password hashing (argon2id) | `app/auth/passwords.py` |
| Opaque secrets (sessions, setup code, API tokens) | `app/auth/tokens.py` |
| API and device tokens (Bearer) | `app/auth/providers/token.py`, `app/routers/auth_tokens.py`, `app/migrations/_108_api_tokens.py` |
| Repository | `app/repository/auth.py` |
| Principal (who is calling) | `app/auth/principal.py` |
| The gate (ASGI middleware, HTTP + WebSocket) | `app/auth/middleware.py` |
| Permissions (roles, `require()`, default deny, WebSocket event classes) | `app/auth/permissions.py` |
| Sessions and the cookie | `app/auth/sessions.py` |
| Audit log (events, retention purge) | `app/auth/audit.py`, `tests/test_auth_audit.py` |
| Username + password provider | `app/auth/providers/password.py` |
| Trusted networks provider | `app/auth/providers/trusted_network.py`, `tests/test_auth_trusted_network.py` |
| `AUTH_FAIL` log line (one per refused credential; a contract) | `app/auth/fail_log.py`, `docs/dev/auth-fail-log.md`, `tests/test_auth_fail_log.py` |
| Login delays (per pair, per username, per IP; known devices) | `app/auth/ratelimit.py`, `app/auth/device_cookie.py`, `tests/test_auth_ratelimit.py` |
| Home Assistant ingress provider | `app/auth/providers/ingress.py` |
| Installation code, first run | `app/auth/setup_code.py` |
| CSRF rule, https detection, `X-Forwarded-Host` for same-origin | `app/origin_policy.py` |
| Trusted proxies (the one interpreter of `X-Forwarded-For`/`-Proto`) | `app/middleware/proxy_headers.py`, `tests/test_proxy_headers.py` |
| Host guard (`host_allowed`, observe/enforce middleware, observed names) | `app/middleware/host_guard.py`, `tests/test_host_guard.py` |
| Security headers (HSTS over https, no-store, Permissions-Policy) | `app/middleware/security_headers.py`, `tests/test_security_headers.py` |
| Routes | `app/routers/auth.py`, `GET /api/health` in `app/routers/health.py` |
| TOTP second factor (codes, replay rule, recovery codes, pending logins) | `app/auth/totp.py`, routes in `app/routers/auth_totp.py`, `tests/test_auth_totp.py` |
| Passkeys (WebAuthn: RP id rules, ceremonies, counter) | `app/auth/webauthn.py` (py_webauthn), routes in `app/routers/auth_webauthn.py`, `app/migrations/_110_webauthn_credentials.py`, `tests/test_auth_webauthn.py` (py_webauthn's vectors in `tests/fixtures/webauthn_vectors.json`, a software authenticator in `tests/webauthn_authenticator.py`) |
| Recovery CLI (`meshloom-admin`) | `app/cli.py`, `pkg/nfpm/meshloom-admin`, `tests/test_cli.py`, `scripts/test/meshloom_admin_tty.sh` |
| Tests | `tests/test_migrations/test_migration_101.py`, `tests/test_auth_passwords.py`, `tests/test_auth_repository.py`, `tests/test_auth_middleware.py`, `tests/test_auth_setup.py`, `tests/test_auth_sessions.py`, `tests/test_auth_session_hygiene.py`, `tests/test_auth_websocket.py`, `tests/test_auth_password_policy.py`, `tests/test_auth_route_coverage.py`, `tests/test_auth_permissions.py`, `tests/test_auth_ingress.py`, `tests/test_auth_audit.py`, `tests/test_auth_ratelimit.py`, `tests/test_auth_users.py`, `tests/test_auth_totp.py`, `tests/test_migrations/test_migration_109.py`, `tests/test_auth_webauthn.py`, `tests/test_migrations/test_migration_110.py`, `tests/test_migrations/test_migration_136.py`, `tests/test_cli.py` |

Column lists: `docs/dev/database.md`, sections `users`, `sessions`, `auth_audit`
and `auth_settings`.

## Credentials at a glance

The gate (`app/auth/middleware.py`) tries one credential per request; the first
present decides. Details are in the sections named in the last column.

| Credential | Principal | Where it works | Notes | Section |
|---|---|---|---|---|
| Bearer token `mlt_…` | the token's account, narrowed by its scopes | HTTP and WebSocket, direct only (never ingress) | never from a query string or cookie; exempt from the CSRF rule once resolved | "API and device tokens" |
| Session cookie | the session's account | HTTP and WebSocket, never under ingress | `__Host-meshloom_session` (https) or `meshloom_session`; sliding 30 days, or a browser-session cookie | "Sessions and the cookie" |
| Ingress headers | the Home Assistant user (`provider = ingress`) | add-on only, peer `172.30.32.2` | no password, no cookie, no 2FA | "Ingress" |
| Trusted network rule | the rule's account | direct peer with a local address only, while `lan_login_free` is on; tried last | closed behind any proxy; strict `Host` | "Trusted networks" |
| Kiosk device token | the system `kiosk` account | `POST /api/auth/kiosk-bootstrap` from loopback only | opens a one-time session code | "Kiosk device token" |

Second factors (TOTP, passkeys) add a step to the password login and are
described in their own sections. Roles and what each may call are in
"Permissions"; the cookie, the CSRF rule and the Host guard are in "The gate",
"Sessions and the cookie" and "Host guard". The user-facing version of all of
this is `docs/user/{en,fr}/deep/access.md` (same headings in both languages: nothing but review keeps
them aligned, so check both when you edit one).

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
  lifetime comes from `auth_settings.session_lifetime_days` (30 by default), capped
  at 12 hours for a session created without `remember`.
  Revoking sessions on a role change or a disable is the caller's decision;
  server code revokes through `app/auth/sessions.py` (below), never the
  repository directly, so the sockets close too.
- **Account writes.** Every write to a `users` row moves `updated_at` to
  `MAX(now, updated_at + 1)`: never backwards, and at least one second on, so
  two writes in one second still change the known-device generation ("Login
  delays"). `update(..., keep_an_admin=True)` and `delete(..., keep_an_admin=True)`
  count the enabled admins inside the same transaction and raise
  `LastAdminError` (rolled back) when none would be left; the users API always
  passes it, `meshloom-admin disable-user --force` does not. `update(...,
  clear_password=True)` removes the password (a role of `kiosk`).
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
  server), `actor_token_id` (the API or device token that acted), `ip` (`client_ip()`), `via`
  (`lan`, `ingress`, `trusted`, `relay`, plus `cli` for `meshloom-admin` and
  `system` for the server on its own), `outcome` (`success`, `failure`,
  `refused` for a 403, a CLI precondition or a login refused per IP, `delayed`
  for a login refused while a delay runs ("Login delays"), `error`), `target` (`user:<id>` for a known account, `username:unknown` for a
  typed name that matches no account (`typed_username_target()`: the typed text
  is never stored, so a password typed into the username field never reaches
  the log), `session:<id>`, `token:<id>`, `bot:<id>` (old rows only),
  `restore_job:<id>`, `auth_settings.<field>`).
- **Actions** (`AuditAction`, a `Literal`, so pyright refuses a typo):

  | Action | Written by |
  |---|---|
  | `setup` | `POST /auth/setup` (`failure` + `details.reason` for a wrong code, `error` when first-run preparation failed) |
  | `login`, `logout` | `POST /auth/login` (`failure`, target the matching `user:<id>` or `username:unknown`; `pending` with `details.factor = totp` when the password checked and a second factor is owed), `POST /auth/login/2fa` (`success` with `details.factor` `totp` or `recovery`, plus `remaining` recovery codes for the latter; `failure` with `details.reason = second_factor_invalid`; `delayed`/`refused` while a login delay runs), `POST /auth/logout`, `GET /auth/kiosk-session` (`details.method = "kiosk_token"`, `reason` on failure) |
  | `totp.enrolled` | `POST /auth/2fa/confirm` (`success` with `details.sessions_revoked`; `failure` for a wrong first code), and the password check of `POST /auth/2fa/enrol` (`failure`, `delayed`, `refused`) |
  | `totp.disabled` | `POST /auth/2fa/disable` (password and code checks too), `meshloom-admin disable-2fa` |
  | `totp.recovery_regenerated` | `POST /auth/2fa/recovery` (password and code checks too) |
  | `token.created`, `token.revoked` | `POST /auth/tokens` (`details.scopes`, `expires_at`, `origin`), `DELETE /auth/tokens/{id}` (`details.owner` when an admin revokes someone else's); refusals too, outcome `failure` (`refused` for a 403) with `details = {reason, status}` (`token_scope`, `token_expiry_invalid`, `token_limit`, `token_not_found`, `forbidden`) |
  | `token.used` | A refused use of a token that exists (outcome `failure`, `details.reason` `revoked`, `expired`, `owner_disabled`, `ingress`, `scopes_invalid`, `kiosk_route`), at most once a minute per token; successful uses only move `last_used_at`. A refused `POST /auth/kiosk-bootstrap` (outcome `refused`, or `delayed` past the rate limit, `details.route = "kiosk_bootstrap"`) |
  | `session.revoked` | `DELETE /auth/sessions[/{id}]`, `DELETE /auth/users/{id}/sessions` (`details.scope = all`), `meshloom-admin revoke-sessions` |
  | `user.created` | `POST /auth/users` (`details.role`, `must_change`), ingress first visit, `meshloom-admin create-admin` |
  | `user.changed` | `PATCH /auth/me` display name, a Home Assistant rename, `PATCH /auth/users/{id}` (`details.fields`; `role` and `previous_role` for a role change; `password_removed` when the role became `kiosk`; `sessions_revoked`) |
  | `user.disabled`, `user.enabled` | `PATCH /auth/users/{id}` (`disabled`), `meshloom-admin disable-user` / `enable-user` |
  | `user.deleted` | `DELETE /auth/users/{id}` (`details.role`, `provider`) |
  | `users.listed` | `GET /auth/users` (`details.count`), `meshloom-admin list-users` |
  | `setup_code.issued` | `meshloom-admin` |
  | `password.changed` | `PATCH /auth/me` (`failure` for a wrong current password; `delayed`/`refused` while a login delay runs; `refused` with `details.reason = password_policy` and `policy`; `refused` with `details.reason = forbidden` when a `kiosk` session or a scoped token sends a `new_password`), `POST /auth/users/{id}/password` (`details.reset`, `must_change`, `sessions_revoked`), `meshloom-admin reset-password` |
  | `settings.changed` | `PATCH /auth/settings`, one row per field changed (the `ip_ban_*` fields: one row, target `auth_settings.ip_ban`, `details` holding the changed ones); `meshloom-admin host-guard-observe` and `rotate-device-key` (target `auth_settings.device_cookie_key`, never the key) |
  | `bot.created`, `bot.changed`, `bot.deleted`, `bots.disabled` | No longer written (MSHL-192 removed the bot routes: `type = bot` answers 410 and the until-restart route is gone). Kept in `AuditAction` so rows written by 4.18.x and earlier 5.0 builds still render. The `plugin.*` actions replace them |
  | `plugin.installed`, `plugin.created`, `plugin.enabled`, `plugin.disabled`, `plugin.config_changed`, `plugin.changed`, `plugin.updated`, `plugin.removed` | `/api/plugins` (`app/routers/plugins.py`): install of a bundled, custom or store plugin (`details.reattached_data` when it took back the data a keep-data uninstall left, MSHL-366; and `POST /{id}/reinstall`, `details.reinstall`), a local plugin created, a PATCH (one row per kind of change: the toggle, `config_changed` for `config`, `scope`, `granted_permissions`, `quotas`, `hooks`, `changed` for the other fields), `PUT /{id}/source` (`updated`), `DELETE /{id}` (`details.keep_data`); target `plugin:<instance_id>`, `details = {plugin_id, kind, fields?}` (plus `source_bytes` for `updated`): field names only, never a value, a secret or the source; outcome `failure` (`refused` for a 403 raised in the route) when the write is refused. A non-admin caller is refused by the permission gate before the route and writes no row |
  | `plugins.paused`, `plugins.resumed` | `PUT /plugins/pause` when the state changes (an idempotent repeat writes nothing): the actor only, no target, no details |
  | `store.repository_added`, `store.repository_removed` | `POST /store/repositories`, `DELETE /store/repositories/{owner}/{name}` (`app/routers/store.py`, MSHL-196): target `repository:<owner/name>` (lowercase; none when the name is not a valid `owner/name`), `details.plugin_id` on a successful add, `details.code` when the add is refused (`store_offline`, `repository_no_release`, ...); outcome `failure` for a refused add or an unknown repository |
  | `backup.restored` | `POST /settings/backup/restore`: the v1 merge or a refusal in the route, the job's end otherwise (`details.steps`) |
  | `private_key.imported`, `factory_reset` | `PUT /radio/private-key`, `POST /radio/factory-reset` (`details.identity`: the first 12 hex of the public key) |
  | `notify.channel_changed` | `/api/notifications/channels` (`app/notifications/api_support.py`, `announce_channel_change`): a creation, a change of destination (public settings, a URL secret, `allow_private_network`; not a credential or a rename), an enable or disable, a deletion; target `notification_channel:<id>`, `details = {action, type, name}`, never a setting or a secret |
  | `notify.rules_changed` | `PATCH /notifications/rules`, `PUT /notifications/rules/conversations/{key}`: target `notification_rules`, `details = {fields, events?, paused_until?}` (the global pause is `paused_until`); a refused change writes nothing |
  | `share.enabled`, `share.disabled` | `PATCH /settings` when `share_enabled` changes (an unchanged value or another field writes nothing); `details.terms_version` on enable; no target (MSHL-248) |
  | `share.object_deleted`, `share.report_sent` | `DELETE /share/{id}`, `POST /share/{id}/report` (`app/routers/share.py`): target `share:<id>`; `details.reason` for a report (never its free-text details); outcome `refused` when a check refuses (403, e.g. `share_not_owner`, `share_disabled`), `error` for a 5xx from the service, `failure` for other statuses. A caller without `send` is refused by the gate and writes no row |
  | `ip.banned` | The built-in ban ("IP ban"), actor `system`, target `ip:<key>`, `details = {failures, route, minutes}` |
  | `ip.unbanned`, `ip.whitelisted`, `ip.unwhitelisted` | `DELETE /auth/bans`, `POST`/`DELETE /auth/ban-whitelist`, `meshloom-admin unban` / `whitelist add` / `whitelist remove`; target `ip:<key or network>` (none for `unban --all`, `details = {scope: all, count}`) |
  | `update.triggered` | `POST /updates/apply` (`trigger: manual`), the auto-update window (`trigger: auto`, `system`; `error` when the helper could not be started) |

  **Every refusal of the users API is written**, under the route's action
  (`users.listed`, `user.created`, `user.changed` or `user.disabled` for a
  PATCH that disables, `user.deleted`, `password.changed`, `session.revoked`),
  with outcome `refused` (`error` for a 5xx), target `user:<id>` from the path,
  and `details = {reason, status}`: `reason` is the error code without `auth_`
  (`last_admin`, `user_not_found`, `username_taken`, `username_invalid`,
  `password_required`, `password_not_applicable`, `password_policy` plus
  `policy`, the policy's reason), or `forbidden` for a non-admin caller (the
  routes declare `_admin(action)`, `require("admin")` plus that row). Never the
  typed name or password. A 422 (body validation) writes no row.

  `pairing.created`, `pairing.revoked` and `pairing.used` are the device
  pairing rows (`docs/dev/device-pairing.md`, "Security rules").
  Reserved for the tickets that add their feature:
  `passkey.added`, `passkey.removed`, `remote.linked`,
  `remote.enabled`, `remote.disabled` (Remote). A new sensitive action adds its
  name there and writes its row through this module.
- **Reads** of the log (`GET /auth/audit`) are not audited (cadrage §4.5 asks only for an
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

The accounts travel in the `users` section of the full backup
(`app/services/backup_accounts.py`, `docs/dev/backup.md`, "Accounts"): hashes
and TOTP material included (the secret, the replay counter and, since section
version 2, the recovery code hashes; since version 3 the passkeys, public keys
and counters), behind the export's **Include accounts** box
(checked by default); a restore never leaves the instance without an enabled
admin. The API tokens travel in their own `api_tokens` section (hashes,
scopes, expiry; never a secret, the database holds none), behind the same box
and only with `users`, in the same transaction (`restore_with`): accounts kept
by `accounts_no_admin` keep their tokens and the file's are not restored. `auth_settings` is not in any JSON backup
(machine-specific). `sessions` and
the setup code are never exported; `auth_audit` is not in the JSON backup but
stays in the SQLite download (IPs, usernames). The SQLite download
(`GET /settings/backup/database`) keeps the accounts and the audit trail but
not their secrets: `prepare_sqlite_snapshot()` sets `users.password_hash`,
`users.totp_secret` and `auth_settings.setup_code_hash` to NULL and empties
`sessions` and `recovery_codes` in the copy, under `secure_delete`. A restored snapshot therefore
has accounts that cannot log in with a password until an admin reset. The
passkeys (`webauthn_credentials`) stay in the copy: they are public keys, and
they only work on an instance that answers on the same RP id.

## The gate

`AuthMiddleware` (`app/auth/middleware.py`) is the innermost middleware of
`app/main.py` (inside CORS, so preflights never reach it). For every `http`
and `websocket` scope:

0. **IP ban.** On the `/api` routes, the API docs and their WebSocket
   upgrades, outside ingress, a banned address ("IP ban") is refused before
   any credential is read (no argon2id work): 429 `auth_ip_banned` with
   `Retry-After` and `params.retry_after`, or a refused upgrade. Pages and
   assets are still served, so the login page can say why, but anonymously:
   no credential is resolved for a banned address on any path.
1. **Principal.** It resolves a `Principal` (user, role, `via`, credential,
   session or token id, scopes) and stores it in `scope["state"]["principal"]`;
   routes read it with `get_principal(request)`. Credentials are tried in
   this order, and the first one present decides:
   - `Authorization: Bearer`: API and device tokens ("API and device
     tokens" below). A token that does not resolve leaves the request
     anonymous, with `auth_invalid_credentials` on a protected route. Never
     read from a query string or a cookie.
   - Any other `Authorization` scheme, `Basic` included, is no credential
     (HTTP Basic auth was removed, MSHL-100): the request stays anonymous and
     gets the plain 401, with no argon2id work and no `WWW-Authenticate`.
   - The session cookie, under the name for the request's scheme (below).
     Never read under ingress.

   A login check that finds the argon2id queue full answers 503
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
5. **Forced password change.** While the principal's account has
   `must_change_password` (an admin set the password, or `meshloom-admin
   reset-password --must-change`), every `/api` route outside
   `PASSWORD_CHANGE_ROUTES` (`GET /api/auth/session`, `POST /api/auth/logout`,
   `GET` and `PATCH /api/auth/me`) answers 403 `auth_password_change_required`,
   whatever the role, and a WebSocket upgrade is refused (1008). `GET
   /auth/session` says `user.must_change_password`, which the frontend turns
   into the change-password form (MSHL-54); a password change through `PATCH
   /auth/me` clears the flag. Only a local account outside `kiosk` with a
   password can carry it, enforced by the repository: `create`, `update` and
   `set_password_hash` raise `MustChangeNotApplicableError` (nothing written)
   when asked to set it on another account, and drop a flag that a role or
   password change made inapplicable, so no kiosk, Home Assistant or
   password-less account is ever stuck behind this gate. `meshloom-admin
   reset-password` refuses a kiosk account.

Every answer under `/api/auth/` carries `Cache-Control: no-store`. No 401
carries a `WWW-Authenticate` challenge (HTTP Basic auth is removed), so a
browser never shows its native login dialog.

`app/middleware/security_headers.py` (`SecurityHeadersMiddleware`, inside the
proxy-headers middleware so it reads the rewritten scheme) adds three headers.
`Strict-Transport-Security: max-age=31536000` only when the scheme is
`https`/`wss` and `request_via()` is `lan` (never ingress or relay; no
`includeSubDomains`, no `preload`). `Cache-Control: no-store` on `/api/auth/*`
and `/api/remote/*` (a second net: the auth gate already sets it) and on the
explicit `NO_STORE_PATHS` list of routes returning secrets (radio private key,
backups, channels, push subscriptions, debug); a test fails when a typed GET route
with a key/secret/password field is missing from it. The headers also reach an
unhandled 500, written by this middleware before Starlette's. `Permissions-Policy:
camera=(), microphone=(), geolocation=(self)`. The static headers (nosniff,
referrer, framing) stay in `app/main.py`; there is no CSP beyond
`frame-ancestors` (maps, WebGPU and inline styles would break).
`tests/test_security_headers.py`.

Public routes (`PUBLIC_ROUTES`): `GET /api/health`, `GET /api/auth/session`,
`POST /api/auth/setup`, `POST /api/auth/login`, `POST /api/auth/login/2fa`,
`POST /api/auth/webauthn/login/options`, `POST /api/auth/webauthn/login/verify`,
`POST /api/auth/logout`, `GET /api/auth/kiosk-session` ("Kiosk device token").

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
was not relayed; `lan` otherwise. A `lan` request that a trusted network rule
signs in becomes `trusted` once resolved (`scope["auth_via"]`, the principal,
the audit rows). Under
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
  (cadrage R3: a local admin may already exist, made with `meshloom-admin` or
  by the first-run setup on a published port, and the sidebar, the add-on's only way in
  by default, must still reach an administrator); the next ones get
  `auth_settings.ingress_default_role` (`member` by default). "First" is
  decided once: creating that admin sets `auth_settings.ingress_admin_granted`
  in the same transaction as its insert
  (migration 107, backfilled to 1 when an ingress identity already existed),
  never cleared, so deleting the only ingress identity does not hand `admin` to
  the next one (or to the same HA user coming back). This is safe
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
- **Managing them.** `GET /auth/users` lists them with `provider: ingress`
  (never `external_id`); `PATCH /auth/users/{id}` gives any of the four roles,
  `kiosk` included, and disables them (the last-admin rule applies: the first
  ingress identity is often the only admin). A password or
  `must_change_password` is refused (400 `auth_password_not_applicable`). The
  display name comes back from Home Assistant on the next request. A deleted
  ingress identity is created again on its next visit, with
  `ingress_default_role` (`GET/PATCH /auth/settings`): disable it to keep it out.
  The Settings screen is MSHL-47.

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
5. Upgrade a 4.18.x add-on (with and without the former `basic_auth_*`): the sidebar lands
   signed in with no setup step.
6. Publish port 8000: `curl -H 'X-Remote-User-Id: x' http://<ha>:<port>/api/contacts`
   answers 401, and the login page asks for a password there.
7. After `meshloom-admin disable-user <name>` on an ingress account, the
   sidebar shows the Home Assistant sign-in page, with no browser password
   prompt.
8. Live updates through the sidebar, Nabu Casa and the Companion app (the
   WebSocket checks of MSHL-279).

### Trusted networks

`app/auth/providers/trusted_network.py`: Home Assistant's `trusted_networks`,
closed whenever a proxy is in the loop. Rules are `auth_settings.trusted_networks`
(`[{cidr, user_id}]`, migration 101, empty by default). They apply only while
the master switch `auth_settings.lan_login_free` ("Login-free on the local
network", migration 136) is on. It is off by default and stays off on upgrade,
even with rules: migration 136 adds it off and logs a warning when rules exist.
Only an admin changes it (`PATCH /api/auth/settings`), which writes a
`settings.changed` audit row with target `auth_settings.lan_login_free`. The
settings show a FR and EN warning: a box that forwards a port with SNAT makes
internet visitors look local, the CGNAT range `100.64.0.0/10` includes
Tailscale and some VPNs, and a relayed request never counts as local. A
"direct exposure" mode is deferred (ROADMAP §4). **Role choice:** no anonymous
or new role. Each rule names an account the admin picks, and the
recommendation is the least privilege that works (a `viewer` or `kiosk`
account), and **never an admin** (enforced, see below). The rules are kept in
memory with
the `public_urls` the `Host` check needs (read on first use, replaced by
`AuthSettingsRepository.update()`). Every check before the account lookup is
synchronous and in memory (peer, rule match, `rule_blocker`), so a request from
an unlisted or proxied peer costs no database read. The gate asks the
provider **last**: only when the request sent no `Authorization` header and no
session cookie resolved (an expired cookie still gets the rule; a request with
any `Authorization` header, a wrong Bearer token or a `Basic` header, never
does: `resolve_trusted_user` checks it itself, since the kiosk reuses
`rule_blocker`). The principal is `via = "trusted"`, credential
`trusted_network`, which is ambient: unsafe methods keep the Origin rule.

A rule never applies (`rule_blocker`, fail closed) when:

- `via` is not `lan` (relay, ingress) or anything tagged `scope["state"]["via"]`
  (the gate does not even call `resolve_trusted_user` for a relayed request,
  `middleware._resolve_trusted_network`: the relay's local end is loopback or
  a LAN address, MSHL-431);
- the TCP peer (`peer_address`, before the trusted-proxy rewrite) is a trusted
  proxy, so a proxied request is never auto-signed in, whatever it forwards;
- the request carries a header a proxy or tunnel adds (`PROXY_HEADERS`:
  `X-Forwarded-For`, `-Proto`, `-Host`, `-Port`, `-Server`, `Forwarded`,
  `X-Real-IP`, `CF-Connecting-IP`, `CF-Ray`, `True-Client-IP`, `X-Client-IP`,
  `X-Cluster-Client-IP`, `Fastly-Client-IP`, `X-Original-Forwarded-For`, `Via`),
  from any peer: a proxy nobody declared closes the rule;
- the peer is loopback (IPv4-mapped forms included) and the install is not the
  add-on (`LOOPBACK_ALLOWED_KINDS`): on a package, a source checkout or a container
  that may share the host's network, a local proxy or `ssh -L` (no header at all)
  brings every visitor from `127.0.0.1`;
- the peer is not a local address (`ip_ban.is_local`: `app/net/local_address.py`,
  i.e. RFC 1918, CGNAT `100.64.0.0/10`, loopback, link-local, ULA, plus the
  host's on-link global IPv6 prefixes), the same definition as the ban,
  `AUTH_FAIL` and the login delays use (a documentation or reserved address is
  public). A rule covering a public range never signs in a public peer (`not_local`).
  A box that port-forwards with SNAT still presents internet visitors with a
  local address: the settings warn about it;
- `Host` fails the strict guard (`host_allowed`): accepted are an IP literal,
  `localhost`, `*.local`, `*.home.arpa`, `*.lan`, `*.internal`, the machine's
  short name and FQDN, and the hosts of `auth_settings.public_urls`. This is the
  cadrage's list, enforced for this provider from day one, whatever
  `host_guard_mode` says: DNS rebinding would otherwise hand a principal to a
  page that holds no cookie at all. The rule is `host_allowed` in
  `app/middleware/host_guard.py`, shared with the general guard below.

The account must exist, be enabled, have no pending password change (a
rule naming such an account is refused at save time, so it never sits there
doing nothing) and not be an admin: `resolve_trusted_user` returns None for an
admin, which also covers an account promoted after its rule was saved and a
rule restored from a backup. The
longest matching prefix wins, then the first listed. Every auto-login writes one
`login` row (`via = trusted`, `details = {method: "trusted_network", cidr}`) at
most once per client address, user and UTC day (in memory, `MAX_AUDITED`
entries).

`PATCH /api/auth/settings` validates the rules (`normalize_cidr`: not wider than
`/8`/`/16`, no IPv4-mapped form, no duplicate network, at most 32, an existing
enabled account) and refuses, with coded 400s, a rule that overlaps a trusted
proxy (checked both ways: a new proxy overlapping a saved rule is refused too),
one whose account is missing or disabled (`auth_trusted_network_user_invalid`) or
must change its password (`auth_trusted_network_user_must_change`) or is an
admin (`auth_trusted_network_user_admin`, `params.cidr`),
one covering loopback where loopback is refused, and one covering the Docker
default gateway (`docker_gateways()`, from `/proc/net/route` on a container
install), always: behind docker-proxy every client of the published port, the
internet included, arrives from the gateway. Turning `lan_login_free` on while
a saved rule (older, or restored) names an admin is refused with 409
`auth_lan_login_free_admin_rules` (`params.cidrs`, comma-separated); the UI
never offers an admin in the account picker and lists such rules. A
`PATCH` that carries neither `trusted_networks` nor `lan_login_free: true` is
not blocked by such a rule (the runtime check above keeps it inert). The
accounts restore applies the remapped rules with `lan_login_free=False` until
`_reload_access_state()` reloads the stored settings. `GET` adds `security_notices`, the Access
security notice the UI shows at the top of Users & Access:
`trusted_admin_reduced_plugin_isolation` (a rule maps to an enabled admin while
plugins run with isolation `none`, the global pause included since the risk is
back on resume; an unreadable state counts as reduced) and
`trusted_loopback_or_gateway_public` (a rule covers loopback or the gateway
while `public_urls` is set) and `trusted_network_public_or_wide`
(`public_or_wide`: a rule is as wide as allowed, `/8` IPv4 or `/16` IPv6, or not
inside one local range (`local_address.network_is_local`: a documentation or
reserved range is flagged too), or inside the shared address space `100.64.0.0/10` (Tailscale,
CGNAT) and wider than `/32`; accepted, since Home Assistant allows it too, but
almost always a mistake; the UI asks for the same confirmation as for a wide
rule, `frontend/src/lib/cidr.ts` mirrors it). The
Remote conditions come with Remote.

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
No 401 carries a `WWW-Authenticate` challenge, under ingress or elsewhere
(the browser would prompt, inside the Home Assistant panel too).

## Permissions

`app/auth/permissions.py`. Five permissions, granted by the four roles
(cadrage §4.2, owner decision Q-A7):

| Role | Permissions | In practice |
|---|---|---|
| `kiosk` | `read` | Channel messages (muted and pending channels included), contacts, channels, packets (control packets included), stats, map, health. Never a direct message; no settings, no profile, no sessions |
| `viewer` | `read`, `read_private` | `kiosk` plus direct messages and marking read; its own profile and sessions |
| `member` | `viewer` + `send` | Send, create and edit contacts, channels and contact groups, favourites, pins, mutes, its own push subscriptions (owner `user_id`, MSHL-290) and its own notification choices, conversation exceptions and pause (`/api/push/my-rules`, `/api/push/my-pause`, MSHL-321, 362, 363), trace and path discovery |
| `admin` | all, `repeater_admin` included | Settings, radio, repeaters and rooms, fanout and plugins, backup and restore, private key, updates, Community, push preferences, every account's push devices (`GET /api/push/subscriptions?scope=all` redacted to the origin, DELETE; never PATCH or test), debug |

**Sharing (`/api/share`, MSHL-248).** Shortening, uploading, *My uploads*, deleting
and reporting are `send` (member, admin); `/share/cache/clear` and the share settings
are `admin`; status and object reads are `read`. An object read tied to a non-channel
message (`message_id`) needs `read_private`: a `kiosk` gets 403 `share_role_forbidden`
before any cache read or request to the service.

`GET /settings/view` (`read`, so every role including `kiosk`) is the one readable
settings route: `AppSettingsView` in `app/models.py` is an allowlist of display
settings (`ui_preferences`, blocked keys and names, regions, unread markers,
`directory_available`), and only `channel-` keys of `last_message_times` are kept
below `read_private`. `GET /settings` stays `admin`. A setting reaches the other
roles only by being added to that model (`tests/test_settings_view.py`).

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
non-API path missing from `NON_API_ACCESS`, or an emit call
(`broadcast_event`, the injected `broadcast_fn`, `ws_manager.broadcast*`,
`send_personal`) in `app/` that passes anything but a `WsEvent` member.

**Refusal.** 403 `{"detail": {"code": "auth_forbidden", "message", "params":
{"permission"}}}` (`permission` is `account` for `own_account()`), translated
by the frontend (`errors.auth_forbidden`, slice `x`). A refused WebSocket
upgrade is closed with 1008 before `accept()`. The permission check runs before
body validation, so a refused caller never gets a 422.

**Direct messages (`read_private`).** Without it:

- `GET /messages` and `GET /messages/around/{id}` answer `CHAN` rows only
  (search `q` and `since_id` included); `type=PRIV`, any other type, or a
  `conversation_key`, or a `conversation_keys` value, longer than a channel key
  (32 characters: a contact key) answers 403.
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

**WebSocket events.** `WsEvent` (`app/auth/ws_events.py`) is the one
enumeration of event types: each member declares the permission needed to
receive it, `app/events.py` has one payload model per member, and
`frontend/src/wsEventTypes.ts` is generated from it
(`scripts/build/export_ws_events.py`, checked by `tests/test_ws_event_export.py`;
`wsEvents.ts` fails `tsc` when its union drifts). A type that is not a member
reaches no client, admins included (fail closed, logged once). `ws_manager` keeps the
principal of each socket and delivers an event only to the clients holding its
permission and the event's `audience` (a permission the caller may add;
`app/websocket.py`). `radio_wifi`, `plugin_status` and `notification_channel_changed` are `admin`; the other
types are `read`. Per event, a client without `read_private` gets:

- no `message` that is not `CHAN` (no `type` counts as private);
- a `raw_packet` decrypted from a DM without its decryption;
- `contact` and `contact_resolved` with `last_contacted` and `last_read_at`
  emptied (`CONTACT_DM_ACTIVITY_FIELDS`, the same fields REST empties);
- no `message_acked` unless the ack is for a channel message (the caller
  passes `audience="read"`; a DM ack or an unknown one stays private);
- no `notification_event` (MSHL-138) about a DM: `message.dm`, a
  `conversation_type` other than `CHAN` with a conversation key, a `message.*`
  without type `CHAN`, or a `message.mention` whose `conversation_open` is not
  `true` (a channel with its own key). A `notify.*` or unknown event id in that
  frame is `admin` only.

`health` goes to every `read` client, but below `admin` every `last_error`
(fanout, notification media, radio proxy, plugins) is `null` and the plugins'
`send_guard` is empty; the statuses stay (`without_admin_details`,
`HEALTH_DETAIL_PERMISSION` in `app/routers/health.py`, also on the REST
route). Toasts (`error`, `success`) are `admin` by default: they carry hosts,
ports and radio errors. A caller lowers it only for a text that reveals none
of that (`audience="send"` for a member's send failure, `"read"` for a channel
decrypt run, `"read_private"` for a DM decrypt run).

**4.x upgrade.** The first administrator is `admin` (and an account an earlier
5.x imported from the retired Basic variables stays one): a single-admin
installation sees no change.

The frontend mirrors the role table in `frontend/src/auth/capabilities.ts` and hides or disables what a role cannot use (`useCapabilities()`, `frontend/AGENTS.md`, "Capability gating"). It is a courtesy, not a control: the declarations above are the only enforcement.

## Sessions and the cookie

- A login or a setup creates a new session row (never reuses one: no
  fixation) holding the SHA-256 of a 256-bit secret; the secret goes only in
  the cookie. Lifetime: `auth_settings.session_lifetime_days` (30 by default; an admin changes it, 1 to 365,
  through `PATCH /auth/settings` and Settings → Users & Access → Session lifetime, MSHL-318). New
  sessions use the new value at once; an existing one keeps its stored `expires_at` until its next
  slide (`touch()`, at most once a minute while used), which recomputes it from the new value, so a
  shorter setting ends an idle session at the old expiry at the latest and a used one after the
  new lifetime. Nothing rewrites rows in bulk.
- **Remember me.** `POST /auth/login` and `POST /auth/webauthn/login/verify` take
  `remember` (default `false`; the TOTP step reuses the value of its pending login,
  `PendingLogin.remember`). It is stored in `sessions.remember` (migration 117; 1 for
  every older row). `remember=true`: the lifetime above and a cookie with `Max-Age`.
  `remember=false`: a browser-session cookie (no `Max-Age`, no `Expires`) and a
  server-side sliding lifetime of `min(session_lifetime_days, 12 h)`
  (`sessions.BROWSER_SESSION_LIFETIME_S`), so an abandoned session ends within that
  time. It still slides while used and has no absolute cap: a stolen cookie that
  keeps being used stays valid. Setup and the kiosk sign-in stay long
  (`_start_session` and `issue_session` default to `remember=True`).
- **Sliding expiry.** A request whose session was last seen more than a
  minute ago moves `expires_at` to now + the lifetime of its kind (`touch()`, one
  write a minute at most) and, for a remembered session, gets the cookie again with
  the same `Max-Age`; a browser-session cookie has none to send again.
- **Cookie.** `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age` = lifetime (absent
  without "remember me").
  Over https (`scope["scheme"]`, set from `X-Forwarded-Proto` only for a trusted
  proxy, see "Proxy headers") its name is
  `__Host-meshloom_session` with `Secure`; over http `meshloom_session`
  without `Secure` (browsers refuse a `Secure` cookie from an insecure origin).
  Only the name for the request's scheme is read, so a cookie planted over
  http is ignored on https.
- A disabled account, a revoked or expired session: anonymous at once.
- Logout revokes the session and expires exactly Meshloom's cookies by name,
  host-only (`Max-Age=0`, `Path=/`, no `Domain`): the session cookie and the
  known-device cookie under the names for the request's scheme and, over https,
  the plain names too (a visit over http may have left them; a browser refuses
  any `Secure` cookie from http, so over http only the plain names are sent).
  Never `Clear-Site-Data: "cookies"`: browsers apply it to the whole registrable
  domain, so it would sign the browser out of every other
  `<slug>.meshloom.link` instance (not on the Public Suffix List) and wipe the
  cookies of other apps on a self-hoster's domain. Under ingress, no cookie at all.
- **Throttled last-seen.** The middleware calls `touch()` only when the
  session it read was last seen a minute ago or more, and `touch()` itself
  writes only under the same condition, so a burst of requests costs one write
  a minute per session, not one per request. WebSocket frames (the frontend's
  30 s `ping`) slide the session too (`ws_manager.note_activity()` →
  `sessions.slide_session()`, at most one attempt a minute per socket), so a
  page that only keeps its socket open, such as a kiosk display, is not signed
  out at expiry; no cookie goes out on that path, the next HTTP request sends
  it again. There is no absolute lifetime: only the sliding lifetime
  (`session_lifetime_days`, or 12 h at most without "remember me").
- **Revocation closes the WebSockets.** `ws_manager` (`app/websocket.py`)
  indexes every socket by the session id and token id of its principal.
  `sessions.revoke_session()` (logout, `DELETE /auth/sessions/{id}`),
  `sessions.revoke_user_sessions()` (`DELETE /auth/sessions`, a password
  change) and the cap in `sessions.issue_session()` revoke the rows, then call
  `ws_manager.close_sessions()`: the sockets close at once with code 1008
  (`REVOKED_CLOSE_CODE`). `ws_manager.close_tokens()` does the same for API and
  device tokens (`providers/token.revoke_token()`). While a session or token
  socket is open, the manager also rechecks every
  `SESSION_RECHECK_INTERVAL_SECONDS` (30 s) which of its sessions and tokens are
  still live (`AuthSessionRepository.filter_live()`,
  `ApiTokenRepository.filter_live()`, one query each) and closes the others: that catches `meshloom-admin` (another process, writing the database),
  expiries and disabled accounts. Sockets with no session (ingress, a trusted
  network rule) are not rechecked. A socket keeps the role it opened with (the recheck checks
  liveness, not the role), so a change of the account itself (role, disable,
  deletion, a password set by an admin or by the user) goes through
  `sessions.sign_out_user()`: `revoke_user_sessions()` plus
  `ws_manager.close_user()`, which closes every socket of that user whatever its
  credential (ingress and trusted-network sockets have no session), except those of the
  session kept.
- **Purge.** `sessions.purge_sessions()` runs in the hourly housekeeping loop
  (`app/services/stale_contacts.py`) and deletes expired and revoked rows.

## API and device tokens

One token per device or script (meshenger, a Home Assistant sensor, the
kiosk screen), revocable alone, attached to a local account (cadrage O4, ADR
001 E1). Code: `app/auth/providers/token.py` (rules in its header),
`app/routers/auth_tokens.py` (routes above), `ApiTokenRepository`
(`app/repository/auth.py`), table `api_tokens` (migration 108,
`docs/dev/database.md`).

- **Secret.** `mlt_` + 256 random bits (`secrets.token_urlsafe(32)`), shown
  once in the `POST /auth/tokens` answer (a `JSONResponse`; the models hold it
  as `SecretStr`). Only its SHA-256 is stored; the lookup goes by that hash and
  the stored one is compared with `hmac.compare_digest`. A value without the
  prefix or over 128 characters is refused before any query. No JWT: a
  revocation needs a table anyway.
- **Header only.** `Authorization: Bearer <token>` on HTTP and on the
  `/api/ws` upgrade (the gate reads the upgrade's headers before `accept()`).
  A token in a query string (`?token=`, `?access_token=`) or a cookie is never
  read: the request stays anonymous. A resolved bearer token is exempt from the
  CSRF rule (above).
- **Scopes.** A token holds one to five of the permissions (`read`,
  `read_private`, `send`, `repeater_admin`, `admin`). At creation every scope
  must be granted by the caller's role (`scope_violation()`; a member asking for
  `repeater_admin` gets 400 `auth_token_scope`). At each use the principal
  carries the owner's **current** role and the scopes, and `permissions_of()`
  intersects them: a token never exceeds its owner's role, even after a
  demotion, and `admin` in both still implies every permission. A stored scope
  list with nothing valid left refuses the token (an empty list would mean the
  full role). `own_account()` refuses any scoped principal, so a token cannot
  reach the profile, the sessions or the token routes themselves.
- **WebSocket events.** A token socket gets the events its permissions allow,
  like any other principal (`WsEvent`, "Permissions"): a `read` token gets no
  direct message and no `admin` frame.
- **Who holds tokens.** `member` and `admin` (`TOKEN_ROLES`, cadrage §4.2);
  `viewer` and `kiosk` get 403 `auth_forbidden` on the routes. The kiosk's
  token is written by the server on the kiosk host, never through the API
  ("Kiosk device token").
- **Refusals.** Unknown, revoked, expired, an owner disabled or deleted (its
  rows cascade), a request through Home Assistant ingress (the Supervisor
  serves one shared origin and signs the user in itself; a device talks to the
  instance directly or through the relay): anonymous, 401
  `auth_invalid_credentials`, or a WebSocket closed 1008 before `accept()`. So
  is the kiosk's device token anywhere but its bootstrap route ("Kiosk device
  token"). A refusal of a row that exists writes `token.used` (`failure`), at
  most once a minute per token; an unknown value writes nothing.
- **Expiry and last use.** `expires_at` is optional (null: never) and fixed at
  creation. `last_used_at` is written at most once a minute per token, never
  for a revoked one.
- **Revocation.** `DELETE /auth/tokens/{id}` (`revoke_token()`) revokes the row
  and closes its sockets at once (`ws_manager.close_tokens()`, 1008); the next
  request with it is a 401. A token socket also closes at the token's
  `expires_at` (a timer armed at connect, `Principal.token_expires_at`). The
  periodic recheck ("Sessions and the cookie") closes the sockets of a token
  revoked from another process or whose owner was disabled. A role change, a disable or a deletion of the
  owner goes through `sign_out_user()`, which closes its token sockets too.
  Revoked and expired rows are kept (a later use is audited); at most 100 live
  tokens per user.
- **OpenAPI.** `/openapi.json` declares `bearerToken` (HTTP bearer) and
  `sessionCookie` as security schemes, required everywhere but `PUBLIC_ROUTES`
  (`app/api_docs.py`).
- Tests: `tests/test_auth_tokens.py` (HTTP and WebSocket handshakes with the
  header, scopes, revocation, expiry, ingress, query strings, audit),
  `tests/test_backup_accounts.py` (the `api_tokens` section).

```bash
curl -b jar -H 'Content-Type: application/json' \
  -d '{"name":"ha-sensor","scopes":["read"]}' http://HOST:8000/api/auth/tokens
curl -H "Authorization: Bearer mlt_..." http://HOST:8000/api/channels
```

## HTTP Basic auth (removed)

Up to 4.18 `MESHCORE_BASIC_AUTH_USERNAME` / `_PASSWORD` were the only gate;
5.0 previews imported them as an admin and accepted `Authorization: Basic`.
HTTP Basic auth is removed (MSHL-100):

- The variables are never read: each start logs the retired-variable warning
  while they are set (`RETIRED_ENV`). A 4.x install that had only them goes
  through the first run with the installation code. An account an earlier
  preview imported stays a normal account.
- A `Basic` header is no credential: the gate ignores it like any unknown
  scheme, so the request gets the plain 401 with no argon2id work, no
  `WWW-Authenticate` challenge and no `AUTH_FAIL` line. A valid session cookie
  next to it still signs the request in. A script uses an API token.
- The add-on options `basic_auth_username` / `basic_auth_password` are gone
  from `meshloom/config.yaml` and `meshloom/run.py`.
- Migration 136 drops `auth_settings.basic_imported`.

**Password check load cap** (`app/auth/providers/password.py`). At most 2
argon2id checks run and 8 wait; one more answers 503 `auth_busy` at once
instead of queueing behind a flood. On the login route the login delays run
before it ("Login delays").

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
  is its IPv4). **None**, so the pair and per-IP rules are skipped and only the
  username rule applies, only when the configuration says clients share one
  address: `install_kind` `compose` or `container` with
  `auth_settings.trusted_proxies` empty and
  `auth_settings.addresses_identify_clients` off (both read live, from
  `proxy_headers`). The owner turns that setting on (Users & Access, "Client
  addresses", MSHL-424) when the peer address is really the client's own (host
  network, or a port published without Docker's userland proxy); the panel
  suggests it when `GET /auth/settings` reports `shared_address_install` and
  `client_public` (`ratelimit.client_is_public`: the address is not local,
  `app/net/local_address.py`, documentation and reserved ranges included, or
  it is unparseable) with no trusted proxy. Request headers never
  decide it: `X-Forwarded-For`, `X-Real-IP` or `Forwarded` from a peer that is
  not a trusted proxy is ignored (the peer is the client, as everywhere) and
  logs a warning once per peer pointing to Trusted proxies, next to the login
  page's untrusted-proxy hint (#210). A LAN client adding the header gains
  nothing (review r2 of #217, tested).
- **Known devices** (`app/auth/device_cookie.py`, OWASP "device cookie"). A
  successful password login sets `meshloom_device` (`__Host-meshloom_device`
  with `Secure` over https): `v1.<user id>.<issued at>.<device id>.<generation>.<HMAC-SHA256>`,
  `HttpOnly`, `SameSite=Strict`, `Path=/`, no `Domain`, 180 days. The
  `__Host-` prefix forbids a `Domain`, so a sibling host on the same
  registrable domain cannot toss a cookie of that name that Starlette's
  parser (last duplicate wins) would read instead; only the name for the
  request's scheme is read. Logout expires it with the session cookie. `generation` is
  the account's `users.updated_at`, moved by every write to the account
  (password change from the route or the CLI, rehash, role change, disable, enable, rename),
  so each invalidates the user's cookies; nothing derived from the password
  goes in the cookie (CodeQL flags a hash of the password hash, rightly). Every
  write moves it at least one second ("Account writes" above), so a change in
  the same second as the previous write moves it too. The account must be enabled at check time. A login for that username with a valid cookie skips
  all three rules while its device id has fewer than `DEVICE_MAX_FAILURES` (5)
  failures; the count is dropped 15 minutes after the device's last failure
  (not a sliding 15-minute window: failures 14 minutes apart keep adding up)
  or at its next successful login, and past 5 it follows the ordinary rules. The HMAC key is 32 random
  bytes in `auth_settings.device_cookie_key` (migration 119), created on first
  use and read from the database at each use (no cache), so cookies survive a
  restart and `meshloom-admin rotate-device-key` (or a restored SQLite
  download, where the column is cleared) forgets every browser at once. It is
  never in the JSON backup or `GET /auth/settings`. Without the
  `auth_settings` row a presented cookie is unknown (fail closed). One cookie per
  browser, for the last user who signed in on it. Never under ingress.
- **Guarantees.** A known browser of the account always gets in with the
  right password, whatever a guesser does from any address. An unknown
  browser: on a per-address deployment (package, source, add-on port, a
  declared trusted proxy) it meets only the one-second username slot unless it
  shares the guesser's address (then the pair and per-IP rules apply to both);
  on a shared address (Docker without a trusted proxy) it competes with every
  guesser for one attempt a second, so a fast guesser can keep it out; behind
  an undeclared reverse proxy every visitor is the proxy's address, so the
  pair and per-IP rules apply to all of them together until it is listed.
- **Memory.** Pair, username and device entries go `FORGET_AFTER_S` (15 min) after
  their last failure once no delay runs; an IP bucket goes once it has leaked
  empty. A sweep runs at most once a minute from `check()`; each table holds
  at most 4096 keys (the least recently failed goes). Restarting empties it.
- **Audit.** One `login` row per delay, not per refused attempt: outcome
  `delayed` (`details.reason` `pair` or `username`) or `refused` (`ip`), with
  `details.retry_after`; the typed name follows `typed_username_target()`.
- **Clock.** The limiter takes a clock; `_FailureWindow` hands it to
  aiolimiter in place of the loop's time, so the tests use a fake clock.
- **Device pairing.** `POST /auth/pair` counts a bad code as a failure under
  one shared key with `name_rule=False`: the pair and per-IP rules apply to the
  client, the per-username slow-down never does, so one client cannot slow
  every device down. On a shared address no rule applies to it (a 128-bit code
  cannot be guessed; `docs/dev/device-pairing.md`).
- **Not covered.** Concurrent attempts on one pair can each pass `check()`
  before the first failure is recorded (bounded by the argon2id queue of 10).
  Without a known device, under a sustained flood on one name from many
  addresses (or many /64s, or one shared address), the owner competes for the
  one slot a second; the per-IP rule and the relay's edge lock answer that,
  not a lock on the name.
- **`AUTH_FAIL` line.** Each refused credential (login, second factor, passkey,
  Bearer, pairing code, setup code) also writes one `WARNING` line on the
  logger `meshloom.auth`, keyed on `client_ip()`, with `via` the transport
  (`request_via()`), `proxied=1` when a local peer (RFC 1918, CGNAT, loopback, link-local, ULA)
  that is not a declared proxy sends forwarding headers
  (`forwarded_by_untrusted_peer()`, the same test as the untrusted-proxy
  warning, narrowed to local peers: a public peer adding a header is the
  client), and the typed name only as a keyed hash. Attempts refused by a delay write none. Format and routes:
  `docs/dev/auth-fail-log.md`, a contract.

## IP ban

`app/auth/ip_ban.py` (MSHL-422, remote propositions v2 §5.3 and decision 6),
storage `app/repository/ip_ban.py`, migration 135. A persistent ban per
address, on top of the login delays, fed by the `AUTH_FAIL` events
(`docs/dev/auth-fail-log.md`): `fail_log` calls `ip_ban.note_failure()` with the
fields of the line it just wrote, so the ban and an external log watcher judge
the same failures with the same address.

- **What counts.** Only a failure whose line reads `proxied=0`; never a
  local address (`app/net/local_address.py`: loopback, RFC 1918, CGNAT,
  link-local, ULA) nor a missing one; never under ingress. Nor, for the ban only (the
  `AUTH_FAIL` line and its `proxied` rule are unchanged), a global IPv6 address
  inside a prefix on-link on the host: `/proc/net/if_inet6`, scope global,
  prefix length 48 to 128, re-read at most every 60 s (`HOST_PREFIXES_TTL_S`).
  Unreadable (macOS, Windows) it adds nothing; in a Docker bridge network or
  the Home Assistant add-on it sees only the container's interfaces, so a LAN
  client on the household's global /64 counts as public: the user doc tells to
  whitelist that /64. A ban already written for such a prefix stops applying. Through the relay the address is
  `client_ip()` (the client in the frame), never the tunnel's peer, and only
  when it is public: a local or missing one writes `ip=-` and bans nothing,
  and a relayed line is always `proxied=0` (MSHL-431).
- **Key.** IPv4 by address, IPv6 by its `/64` (an IPv4-mapped address is its
  IPv4), like the login delays.
- **Threshold.** `auth_settings.ip_ban_max_failures` (10, 3 to 1000) failures
  within `ip_ban_window_minutes` (10, 1 to 1440) ban the key for
  `ip_ban_duration_minutes` (60, 1 to 43200): a row in `ip_bans` and an
  `ip.banned` audit row. `ip_ban_enabled` (on) turns it off: nothing is counted
  and no ban applies; the rows stay. `GET/PATCH /auth/settings`, Settings →
  Users & Access → Address bans; never an environment variable.
- **Whitelist.** `ip_ban_whitelist` (addresses or CIDR networks, host bits
  cleared, at most 256, each with a note): an address it covers is never
  banned, and a ban on it stops applying. It always wins.
- **Enforcement.** Step 0 of the gate (`check()`), on every path outside
  ingress: refused on the API and the API docs, anonymous elsewhere (no
  `resolve()`, no argon2id). A key with no ban in memory costs one dict
  lookup; a key with one re-reads the whitelist then its row on every request,
  so `meshloom-admin unban` or `whitelist add`, run against the database while
  the service runs, apply on the next request. A whitelisted address is
  cached for 10 s (`WHITELIST_CACHE_S`, at most 4096 addresses): a removal
  through `DELETE /auth/ban-whitelist` clears the cache
  (`whitelist_changed()`), one by `meshloom-admin whitelist remove` applies
  within 10 s. A database
  error while re-reading keeps the ban until its in-memory end (fail closed).
  `load()` at startup reloads the running bans, so a ban survives a restart.
- **Memory.** Failure counts live in memory (at most 4096 keys, the oldest
  goes) and start again empty after a restart; the bans do not. `ip_bans`
  holds at most 10000 rows (`MAX_BANS`; past it, the ones that end first go);
  ended rows are deleted when a new ban is written. The in-memory map of bans
  follows the same rule (`_prune()`).
- **Managing.** `GET /auth/bans`, `DELETE /auth/bans?address=`,
  `POST /auth/ban-whitelist`, `DELETE /auth/ban-whitelist?network=` (admin,
  routes table), the panel, and `meshloom-admin list-bans [--json]`,
  `unban ADDRESS|--all`, `whitelist list|add NETWORK [--note]|remove NETWORK`.
- **Not covered.** No escalation for repeat offenders, no lower thresholds for
  a directly exposed instance yet (the "direct exposure" mode of the Accès
  scoping). fail2ban files ship separately (MSHL-423).
- Tests: `tests/test_ip_ban.py`, `tests/test_cli_ip_ban.py`,
  `tests/test_migrations/test_migration_135.py`.

## Second factor (TOTP)

Access T50, owner decision Q-A16 (optional for users) and lead decision Q16
(no hard rule: `require_2fa_for_admins`, off by default). `app/auth/totp.py`
(pyotp), routes in `app/routers/auth_totp.py`, storage in `users.totp_*` and
`recovery_codes` (migration 109) through `TotpRepository`.

- **Who.** A `local` account with a password, outside `kiosk`. Home Assistant
  identities and the kiosk account have none (403 `auth_2fa_not_applicable`);
  ingress never asks for one (Home Assistant authenticated the user).
- **Enrolment.** `POST /auth/2fa/enrol` checks the current password (login
  delays, argon2id queue: a stolen session cannot enrol a factor that would
  lock the owner out), stores `pyotp.random_base32()` (160 bits) as a pending
  secret (`totp_enabled_at` NULL; enrolling again replaces it) and answers it
  with its `otpauth://` URI (issuer: the radio's name, else `Meshloom`). `POST
  /auth/2fa/confirm` turns it on with the first valid code: its step becomes
  `totp_last_counter`, ten recovery codes are generated and returned once, and
  every other session of the account is signed out. Turning the factor on or
  off moves `updated_at` (known-device cookies, pending logins).
- **Codes.** 6 digits, 30 s, one step either way (`VALID_WINDOW = 1`). A code
  is accepted only for a step after `totp_last_counter`
  (`TotpRepository.claim_counter`, one conditional `UPDATE`): replaying a code,
  or using an earlier one, inside its 90 s window is refused, even by two
  concurrent requests.
- **Recovery codes.** Ten codes of 12 characters (`xxxx-xxxx-xxxx`, an alphabet
  without 0/o/1/l/i, about 59 bits), typed with any case, spaces or dashes.
  Stored as argon2id hashes (PyNaCl) at a light cost (2 passes, 1 MiB): they
  are random, so the hash only makes a stolen table useless, and a check tries
  up to ten. Each works once (`used_at`, conditional `UPDATE`). `POST
  /auth/2fa/recovery` (password and a TOTP code) replaces them; disabling the
  factor deletes them. Disable and recovery check the password with
  `record_success=False`: the login delays are cleared only once the password
  **and** the code checked, so wrong codes keep building the (address, name)
  delay even with the right password.
- **Login.** A correct password for an account with the factor on opens no
  session and does not count as a limiter success: `POST /auth/login` answers a
  pending-login token (256 bits, only its SHA-256 kept, in memory, 5 minutes,
  1024 at most) and writes a `login` row with outcome `pending`. `POST
  /auth/login/2fa` checks the code (TOTP or recovery) under the login delays,
  keyed on the client's address and the account's name like a password: a
  wrong code is `record_failure` (so password and code failures share one
  budget), a right one `record_success`. The token is bound to the first
  step's client (`client_binding`: SHA-256 of `client_ip()`, as the trusted-proxy
  interpreter resolved it, and the User-Agent): another address or browser gets
  401 `auth_2fa_pending_invalid`, the token is discarded and a `login` row
  `refused` (`details.reason = pending_client_mismatch`) is written. While a
  code is checked the entry is taken out of the table (`PendingLogins.take`), so
  two concurrent requests cannot both use one token; it is put back only after a
  check that opened no session (a wrong code counts). Five wrong codes void the
  token; a change to the account (`updated_at`), a disable or a removed factor
  void it too; a restart forgets every token (sign in again). The session and the
  known-device cookie are issued only after the second factor.
- **No other way around it.** A trusted network
  rule still signs its account in without a password or a code: that is the
  admin's explicit choice, as before; the settings warn about rules naming an
  administrator.
- **`require_2fa_for_admins`** (`auth_settings`, `GET/PATCH /auth/settings`).
  When on, an admin with a password and no enabled factor reaches only
  `SECOND_FACTOR_ENROLMENT_ROUTES` (`GET /auth/session`, `POST /auth/logout`,
  `GET /auth/me`, `GET /auth/2fa`, `POST /auth/2fa/enrol`, `POST
  /auth/2fa/confirm`); every other `/api` route answers 403
  `auth_2fa_enrolment_required` and the WebSocket upgrade is refused (the gate,
  `app/auth/middleware.py`), whatever the credential (session, an API
  token of that admin, a trusted network rule). Members, viewers, kiosks and
  ingress identities are not affected. `must_change_password` comes first. An unreadable setting
  counts as on (fail closed). `GET /auth/session` says
  `user.second_factor_enrolment_required`.
- **Recovery on the host.** `meshloom-admin disable-2fa <username>` removes the
  factor (or a pending enrolment) and the recovery codes, and writes a
  `totp.disabled` row (`via = cli`). Sessions stay.
- **Secret at rest.** Base32 in `users.totp_secret` until the instance secrets
  key (Notifications T32, `data/secrets.key`) exists; encrypting it then is a
  follow-up. It is never returned after enrolment, never logged, null in the
  database download, and in the JSON backup only inside the `users` section
  ("Include accounts").

## Passkeys

Access T52, owner decision Q-A16 (optional, HTTPS only). `app/auth/webauthn.py`
on **py_webauthn** (ceremonies, attestation parsing, COSE keys; no CBOR or
signature code here), routes in `app/routers/auth_webauthn.py`, storage in
`webauthn_credentials` (migration 110) through `PasskeyRepository`.

- **Who.** A `local` account with a password, outside `kiosk`
  (`webauthn.applicable`). The account's passkeys are managed from a session only:
  a token, a trusted network rule or ingress gets 403
  `auth_passkey_not_applicable`. A passkey adds a way in next to the password and
  never replaces it, so removing the last one locks nobody out.
- **Relying party** (`relying_party_for`). WebAuthn needs a secure context and
  a domain name. The RP id is the request's `Host` (lowercase, no port) when it
  is: `localhost` (a secure context over http too; origin = the request's scheme
  and port); the host of a declared public address (`auth_settings.public_urls`,
  "Host guard") on an https request (`scope["scheme"]`, so behind a TLS
  terminator only a trusted proxy's `X-Forwarded-Proto` makes it https); or the
  Remote hostname (`host_guard.remote_hostnames()`) through the relay
  (`via = relay`) or over https. The expected origins are the declared addresses
  of that host (`https://host[:port]`) and, for the Remote hostname,
  `https://<host>`. Everything else is refused with 400 `auth_passkey_unsupported`
  and `params.reason`: `ingress` (Home Assistant), `insecure` (plain http
  outside `localhost`), `host` (an IP literal, a `.local` or other undeclared
  name, the relay without a Remote hostname). An unreadable setting refuses too
  (fail closed). `GET /auth/session` says `methods.passkey` exactly when this
  resolves for the request, so it is false on `http://<LAN IP>` and true through
  the relay.
- **Ceremonies** (`Ceremonies`, in memory). Each options call stores a 32-byte
  challenge for 5 minutes (`CEREMONY_TTL_S`) with
  its purpose, RP id, account and binding: the session id for a registration,
  the pending token's SHA-256 and the client (`totp.client_binding`) for a
  second factor, the client for a passwordless sign-in. The verify call finds it
  by the challenge inside `clientDataJSON` and removes it before any check: a
  challenge is used once, right or wrong, and only by the session or client it
  was issued to. A restart forgets them. Each purpose has its own cap
  (`CEREMONY_MAX_ENTRIES`: `login` 1024, `register` 256, `second_factor` 1024),
  so no purpose pushes another's out; a full `login` table (public) refuses a
  new challenge (429) instead of evicting one before its TTL, the two
  authenticated tables drop their own oldest entry.
- **Policy.** Registration: `attestation: none`, resident key preferred, user
  verification preferred, the account's passkeys in `excludeCredentials`, the
  user handle `user_handle()` (SHA-256 of the account id and creation time,
  never the name), at most 20 passkeys (`MAX_PASSKEYS_PER_USER`). Passwordless
  sign-in: empty `allowCredentials` (discoverable credentials), user
  verification **required**. Second factor: the account's passkeys, user
  verification preferred. A `userHandle` in an assertion must be the owner's.
- **Counter.** py_webauthn refuses an assertion whose counter is not greater
  than the stored one when either is non-zero (a cloned authenticator or a
  replay: audited as `passkey_sign_count_not_increased`); the new value is
  stored with a conditional `UPDATE` (`record_use`), so two concurrent uses of
  one assertion cannot both pass. Synced passkeys that always report 0 stay at 0.
- **Re-authentication.** `register/options` and `DELETE /credentials/{id}` need
  the current password, checked like `PATCH /auth/me` (login delays, argon2id
  queue): a stolen session cannot add a way in, or remove one, without it. With
  TOTP on, `register/options` also needs a TOTP or recovery code (`code`, the
  proof `/2fa/disable` asks, checked by `_check_code`; the password with
  `record_success=False`; 403 `auth_2fa_required` without it): a passwordless
  sign-in skips TOTP, so the password alone must not add one.
- **Both factors.** A passwordless sign-in (user verification required)
  replaces the password **and** the TOTP code; the user docs say so.
- **Sign-in.** An assertion is checked under the login delays keyed on the
  client's address and the **owner's** name: a failure (bad signature, origin,
  RP id, no user verification, counter) is `record_failure`, so password, code
  and passkey failures share one budget; an unknown credential id writes a
  `login` `failure` row (`passkey_unknown`). `POST /auth/webauthn/login/options`
  first runs the login limiter's address rules (`login_limiter.check` under
  `OPTIONS_LIMITER_NAME`, a name no failure is recorded for: an address refused
  for its failed sign-ins gets no challenge), then 20 a minute per client
  (`OptionsLimiter`, keyed by the limiter's address key, else the client
  address on a shared-address install; 429 `auth_rate_limited`). A passwordless sign-in opens the session at once, also
  for an account with TOTP (a passkey with user verification is already two
  factors); the `login` row says `details.method = passkey`.
- **Second factor.** A passkey never makes the password ask for more: an
  account without TOTP still signs in with its password alone (on plain http a
  passkey could not be used). With TOTP on, `POST /auth/login` lists `passkey` in
  `second_factor.methods` when the account has one and the request can run a
  ceremony; `POST /auth/webauthn/login/options` with the `pending_token` (not
  consumed) returns options for the account's passkeys, and `POST
  /auth/login/2fa` with `{pending_token, passkey}` (instead of `code`) checks the
  assertion under the pending-login rules: a failure counts as a wrong code
  (401 `auth_2fa_invalid`), the token is bound to its client and voided after
  five. `require_2fa_for_admins` still asks for TOTP (a follow-up may count a
  passkey).
- **Recovery on the host.** `meshloom-admin remove-passkeys <username>` deletes
  every passkey of the account and writes a `passkey.removed` row (`via = cli`).
- **Audit.** `passkey.added`, `passkey.removed` (details: `passkey_id`), and
  `login` rows. Never a challenge, a credential, a key or a pending token.

## First run and the installation code

`prepare_first_run()` (`app/auth/setup_code.py`) runs in the lifespan, while
no account exists:

1. The retired `MESHCORE_BASIC_AUTH_*` variables are never imported (HTTP
   Basic auth was removed, MSHL-100): when set, they only get the
   retired-variable warning (`RETIRED_ENV`, `app/services/legacy_env_settings.py`).
2. On a database that existed before this start (the file was there, or the
   radio history says so: the same `existing_database` as the legacy imports)
   an 8-digit installation code is generated, its SHA-256 stored in
   `auth_settings.setup_code_hash`, and the code logged at WARNING. A new code
   replaces the old one at every start. `meshloom-admin setup-code` prints a
   fresh one (`issue_setup_code()`).
3. On a database created by this start nothing is asked from the local
   network: whoever installed it is in front of it. A restart before the setup
   makes the database existing, and the code is then asked.

**Exposed first run (MSHL-424).** On a fresh database a request is *exposed*
(`setup_code.request_exposed(scope)`) when it does not come through the LAN
path (`auth_via` other than `lan`), carries any forwarding header (`PROXY_HEADERS`
in `app/auth/providers/trusted_network.py`: `X-Forwarded-*`, `Forwarded`,
`X-Real-IP`, `CF-Connecting-IP` and the rest) from a peer that is not a trusted proxy, comes from a client
address that is not local (`ratelimit.client_is_public`: not RFC 1918,
loopback, link-local, ULA or CGNAT per `app/net/local_address.py`, so a
documentation or reserved address is exposed too; on the address a trusted
proxy forwarded), or
when `auth_settings.public_urls` is not empty. The first exposed `GET
/api/auth/session` or `POST /api/auth/setup` issues a code
(`ensure_exposure_code()`, under a lock, only when none is stored), logs it at
WARNING and answers `setup_code_required: true`; setup without it answers 403
`auth_setup_code_invalid`. That code is waived for later non-exposed requests
(kept in memory as `_exposure_code_hash`), so an internet visitor cannot lock
the owner on the LAN out. A code from `meshloom-admin setup-code` or from a
restart is required from everyone. Under ingress the session issues no code
(setup is refused there, `auth_ingress_only`). Limits, both closed by declaring
a public URL: under Docker's userland proxy every client appears as the gateway
(private); a local tunnel that forwards without headers (`ssh -R`, `socat`,
HAProxy in TCP mode) makes Internet visitors appear as 127.0.0.1.

**Fail closed.** `POST /api/auth/setup` answers 503 `auth_setup_unavailable`
until `prepare_first_run()` has succeeded in this process: when it raised at
startup (a locked database), the setup route runs it again and keeps refusing
while it fails. Setup is also refused when the database existed before this
start and no code hash is stored, whatever code is sent, and no exposure code is
issued then. `GET /api/auth/session` then says `setup_code_required: true`.

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
and `params.min_length`. Applied at setup and on a password change.

## Routes (`/api/auth`)

Errors are `{"detail": {"code", "message", "params"?}}`; the frontend
translates `errors.<code>` (slice `x`). Bodies with a password use
`SecretStr` and `hide_input_in_errors`; the app-wide 422 handler drops inputs.

| Route | Access | Behaviour |
|---|---|---|
| `GET /auth/session` | public | Anonymous: `{authenticated: false, setup_required, setup_code_required, methods: {password, ingress, passkey}, secure}` (`passkey`: a WebAuthn ceremony can run on this request, "Passkeys") (`secure`: the server saw https, the frontend warns when the browser is on https and it is false; `methods` is `{password: false, ingress: true}` under ingress, else the reverse), **no instance name or version**. Signed in: plus `user {id, username, display_name, role, must_change_password, totp_enabled, second_factor_enrolment_required}`, `via`, `instance {name, version}` (name: the radio's name, or absent) |
| `POST /auth/setup` | public while no account exists | `{username, password, display_name?, setup_code?}`. 409 `auth_setup_done` once an account exists; 403 `auth_setup_code_invalid` when a code is required (existing database, or an exposed request, "Exposed first run") and wrong or missing; 400 `auth_password_policy`, `auth_username_invalid`; 503 `auth_setup_unavailable` (fail closed, above). Creates the `admin`, clears the code, opens a session (cookie), answers like `GET /auth/session`. 403 `auth_ingress_only` under ingress (the first HA user becomes the admin there) |
| `POST /auth/login` | public | `{username, password}`. One generic 401 `auth_invalid_credentials` for an unknown name, a wrong password, a disabled account or one without a password (ingress, kiosk); each runs one argon2id check, against a fixed hash when there is none, so the time does not tell them apart. At most two checks run at once, eight wait, then 503 `auth_busy`. 429 `auth_rate_limited` (`params.retry_after`, `Retry-After`) while a login delay runs, before any password check ("Login delays"). Rehashes on success when `PARAMS` changed. Answers like `GET /auth/session`. For an account with a second factor it opens no session: `authenticated: false` plus `second_factor {pending_token, methods: ["totp", "recovery"] (+ "passkey" when the account has one and the request can run a ceremony), expires_in}` ("Second factor (TOTP)"). 403 `auth_ingress_only` under ingress, before any password check |
| `POST /auth/login/2fa` | public | `{pending_token, code}` or `{pending_token, passkey}` (an assertion for second-factor options, "Passkeys"; exactly one, 422 otherwise): the second step ("Second factor (TOTP)"). 400 `auth_passkey_unsupported` when the request cannot run a ceremony (not counted). Opens the session and answers like `GET /auth/session`. 401 `auth_2fa_pending_invalid` (unknown, expired or used-up token, or the account changed), 401 `auth_2fa_invalid` (wrong or replayed code), 429 `auth_rate_limited`, 403 `auth_ingress_only` under ingress |
| `POST /auth/logout` | public, idempotent | Revokes the current session, clears the cookie |
| `GET /auth/sessions` | own account (not `kiosk`) | The user's active sessions, newest first, `current` marks this one |
| `DELETE /auth/sessions` | own account (not `kiosk`) | Revokes all the user's sessions except this one: `{revoked}` |
| `DELETE /auth/sessions/{id}` | own account (not `kiosk`) | Revokes one of the user's sessions; 404 `auth_session_not_found` for anyone else's |
| `GET /auth/me` | own account (not `kiosk`) | Profile: `id, username, display_name, role, provider, has_password, must_change_password, totp_enabled, created_at` |
| `POST /auth/webauthn/register/options` | own account, session only | `{password, code?}` (`code` required with TOTP on: 403 `auth_2fa_required`, `auth_2fa_invalid`): creation options (`navigator.credentials.create`, browser JSON). 400 `auth_passkey_unsupported`, 403 `auth_passkey_not_applicable`, `auth_invalid_credentials`, 409 `auth_passkey_limit`, 429 |
| `POST /auth/webauthn/register/verify` | own account, session only | `{credential, name (1-64)}`: stores the passkey, answers it like the list. 400 `auth_passkey_invalid` (challenge unknown, used, from another session, wrong origin or RP id), 409 `auth_passkey_exists`, `auth_passkey_limit` |
| `GET /auth/webauthn/credentials` | own account | `[{id, name, created_at, last_used_at, transports, aaguid, backup_eligible, backed_up}]`; never a key or credential id |
| `DELETE /auth/webauthn/credentials/{id}` | own account, session only | `{password}`. 403 `auth_invalid_credentials`, 404 `auth_passkey_not_found` (also someone else's) |
| `POST /auth/webauthn/login/options` | public, 20/min per address | `{}`: passwordless options (discoverable, user verification required); `{pending_token}`: second-factor options for that pending login (401 `auth_2fa_pending_invalid`, 404 `auth_passkey_not_found`). 400 `auth_passkey_unsupported`, 403 `auth_ingress_only`, 429 |
| `POST /auth/webauthn/login/verify` | public | `{credential}` for passwordless options of this client: opens the session, answers like `GET /auth/session`. 401 `auth_passkey_invalid`, 429 `auth_rate_limited` |
| `GET /auth/2fa` | own account (not `kiosk`) | `{enabled, pending, recovery_codes_left, enrolment_required}`; never the secret |
| `POST /auth/2fa/enrol` | own account (not `kiosk`) | `{password}` (checked like `PATCH /auth/me`'s current password). Stores a new pending secret and answers `{secret, otpauth_uri, issuer, digits, period}`, the only time the secret leaves. 403 `auth_2fa_not_applicable` (no password, ingress, kiosk), 409 `auth_2fa_already_enabled` |
| `POST /auth/2fa/confirm` | own account (not `kiosk`) | `{code}`: the first valid code turns the factor on and answers `{recovery_codes}` (ten, shown once); signs the account out everywhere else. 400 `auth_2fa_invalid`, 409 `auth_2fa_not_pending`, `auth_2fa_already_enabled` |
| `POST /auth/2fa/disable` | own account (not `kiosk`) | `{password, code}` (TOTP or recovery code). 403 `auth_invalid_credentials`, `auth_2fa_invalid`; 409 `auth_2fa_not_enabled`; 429 |
| `POST /auth/2fa/recovery` | own account (not `kiosk`) | `{password, code}` (a TOTP code, not a recovery code): ten new recovery codes, the old ones stop working. Same errors as disable |
| `PATCH /auth/me` | own account (not `kiosk`) | `display_name`; or `current_password` + `new_password` (403 `auth_invalid_credentials` when the current one is wrong, 400 `auth_password_policy`). The current password is checked like a login: the login delays first (`login_limiter.check` on the client's address key and the account's name, 429 `auth_rate_limited`; a wrong one is `record_failure`), then the bounded argon2id queue (503 `auth_busy`). A change clears `must_change_password`, revokes the user's other sessions and closes their sockets (`sign_out_user`), and moves `updated_at` (known devices forgotten). The one write route open while `must_change_password` is set |
| `GET /auth/users` | `admin` | Every account by id: `{id, username, display_name, role, provider, disabled, must_change_password, has_password, totp_enabled, created_at, updated_at}`; never `external_id` or a hash |
| `POST /auth/users` | `admin` | 201. `{username, role, display_name?, password?, must_change_password = true}`. `password` required for every role but `kiosk` (400 `auth_password_required`) and refused for `kiosk` (400 `auth_password_not_applicable`); 400 `auth_password_policy`, `auth_username_invalid`; 409 `auth_username_taken`. Local accounts only (ingress ones come from Home Assistant) |
| `PATCH /auth/users/{id}` | `admin` | `{display_name?, role?, disabled?, must_change_password?}`; a field equal to the stored value is no change. 409 `auth_last_admin` when no enabled admin would be left (checked in the write's transaction); 404 `auth_user_not_found`; 400 `auth_password_not_applicable` for `must_change_password: true` on an account without a password (also when the account changes between that check and the write). A role change or a disable signs the user out everywhere (`sign_out_user`, the admin's own session included); a role of `kiosk` removes the password; raising `must_change_password` closes the user's sockets |
| `DELETE /auth/users/{id}` | `admin` | Deletes the account and its sessions, closes its sockets. 409 `auth_last_admin`, 404 |
| `POST /auth/users/{id}/password` | `admin` | `{password, must_change_password = true}`, password policy against the target's name. Local accounts outside `kiosk` only (400 `auth_password_not_applicable`, also when the account changes before the write). Signs the user out everywhere (the admin's own session stays when it is their account), forgets its known devices |
| `DELETE /auth/users/{id}/sessions` | `admin` | Signs the user out everywhere (sessions and sockets; the admin's own session stays): `{revoked}` |
| `GET /auth/tokens` | own account, `member` or `admin` (403 `auth_forbidden` otherwise; never a token) | Live tokens, newest first: `{id, name, scopes, created_at, last_used_at, expires_at, origin, user_id, username}`, the caller's own, every user's for an admin. Never the secret or the hash |
| `POST /auth/tokens` | same | 201. `{name (1-64), scopes (1-5 of read, read_private, send, repeater_admin, admin), expires_at?}` (Unix seconds, null never). Answers the row plus `token` (`mlt_…`), the only time it is shown. 400 `auth_token_scope` (`params.scope`) for a scope the caller's role does not grant, 400 `auth_token_expiry_invalid` for an expiry not in the future, 409 `auth_token_limit` (`params.max`, 100 live tokens per user) |
| `DELETE /auth/tokens/{id}` | same | 204. Revokes the token (the caller's own; any for an admin) and closes its sockets at once. 404 `auth_token_not_found` for an unknown, revoked or someone else's token |
| `POST /auth/pairings` | own account, `admin` only (403 `auth_forbidden` otherwise; never a token) | 201. `{label (1-64), scopes (1-5), ttl (30-600 s, 300 by default)}`: a single-use pairing code bound to those scopes and to the caller. Answers the row (`id, label, scopes, urls, created_at, expires_at, created_by, username`) plus `code` (`mlp_…`) and `qr_payload` (`meshloom://pair?d=…`), the only time they are shown. 400 `auth_token_scope`, 409 `auth_pairing_limit` (`params.max`, 10 pending per user) ([Device pairing](device-pairing.md)) |
| `GET /auth/pairings` | same | Every pending code, newest first. Never the code |
| `DELETE /auth/pairings/{id}` | same | 204. Revokes a pending code at once. 404 `auth_pairing_not_found` (unknown or no longer pending) |
| `POST /auth/pair` | public, login delays per client (no username slow-down) | `{code, device_name (1-64), extensions?}` → 201 `{token, token_id, scopes, user {id, username, display_name}, urls}`: the device token (origin `pairing`, no expiry) of the code's creator with exactly its scopes. 410 `auth_pairing_expired` for every bad code (one answer), 403 `auth_pairing_ingress`, 429 `auth_rate_limited`, 409 `auth_token_limit` ([Device pairing](device-pairing.md)) |
| `POST /auth/kiosk-bootstrap` | `read`, bearer token of the system `kiosk` account, kiosk host, direct loopback | `{path, expires_in}`: a one-time `/api/auth/kiosk-session?code=…` (60 s). 403 `auth_kiosk_bootstrap_refused` otherwise ("Kiosk device token") |
| `GET /auth/kiosk-session?code=` | public, kiosk host, direct loopback | 303 to `/`; with a pending code, a session cookie of the `kiosk` account. A used, unknown or expired code sets nothing |
| `GET /auth/settings` | `admin` | Access settings: `{trusted_proxies, trusted_networks, audit_retention_days, session_lifetime_days, ingress_default_role, security_notices, require_2fa_for_admins, addresses_identify_clients, ip_ban_enabled, ip_ban_max_failures, ip_ban_window_minutes, ip_ban_duration_minutes, shared_address_install, client_public}` (canonical CIDRs; `[{cidr, user_id}]`; days, 366 by default; days, 30 by default; `member` by default; the Access security notice codes, read only; `shared_address_install`: a `compose` or `container` install; `client_public`: this request's client address is public; both read only) |
| `PATCH /auth/settings` | `admin` | Change them; applies to the next request. 400 `auth_trusted_proxies_invalid` for a non-IP entry, a network wider than `/8` (IPv4) or `/16` (IPv6), an IPv4-mapped entry or more than 32. Recommend the proxy's own address only: a Docker network or its gateway would trust clients reaching a published port through it. `audit_retention_days` 1 to 36500 (422 otherwise); `session_lifetime_days` 1 to 365 (422 otherwise; audited with the key `lifetime_days`, because the audit filter refuses keys containing `session`); `ingress_default_role` one of the four roles (422 otherwise). `trusted_networks` (whole list): 400 `auth_trusted_networks_invalid`, `auth_trusted_network_user_invalid`, `auth_trusted_network_user_must_change`, `auth_trusted_network_overlaps_proxy` (`params.cidr`, `params.proxy`), `auth_trusted_network_loopback`, `auth_trusted_network_overlaps_gateway`, `auth_trusted_network_user_admin` (`params.cidr`); 409 `auth_lan_login_free_admin_rules` (`params.cidrs`) when `lan_login_free: true` while a rule names an admin ("Trusted networks"). `require_2fa_for_admins` (bool, off by default; "Second factor (TOTP)"). `addresses_identify_clients` (bool, off by default; "Login delays"). `ip_ban_enabled` (strict bool), `ip_ban_max_failures` 3 to 1000, `ip_ban_window_minutes` 1 to 1440, `ip_ban_duration_minutes` 1 to 43200 (strict integers, 422 otherwise; "IP ban"). One `settings.changed` row per field, except the `ip_ban_*` fields: one row for all of them, target `auth_settings.ip_ban`, `details` holding the changed fields and their new values |
| `GET /auth/bans` | `admin` | `{bans: [{address, banned_at, expires_at, failures, route}], whitelist: [{network, note, created_at}]}`: the running bans (the one that ends last first) and the whitelist ("IP ban") |
| `DELETE /auth/bans?address=` | `admin` | 204: lifts one ban at once (an address is reduced to its key). 400 `auth_ban_address_invalid`, 404 `auth_ban_not_found` |
| `POST /auth/ban-whitelist` | `admin` | 201 `{network, note?}`: never ban it; a ban on it stops applying. 400 `auth_ban_whitelist_invalid`, 409 `auth_ban_whitelist_exists`, `auth_ban_whitelist_full` (`params.max`) |
| `DELETE /auth/ban-whitelist?network=` | `admin` | 204. 400 `auth_ban_whitelist_invalid`, 404 `auth_ban_whitelist_not_found` |
| `GET /auth/audit?cursor=&limit=` | `admin` | The audit log, newest first: `{entries, next_cursor}`. `limit` 1 to 500 (100 by default); `next_cursor` is opaque, sent back as `cursor` for the older page, null on the last; a malformed cursor is 400 `auth_audit_cursor_invalid`. Reading writes no row |

Every route above that changes something writes one audit row ("Audit log").

`GET /api/health` is public: anonymous callers get exactly `{"ok": true}`
(probes, the kiosk launcher), a signed-in caller the full payload.

### Setting up without the UI

The setup page does this in a browser; from a shell (scripts, tests) it is:

```bash
curl -c jar -H 'Content-Type: application/json' \
  -d '{"username":"owner","password":"a long passphrase","setup_code":"12345678"}' \
  http://HOST:8000/api/auth/setup        # setup_code: existing database, or reached from outside the LAN
curl -c jar -H 'Content-Type: application/json' \
  -d '{"username":"owner","password":"a long passphrase"}' http://HOST:8000/api/auth/login
curl -b jar http://HOST:8000/api/auth/session
```

## Kiosk host

`/var/lib/meshloom/kiosk` (host flag, `app/AGENTS.md`, "Host flags") makes the
first start create a `kiosk` account (`app/services/kiosk.py`, called from
`prepare_first_run`) with no trusted-network rule. `UserRepository.any_exist()`
(the meaning of "setup done" in `/auth/session`, `/auth/setup`, the gate and the
ingress first-user rule) ignores `kiosk` accounts. On a kiosk host an
installation code is issued even on a fresh database; `/auth/session` returns it
as `kiosk_setup_code` only to a direct loopback peer carrying no proxy header
while setup is open.

The account is created when absent by username, at every start: a disabled
`kiosk` account stays disabled, but one an administrator deleted comes back at
the next restart of a kiosk host (harmless: no password, read-only). Remove the
marker to stop that. The code is shown only after the trusted-network request
checks (`rule_blocker`: not relayed or ingress, no trusted proxy, no proxy
header, strict `Host`) with loopback allowed, and any error hides it.
`meshloom-admin setup-code` works on a kiosk host (it tests `any_exist()`); the
screen then stops showing the old code.

### Kiosk device token

The launcher never signs in through a trusted-network rule (lead decision
2026-10-17: no `127.0.0.1 → kiosk` seed). Code: `app/services/kiosk.py` (rules
in its header), `app/routers/auth_kiosk.py`, `pkg/rpi/meshloom-kiosk`,
`pkg/nfpm/postinstall.sh` (`provision_kiosk_token_dir`).

- **Provisioning.** `prepare_first_run` calls `ensure_kiosk_token()` after
  `ensure_kiosk_user()`, at every start of a kiosk host. It keeps exactly one
  live token of the system `kiosk` account: scope `read` (the role has no
  `read_private`, so the token can never read a direct message), no expiry,
  origin `pairing` (a device token nobody typed; a dedicated origin would need a
  migration), name `Kiosk screen`. The secret goes to
  `/var/lib/meshloom-kiosk/kiosk.token` (`0640`), written atomically (temporary
  file, `fsync`, rename). When the file does not hold that one live token
  (missing, edited, revoked, a restore), every live token of the account is
  revoked, the account is signed out everywhere (`sign_out_kiosk`: pending
  codes voided, then `sign_out_user`, sessions and sockets) and a new token
  written: `token.created` and `token.revoked` audit rows,
  `via = system`, `details.reason = "kiosk_rotation"` on the revocations. A
  write that fails revokes the new token (fail closed). Nothing logs the secret.
- **The directory.** The service runs as `meshloom` and `/var/lib/meshloom` is
  `0750`, so the token lives in its own directory, created by the package on a
  kiosk host only: `meshloom:meshloom-kiosk`, mode `2750` (setgid: the file
  gets the group with no `chgrp`, and `meshloom` need not be in the group).
  `meshloom.service` lists it as an optional `ReadWritePaths=-…`. The package
  adds uid 1000 (the desktop user, as `meshloom-kiosk-setup` assumes) to the
  group; the new `meshloom-kiosk-setup` does too, after Imager creates that user.
  The server refuses to write unless it owns the directory, it is a real
  directory (not a link), its group is `meshloom-kiosk` with the setgid bit
  set, and others have no access; it then logs a warning and the screen shows
  the sign-in page.
- **Rotation and revocation.** An admin sees the token in
  **API and device tokens** (owner `kiosk`). `DELETE /auth/tokens/{id}` of the
  kiosk's token first signs the account out (`sign_out_kiosk`: pending codes
  voided, sessions revoked, sockets closed), then calls `ensure_kiosk_token()`:
  a new secret in the file. The launcher sees the file change within 10 s,
  restarts Chromium and signs in with the new token. To keep the screen signed
  out, disable the `kiosk` account (the token is then refused as
  `owner_disabled`).
- **Bootstrap route only.** The token provider refuses the system `kiosk`
  account's token on every request but `POST /api/auth/kiosk-bootstrap`
  (`resolve_token(..., kiosk_bootstrap=...)`, set by the gate from the method
  and path; reason `kiosk_route`), from loopback, the LAN or the relay alike, on
  HTTP and on the WebSocket upgrade: a copy taken from the Pi reads nothing, it
  only buys a one-time code on the Pi itself.
- **Bootstrap.** `POST /auth/kiosk-bootstrap` (`read`) needs a bearer token of
  the system `kiosk` account (`credential == "token"`; a session or another
  account's token is refused), a kiosk host and a direct loopback request
  (`direct_loopback_request`: the `rule_blocker` checks with loopback allowed:
  no relay or ingress, no trusted proxy, no proxy header, strict `Host`). Any
  refusal is one 403 `auth_kiosk_bootstrap_refused` plus a `token.used`
  `refused` row with `details.reason` (`not_kiosk_host`, `not_a_token`,
  `not_kiosk_account`, `not_loopback`). The answer is `{path, expires_in}`:
  `/api/auth/kiosk-session?code=<43 characters>`, a code of 256 bits held in
  memory (by its SHA-256) for 60 s, single use, at most 8 pending (the oldest
  goes). At most 6 codes a minute (`BOOTSTRAP_RATE`, below the 8 pending, so
  no burst can push the screen's code out): beyond, 429 `auth_rate_limited`
  with `params.retry_after` and `Retry-After`, and a `token.used` `delayed`
  row (`details.reason = "rate_limited"`).
- **Exchange.** `GET /auth/kiosk-session?code=` is in `PUBLIC_ROUTES`. On a
  kiosk host and a direct loopback request, a pending code of an enabled
  `kiosk` account is spent and a normal session issued (`issue_session`, the
  cookie of `docs/dev/auth.md` "Sessions and the cookie", sliding lifetime).
  Every answer is a 303 to `/` with `Referrer-Policy: no-referrer` (and
  `no-store`, as every `/api/auth` answer); only a success sets the cookie. A
  `login` row records it, `details.method = "kiosk_token"`, with `reason`
  (`not_kiosk_host`, `not_loopback`, `invalid_code`, `account_unavailable`,
  `rate_limited`) on failure. The code is spent before the account check: one
  try per code. After 10 failed exchanges in a minute (`EXCHANGE_FAILURE_RATE`)
  every code is refused until the window slides.
- **Setup.** The bootstrap works while no administrator exists (the `kiosk`
  account is a principal; `any_exist()` ignores it), so the screen holds a
  kiosk session behind the setup page and its installation code. `SetupPage`
  re-reads `GET /auth/session` every 5 s while it shows the kiosk code, and
  opens the app once an administrator exists.
- **Launcher.** `pkg/rpi/meshloom-kiosk` reads the file (or through
  `sg meshloom-kiosk` when the session started before the group was added),
  accepts only `mlt_[A-Za-z0-9_-]+`, and hands the header to curl through its
  config on stdin (`printf` is a builtin): the token is never in an argv
  (`ps`), a URL or a log. It accepts only a returned path of the expected form
  and opens Chromium on it, else the plain URL (fail closed: the setup or
  sign-in page). Every 10 s (every 60 s through `sg`, to spare a fork chain
  and any audit line per check; on Debian trixie `sg` itself logs nothing to
  auth.log) it compares a CRC of the token file (`cksum` on
  stdin, with the same `sg` fallback) and restarts Chromium when it changed (a
  rotation). Tests: `tests/test_kiosk_token.py` (also runs the launcher's
  functions against fake `curl`, the restart loop against fake `chromium` and
  `sg`, and checks shellcheck).

**Images flashed before 5.0.0** (T11 path). The 4.18 image installed the
launcher, `meshloom-kiosk-setup` and the autostart entry itself; the 5.0 deb
replaces the launcher at the same path, its `postinstall` writes the marker,
creates the group and directory and adds uid 1000 to the group; the next server
start creates the `kiosk` account and its token. The running 4.18 launcher
keeps its old code until the desktop session restarts, so the screen signs in
after the next reboot (the user docs say so). No rule migration: no release
ever seeded a trusted-network rule.

**Upgrade check, simulated (2026-10-18).** A Debian 12 container with the
4.18.2 image layout (uid 1000 `pi`, the 4.18.2 launcher, setup helper and
autostart entry, a database created by the 4.18.2 server), then this branch's
launcher, app and `postinstall.sh`, the real server and the real launcher with
Chromium replaced by a `curl` script: marker written, `meshloom-kiosk` group with
`pi`, directory `drwxr-s--- meshloom meshloom-kiosk`, token `-rw-r-----
meshloom meshloom-kiosk`, readable by `pi` and not by `nobody`, absent from the
server log and from `ps` while the browser ran; with no administrator the
screen got a `kiosk` session and the setup page's code; after setup the next
start opened a `kiosk` session (`/api/channels` 200, `/api/settings` 403); a
bootstrap from the container's LAN address answered 403; `trusted_networks`
stayed empty. Run again after review r1: the kiosk token on `/api/channels`
answered 401; an admin revoking it signed the running browser's session out
(`authenticated: false`), the token file changed, and the launcher restarted
the browser, which signed in again as `kiosk` within 15 s.

**Manual check on a real image (to record before 5.0.0).** Not run yet; record
the result here.

1. Flash the 4.18.2 kiosk image, let it start, then update it to a 5.0 build
   (Settings, or `apt`): after a reboot the screen shows the setup page with the
   installation code, in a `kiosk` session.
2. Create the administrator from a phone: within a few seconds the wall screen
   opens the app as `kiosk` (channels, no direct messages, no settings door).
3. Reboot: the screen opens the app again with no setup or sign-in page.
4. `ps -ef | grep -c mlt_` on the Pi stays 0 while Chromium runs.
5. Revoke **Kiosk screen** in the tokens list: `/var/lib/meshloom-kiosk/kiosk.token`
   changes at once, the screen is signed out and within about 10 s (60 s in a session older than the group) signs in
   again with the new token.
6. From another machine, `curl -H "Authorization: Bearer $(sudo cat
   /var/lib/meshloom-kiosk/kiosk.token)" http://<pi>:8000/api/channels`
   answers 401.
7. Repeat 2–4 with a kiosk image built from this branch.

## Host guard

`app/middleware/host_guard.py` (MSHL-60) answers one question, "is this `Host` a
name of this instance?" (`host_allowed`), for two callers with different
strictness:

- The trusted-network provider calls it directly and **always** refuses a
  rejected host, in both modes (see "Trusted networks").
- `HostGuardMiddleware` follows `auth_settings.host_guard_mode`. `observe` (the
  5.0.0 default, so a reverse proxy name nobody declared yet keeps working
  after an upgrade) passes the request, logs each new name once (warning) and
  records it. `enforce` answers 421 `auth_host_rejected` (`detail.params.host`
  names the host; the message says to declare it in Users & Access) and closes a
  WebSocket upgrade with 1008. `enforce` becomes the default in 5.1 (decision
  Q-A4).

Accepted names: an IP literal, `localhost`, `*.local`, `*.home.arpa`, `*.lan`,
`*.internal`, the machine's short name and `socket.getfqdn()`, the hosts of
`auth_settings.public_urls`, and the Remote hostname
(`host_guard.set_remote_hostname()`; the Remote client, T63, calls it when it
learns the hostname). Exempt, whatever the mode: Home Assistant ingress
(`ingress.is_ingress_request`: add-on and Supervisor peer) and requests tagged
`scope["state"]["via"] == "relay"`. The middleware sits inside the
trusted-proxy middleware (it needs the recorded TCP peer) and outside the auth
gate; the header is read raw, never `X-Forwarded-Host`.

What the settings screen (MSHL-65) reads: `GET /api/auth/settings` returns
`host_guard_mode` and `rejected_hosts`, a list of `{host, count, first_seen,
last_seen}`, newest first, at most 64 (`MAX_REJECTED_HOSTS`, least recently
seen dropped). It lives in memory and is empty after a restart; only a
name of letters, digits, dots and hyphens (LDH, 253 characters at most) is
recorded; anything else (quotes, semicolons, braces, underscores, non-ASCII) is
neither listed nor logged. The first-sighting warning is rate limited
(`WARNING_BURST` lines per `WARNING_WINDOW_S`, then one summary line when the next
window opens), so cycling through names cannot flood the log. The 421 carries the
static security headers (`nosniff`, `Referrer-Policy`, `X-Frame-Options`, a
`frame-ancestors 'none'` CSP, `Permissions-Policy`, `no-store`; no HSTS) because it
is written outside `app/main.py`'s header middleware.

`enforce` records the refused names too (before the 421 or the 1008), so the screen
lists what was refused; the names are client-supplied, hence the risk text and the
confirmation step of "Allow". Recovery without the UI: `meshloom-admin
host-guard-observe` (audited as `settings.changed`, `via = "cli"`; like the other
commands it needs a shell and the database file, no extra privilege). The running
server sees it without a restart: a request about to be refused re-reads the settings
row first, at most every 2 s (`REFRESH_MIN_INTERVAL_S`), and keeps its in-memory
settings if the read fails. Public URL hosts
are converted to punycode (IDNA), checked label by label (1 to 63 LDH characters, no
leading or trailing hyphen), the port is 1 to 65535, and the path prefix is limited to
`[A-Za-z0-9._~-]` segments and `%XX` escapes (no dot segment, even encoded).

Settings (MSHL-65, Settings → Users & Access → Public addresses,
`AccessPublicAddressesPanel.tsx`): `PATCH /api/auth/settings` (admin) takes
`public_urls` and `host_guard_mode` and returns them in the view. Entries go
through `host_guard.normalize_public_urls`: `https` only, a DNS name (LDH) or IP
literal, optional port (443 dropped) and path prefix, no credentials, query or
fragment, at most 16, canonical form; a refusal is the coded 400
`auth_public_urls_invalid`. **Lock-out rule**: the route computes the resulting
mode and list and answers 400 `auth_host_guard_would_lock_out` (`params.host`) when
`host_guard.would_lock_out(scope, mode, urls)` says the request being answered
would be rejected (ingress and relay requests are exempt, as in the middleware).
It applies to switching to `enforce`, and to removing the current host while
`enforce` is on. Saving a list `forget_rejected`s every declared host (the "Allow"
button is a PATCH adding `https://<host>`). Both changes are audited
(`settings.changed`, targets `auth_settings.public_urls` and
`auth_settings.host_guard_mode`). No environment variable and no migration: the
columns exist since migration 101. Not done here: a dashboard banner for hosts
rejected in the last 24 hours, and the pairing payloads / Remote page that will read
`public_urls`.

## Manual checklist before the 5.0.0 tag

Automated tests cover the gate, the providers and the CLI; they cannot cover
what depends on the install kind (users, paths, proxies, Docker networks, the
Supervisor). Run this list on a build from `preversion-5` for each of the five
install kinds (`MESHLOOM_INSTALL_KIND`: `package`, `compose`, `addon`,
`container`, `source`; `app/services/install_kind.py`) and the kiosk image, old
and new, and record the date, the build and the result of each line in the
release PR. A line that cannot be run is written down as not run, with the
reason. Never run it against a production instance or its data.

### For every install kind

Use a fresh database for A, a copy of a 4.18 database for B.

A. **Fresh install.**
1. Open the address: the setup page asks for the administrator with **no**
   installation code (add-on: not asked, see below). Create it; you land in the
   app.
2. Sign out, sign in with a wrong password six times: the delay message
   appears, the right password still works for this browser.
3. Create a `member`, a `viewer` and a `kiosk` in **Users & Access**. As each:
   `member` sends a message; `viewer` cannot (the send control is hidden and
   `POST /api/messages/channel` answers 403 `auth_forbidden`); `kiosk` sees channels, no
   direct messages, no settings door.
4. `curl -i <url>/api/channels` without credentials answers 401; a response
   carries `Cache-Control: no-store` on `/api/auth/session`.
5. Create an API token (scope `read`), `curl -H "Authorization: Bearer …"
   <url>/api/channels` answers 200 and `/api/settings` 403; revoke it: 401 at
   once.
6. Turn on two-factor authentication (QR code, recovery codes), sign out and in
   with the code, then with a recovery code; `meshloom-admin disable-2fa <user>`
   (command per kind below) lets the password alone in again.
7. Sessions: sign in from two browsers, revoke one from **My sessions**: its live
   connection closes within 30 s. `meshloom-admin revoke-sessions --all` signs
   everyone out.
8. Full backup with **Include accounts**, then restore it on a second instance:
   the same accounts and passwords work. A restore of a backup without
   accounts keeps the current ones.
9. The audit log lists the sign-ins, the account changes and the token events
   above, and no password, code or token.

B. **Upgrade from 4.18.**
1. Without Basic variables: after the update the setup page asks the
   **installation code** printed in the log (`journalctl -u meshloom`,
   `docker logs`, the add-on **Log** tab); a wrong code is refused, one per
   second; `meshloom-admin setup-code` prints a new one and the old one stops
   working.
2. With the retired `MESHCORE_BASIC_AUTH_*` set: they are not imported, the
   log warns they are retired, and the setup page asks the installation code
   as in 1; a script using `Authorization: Basic` gets a plain 401 (use an API
   token).
3. With trusted network rules: after the update they sign nobody in until an
   admin turns on **Login-free on the local network** (migration 136 logs a
   warning).
4. The 4.18 history, contacts and channels are intact, the radio still binds
   (**Bind without wipe**).

### Per install kind

| Kind | Where to check | What differs |
|---|---|---|
| `package` (apt/dnf, Pi image) | `sudo meshloom-admin list-users`; log in `journalctl -u meshloom`; `/etc/meshloom/meshloom.env` | `sudo` is required, the command switches to `meshloom`; extensions are not locked by default (`MESHCORE_DISABLE_PLUGINS` is commented out in `meshloom.env`); loopback is not a trusted-network source; `systemctl restart meshloom` keeps sessions |
| `compose` | `docker compose exec meshloom meshloom-admin list-users`; `docker compose logs` | behind the Docker bridge every visitor looks like the gateway: with no **Trusted proxies** set, only the per-account delay applies (see user docs); a rule covering the gateway is refused while a public address is set |
| `container` (plain `docker run`) | `docker exec -it <name> meshloom-admin list-users` | same as compose; `--user 10001` when `MESHLOOM_RUN_AS_USER` is set |
| `source` | `uv run python -m app.cli list-users` in the checkout | database in `data/`; no root; loopback refused for trusted networks |
| `addon` | see "Ingress" check list (steps 1–8) and "Manual check on a real Home Assistant" | no setup page: the first Home Assistant user is `admin`, the next `member`; published port 8000 asks for a password; `meshloom-admin` through *Advanced SSH & Web Terminal* with Protection mode off |

On `package`, `compose`, `container` and `source` also check, once each:

1. Behind a reverse proxy (Caddy or Nginx) with the proxy listed in **Trusted
   proxies**: the audit log and **My sessions** show the visitor's address, the
   cookie is `__Host-meshloom_session`, passkeys can be added on the declared
   `https://` name, and a trusted-network rule does **not** sign a device in.
2. Without listing the proxy: a warning names it, every visitor shares the
   proxy's address, and the page still works. Declare the name in **Public
   addresses**, switch to strict, open the instance under an undeclared name
   (421), then `meshloom-admin host-guard-observe` brings it back.
3. A WebSocket from a page of another site is refused, and an unsafe request
   with a foreign `Origin` answers 403 `auth_origin_rejected`.
4. The radio proxy (if turned on) accepts a connection from another machine on
   5051: expected, recorded as the owner's decision.

### Kiosk image, old and new

Run the seven steps of "Manual check on a real image" under "Kiosk device
token" for both images:

- **Old**: the 4.18.2 kiosk image, updated to the 5.0 build (the launcher, the
  `kiosk` group and the token are installed by the package, and the screen signs
  in after the reboot).
- **New**: an image built from this branch.

Also check on each: `Settings → Users & Access` shows **Kiosk screen** in the
token list; `ps -ef | grep -c mlt_` stays 0; with the `kiosk` account disabled
the screen shows the sign-in page and not the app; and the setup code appears
only on the Pi's own screen (not from another machine, not through a proxy).

### Record

| Date | Build | Kind | Result |
|---|---|---|---|
| | | `package` | |
| | | `compose` | |
| | | `addon` | |
| | | `container` | |
| | | `source` | |
| | | kiosk, old image | |
| | | kiosk, new image | |
