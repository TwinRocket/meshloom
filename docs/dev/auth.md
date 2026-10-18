# Local accounts: data model

Meshloom 5.0.0 makes login mandatory (LAN included) with four roles: `admin`,
`member`, `viewer` and `kiosk`. This page covers the data layer only: tables,
password hashing, opaque tokens and the repository. Nothing reads it yet: the
middleware, the routes and the UI come with the later Access tickets (auth core,
audit log, 2FA, backup sections). Like every doc here this is a hypothesis: the
code wins.

## Where things are

| Piece | File |
|---|---|
| Tables (existing databases) | `app/migrations/_101_auth_data_model.py` |
| Tables (fresh databases) | `SCHEMA_TABLES` / `SCHEMA_INDEXES` in `app/database.py`, textually identical |
| Typed models | `app/auth/models.py` |
| Password hashing (argon2id) | `app/auth/passwords.py` |
| Opaque secrets (sessions, setup code) | `app/auth/tokens.py` |
| Repository | `app/repository/auth.py` |
| Tests | `tests/test_migrations/test_migration_101.py`, `tests/test_auth_passwords.py`, `tests/test_auth_repository.py` |

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
  thread. Bounding how many run at once (the login limiter, a
  semaphore) belongs to the caller.
- **Sessions and codes are opaque secrets** (`tokens.new_token()`, 256 bits from
  `secrets`). Only `hash_token()` (SHA-256 hex) is stored. The repository looks a
  session up by that hash and checks the stored value with `hmac.compare_digest`.
  No signed cookie, no JWT: revocation needs a table anyway.
- **Session lifecycle.** `create()` keeps at most 50 active sessions per user and
  revokes the oldest beyond that; `touch()` writes `last_seen_at` at most once a
  minute and can slide `expires_at` in the same write; `revoke()`,
  `revoke_all_for_user()` and `purge()` (deletes expired and revoked rows).
  `touch()` never writes a revoked or expired session, so it can never revive
  one, and a new session never evicts itself (a clock that went backwards).
  User agents are capped at 512 characters. The
  lifetime comes from `auth_settings.session_lifetime_days` (30 by default).
  Revoking sessions on a role change or a disable is the caller's decision.
- **Audit.** `AuthAuditRepository.add()` appends a row; its `details` are JSON.
  Each key, at any depth, is lowercased with every non-alphanumeric character
  removed (`new_Password-2` becomes `newpassword2`), then refused with
  `ValueError` when it contains a fragment such as `password`, `pwd`, `secret`,
  `token`, `key` (so `apiKey`, `privateKey`), `hash`, `seed`, `otp` (so `totp`),
  `cookie`, `setupcode` or `recoverycode`, or equals `code` or `pin`. Harmless
  keys that contain a fragment are refused too: that is accepted. Failed
  logins are written without authentication, so every field is bounded:
  `action` 64, `outcome` 32, `target` 256, `ip` 64, `via` 32 characters
  (truncated), and `details` larger than 2 KiB serialised are stored as
  `{"bytes": <size>, "truncated": true}`. Retention is `audit_retention_days`, **366 days** by
  default (owner decision Q-A14); `purge_older_than()` is the helper, no loop
  calls it yet.
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

## Backups

The new tables and settings are not in any JSON backup yet. The `users` and
`api_tokens` sections of the full backup are a later ticket; `sessions`,
`auth_audit` and the setup code are never exported. The SQLite download
(`GET /settings/backup/database`) keeps the accounts and the audit trail but
not their secrets: `prepare_sqlite_snapshot()` sets `users.password_hash`,
`users.totp_secret` and `auth_settings.setup_code_hash` to NULL and empties
`sessions` in the copy, under `secure_delete`. A restored snapshot therefore
has accounts that cannot log in with a password until an admin reset.
