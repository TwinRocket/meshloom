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
  and the key `NFKC(name).casefold()` in `username_key`, which has the `UNIQUE`
  constraint. SQLite's `NOCASE` is not used: it folds ASCII only, so `Élise` and
  `éLISE` would both be accepted. A clash raises `UsernameTakenError`.
- **User ids are never reused** (`AUTOINCREMENT`), so an audit row or a trusted
  network rule never ends up pointing at a later account.
- **Roles and providers** are CHECK-constrained in SQL and `Literal` types in
  Python. `password_hash` is NULL for identities that never log in with a
  password (`ingress`, the kiosk `system` account).
- **Passwords** are hashed with argon2id through PyNaCl (`nacl.pwhash.argon2id`,
  libsodium), `OPSLIMIT_MODERATE` / `MEMLIMIT_MODERATE` (3 passes, 256 MiB). The
  stored value is libsodium's encoded string, which carries its salt and
  parameters; `needs_rehash()` tells a caller to store a fresh hash after a
  successful login when the module parameters change. Passwords are NFC
  normalized and capped at 1024 characters. `argon2-cffi` was not taken: a second
  CFFI build for the same algorithm, while PyNaCl already ships wheels for
  aarch64 and armv7.
- **Off the event loop.** A hash or a verify takes about half a second on a
  desktop and several seconds on a Raspberry Pi, and allocates 256 MiB: from
  async code, call `hash_password_async()` / `verify_password_async()`, which run
  in a worker thread. Bounding how many run at once (the login limiter, a
  semaphore) belongs to the caller.
- **Sessions and codes are opaque secrets** (`tokens.new_token()`, 256 bits from
  `secrets`). Only `hash_token()` (SHA-256 hex) is stored. The repository looks a
  session up by that hash and checks the stored value with `hmac.compare_digest`.
  No signed cookie, no JWT: revocation needs a table anyway.
- **Session lifecycle.** `create()` keeps at most 50 active sessions per user and
  revokes the oldest beyond that; `touch()` writes `last_seen_at` at most once a
  minute and can slide `expires_at` in the same write; `revoke()`,
  `revoke_all_for_user()` and `purge()` (deletes expired and revoked rows). The
  lifetime comes from `auth_settings.session_lifetime_days` (30 by default).
  Revoking sessions on a role change or a disable is the caller's decision.
- **Audit.** `AuthAuditRepository.add()` appends a row; its `details` are JSON and
  a key containing a word such as `password`, `token`, `secret`, `hash`, `otp`,
  `pin` or `cookie` (split on `_`, `-` and camelCase) raises `ValueError`
  instead of being stored. Retention is `audit_retention_days`, **366 days** by
  default (owner decision Q-A14); `purge_older_than()` is the helper, no loop
  calls it yet.
- **Settings** live in their own single-row table `auth_settings`, not in
  `app_settings`, so the setup code hash never reaches `AppSettings`, the v1
  backup settings or the settings API. `AuthSettings` exposes `setup_code_set`
  instead of the hash; `get_setup_code_hash()` / `set_setup_code_hash()` are the
  only accessors. CIDRs (`trusted_networks[].cidr`, `trusted_proxies`) are
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
`auth_audit` and the setup code are never exported. The SQLite download is a raw
copy and contains everything.
