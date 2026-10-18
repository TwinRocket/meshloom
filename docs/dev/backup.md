# Backup sections and restore hooks

How a feature adds its data to the full backup (`meshloom-backup-v2`) and
reacts to a restore, without touching the envelope. Code:
`app/services/backup_sections.py`. The formats, the coverage table, the
identity decision and the restore job are in `app/AGENTS.md`, "Backups"; the
user page is `docs/user/{en,fr}/backup.md`.

## The envelope

There is one v2 format. A `meshloom-backup-v2` document is the v1 fields
(contacts, channels, settings, groups, push preferences), plus `identity`,
`radio`, `wifi` and `meshloom_extra` (all from #156), plus one optional field:

```json
"sections": {
  "plugins": {"version": 1, "sensitive": false, "secrets_omitted": true, "data": {"...": "..."}}
}
```

- `sections` maps a section name (`^[a-z][a-z0-9_]{0,47}$`) to a
  `BackupSection` (`app/models.py`): the section's `version`, `sensitive`,
  `secrets_omitted` and `data` (any JSON). The export writes `version` and
  `sensitive` from the registration, whatever the export function returned.
- A file without `sections` (a #156 export) and a v1 file restore exactly as
  before. A v2 file whose `sections` is empty restores the same data as the v1
  round trip of the same database (`tests/test_backup_sections.py`).
- The encrypted envelope (`meshloom-backup-v2-encrypted`) wraps the whole
  document, sections included: nothing changes there.
- `GET /settings/backup/json` stays `meshloom-backup-v1`, without sections and
  without the notification secrets (`********`, paths listed in
  `secret_omitted`; a restore of that file keeps the stored values). The full
  backup carries them.
- **Older versions.** A 4.18.x install reads a v2 file as a v1 one: it restores
  the v1 fields and silently drops `identity`, `radio`, `wifi`,
  `meshloom_extra` and `sections`. The user page tells users to restore v2 files
  on 5.0 or later only.
- **Size.** `POST /settings/backup/inspect` and `POST /settings/backup/restore`
  read their body in chunks and refuse more than 64 MiB
  (`MAX_BACKUP_BODY_BYTES`) with 413 `backup_too_large`, before parsing.

## Registering a section

```python
from app.models import BackupSection
from app.services.backup_sections import register_backup_section


async def export_plugins() -> BackupSection | None:
    rows = await PluginRepository.list_installed()
    if not rows:
        return None  # nothing to write: the section is left out of the file
    return BackupSection(secrets_omitted=True, data={"plugins": [...]})


async def restore_plugins(section: BackupSection, conn: aiosqlite.Connection) -> list[str]:
    # Write through conn only (see "Transactions").
    await conn.execute("INSERT INTO ...", (...,))
    return []  # warnings: "code" or "code:param"


register_backup_section(
    "plugins",
    export_plugins,
    restore_plugins,
    description={"en": "Installed plugins", "fr": "Extensions installées"},
    sensitive=False,
    version=1,
)
```

Register at import time of a module the app always imports (a service module
imported by its router), so the section exists before the first export or
restore. Registering a name twice, a missing `en` or `fr` description, or a
version below 1 raises `ValueError`.

- **`description`** (`en` and `fr`, both required): what the restore summary
  (`POST /settings/backup/inspect`, `sections[]`) shows for the section.
- **`sensitive`** (required): the section holds secrets, hashes included
  (password hashes, TOTP secrets, tokens). The summary marks it "contains
  secrets", and an export without a passphrase adds the file warning
  `section_sensitive_unencrypted:<name>` (the export view tells the user to
  keep the file like the radio key, or to encrypt it). Prefer
  `secrets_omitted` when the secrets can be re-entered.
- **`version`** (default 1): the newest `data` shape this code reads. Bump it
  when `data` changes shape, and keep reading the older shapes. A file section
  with a higher version (written by a newer Meshloom) is not restored: warning
  `section_too_new:<name>`, and `too_new` in the summary.
- **Export.** `export_backup_v2()` calls every export function in
  registration order, except the names in `skip` (`export_sections(skip=...)`:
  the account sections when the export dialog's **Include accounts** box is
  unchecked, `include_accounts: false` on `POST /settings/backup/export`). One
  that raises is logged and left out; the file's `warnings` gets
  `section_export_failed:<name>`.
- **Restore.** The restore job restores the file's sections inside its
  `meshloom` step, after `meshloom_extra`, so turning that step off skips them
  too. A restore function returns warnings for `result.warnings`; one that
  raises is rolled back and logged, becomes `section_failed:<name>`, and the
  job goes on with the other sections. The UI translates every warning code
  (`settings.fullRestore.warnings.<code>`, with the parameter as `{{value}}`):
  add your codes to the i18n files (fr and en) and to the code list in
  `frontend/src/test/backupRestoreFlow.test.tsx`.
- **`requires`.** `register_backup_section(name, ..., requires=["users"])`:
  the named sections are restored first, in the same restore. When one of them
  is missing from the file, too new or failed, this section is skipped
  (`section_skipped:<name>`). A cycle is refused at registration. A required
  name nobody registered counts as missing.
- **Kept on purpose.** A restore function that refuses the file's data and
  keeps the current one (the accounts without a usable administrator) raises
  `SectionKept(warnings)`: nothing it wrote is committed, its warnings reach
  the result, and it does **not** count as restored, so the sections that
  require it are skipped. Returning a warning alone would let them run against
  the data it kept.
- **`restore_with`.** `register_backup_section(name, ..., restore_with="users")`
  for rows that belong to another section's rows (the `api_tokens` of the
  accounts): implies `requires`, and the restore runs in the owner's
  transaction, right after it, only when the owner was written. Owner kept,
  failed, skipped or missing: this section is skipped and the current rows
  stay. A failure of either rolls both back (`section_failed:` for each).

- **Never exported.** `NEVER_EXPORTED_SECTIONS` (`sessions`, `pairing_codes`,
  `auth_audit`, `remote_identity`): registering one of these names raises, and
  a file section with one of them is ignored with an INFO log line.

## Transactions

Each restore function runs in **its own transaction**: the restore opens
`db.tx()` and passes its connection. A function that returns commits
everything it wrote; one that raises (or runs past
`SECTION_RESTORE_TIMEOUT`, 60 s) is rolled back entirely, and the other
sections still run. So:

- Write through `conn` only, with SQL or repository helpers that take a
  connection. Never call `db.tx()`, `db.readonly()` or a repository method that
  does (`AppSettingsRepository.update()`, ...): the lock is held, so the call
  waits until the timeout and the section fails.
- Do no I/O other than the database in it (no radio, no network): read what you
  need in the export function, and refresh in-memory caches or restart
  services from an `on_restore` hook, which runs after every section.

## Restore rules

These hold for every section; a restore function must keep them:

1. **A missing section leaves its data untouched.** A section absent from the
   file is not restored at all; its restore function is never called with an
   empty value.
2. **`secrets_omitted` never clears a stored secret.** A section exported
   without its secrets says so with `secrets_omitted: true`; its restore keeps
   the stored secret wherever the file has none, and never writes an empty one.
3. **An unknown section is ignored** with an INFO log line naming it, and a
   section too new for its owner is skipped: a file from a newer version
   restores what this version knows. Both are listed in the summary.
4. **Identity seeds are never exported.** The per-service instance identity
   seeds (share, Remote: `app/services/instance_identity.py`) are
   never in a file, even though the radio private key is. Name their columns
   `app_settings.<service>_instance_seed` and mark them `excluded` in `BACKUP_COVERAGE`:
   - the JSON export reads every `app_settings.*_seed` value and refuses a
     document (v1 or v2, sections included) that contains one, with a 500
     `backup_identity_seed_leak` (coded on both `GET /settings/backup/json` and
     `POST /settings/backup/export`);
   - the SQLite snapshot (`GET /settings/backup/database`) sets every
     `app_settings.*_seed` column to NULL in the copy, with the other secrets
     the full backup excludes (`SNAPSHOT_SCRUBBED_COLUMNS`: the BLE PIN and the
     previous-transport snapshot that holds it) and the local accounts
     secrets (`SNAPSHOT_SCRUBBED_AUTH_COLUMNS`: `users.password_hash`,
     `users.totp_secret`, `auth_settings.setup_code_hash`; the `sessions`
     and `recovery_codes` tables and the plugin config secrets in
     `plugin_secrets` are emptied,
     `SNAPSHOT_EMPTIED_TABLES`), under `PRAGMA secure_delete`
     so the old values do not stay in the file's free space. The live database
     keeps them. `api_tokens` stays in the copy: it holds SHA-256 hashes of
     256-bit secrets, which give nothing without the token itself.

   The sharing consent (`app_settings.share_enabled`) and the Store consent
   (`app_settings.store_online`, not in `AppSettings` yet) are not seeds but follow the
   same rule: they are left out of the JSON export (`BackupExport` serializes
   `settings` without `share_enabled`; `store_online` is not in `AppSettings`),
   never restored, and set to 0 in the SQLite snapshot
   copy (`SNAPSHOT_RESET_COLUMNS`), so a copy put back elsewhere cannot enable the
   sharing or Store egress.

   The seeds are regenerated after a restore, through the hook below.
5. **Secrets in `data` are plain JSON.** Unlike `identity`, `wifi` and
   `meshloom_extra.vapid`, a section's `data` is not masked in other dumps.
   Keep secrets out of log lines and exception messages, and register the
   section as `sensitive`.

## Accounts (`users`, `api_tokens`)

`app/services/backup_accounts.py` registers the `users` section (Access T65,
`sensitive`, version 3) and the `api_tokens` section (Access T27, below),
imported by `services/backup.py`.

- **Data.** `{"users": [...]}`, one object per `users` row: `id`, `username`,
  `display_name`, `password_hash` (argon2id), `role`, `provider`,
  `external_id`, `disabled`, `must_change_password`, `totp_secret`,
  `totp_enabled_at`, `totp_last_counter`, `created_at`, `updated_at`, and
  since version 2 `recovery_codes` (`[{code_hash, created_at, used_at}]`, the
  argon2id hashes of the TOTP recovery codes, never a code; a version 1 file
  has none and restores without them), and since version 3 `passkeys`
  (`[{credential_id, public_key, sign_count, transports, aaguid, name,
  backup_eligible, backed_up, created_at, last_used_at}]`, ids and COSE keys
  in base64url; a public key, never a secret; a version 1 or 2 file restores
  without them). Never
  `username_key` (recomputed), sessions, the audit trail or `auth_settings`
  (with the setup code hash). Keys a file adds to a row are dropped
  (`BackupUser`, `extra="ignore"`, `hide_input_in_errors`).
- **Include accounts.** `ACCOUNT_SECTIONS` lists the sections the export box
  covers (`users` and `api_tokens`). Checked by default; unchecked, none of them is
  exported and the restore of that file keeps the current accounts (rule 1).
- **Restore.** The file's accounts replace the instance's, ids kept, in the
  section's transaction. A section with no enabled administrator **who can sign
  in here** writes nothing and returns `accounts_no_admin`: a local account
  with a password hash, or an `ingress` identity only when the install kind is
  `addon` (`BackupUser.can_sign_in_here`; an add-on backup whose only admin is a
  Home Assistant user, restored on Docker or the package, is refused). The
  count is checked again after the writes with the same rule
  (`_USABLE_ADMIN_SQL`; a failure raises and rolls back), so a restore never
  locks everyone out. Otherwise it returns `accounts_restored`. Every
  `sessions` row is deleted and every WebSocket of the old and new account ids
  is closed at once (`ws_manager.close_user`; a kept id may name another
  person), `updated_at` becomes at least the restore time and past both the
  file's and the replaced row's value (every known-device cookie, keyed on it,
  is invalidated), and a trusted network rule (`auth_settings.trusted_networks`)
  survives only when its `user_id` names the same `username_key` before and
  after; each dropped rule is a `trusted_network_dropped:<cidr>` warning. The
  in-memory rules are replaced by the kept ones inside the restore (fail
  closed), and the `accounts` hook (`register_on_restore`) resets them so they
  are read again.
- **Row rules** (`BackupUser`, the accounts API's invariants). A password hash,
  TOTP material and a forced password change are kept only on a `local`
  account outside `kiosk`, stripped otherwise; recovery codes only with an
  enabled TOTP factor (secret and enable time), at most 64, each an argon2id
  hash under the same cost limit; passkeys only on an account that keeps a
  password hash (at most 64, strict base64url, a credential id listed twice is
  restored once); a `system` account must be a
  `kiosk`; a hash must be argon2id (`v=19`, `p=1`) with a cost up to libsodium's
  MODERATE limits (256 MiB, 3 passes), so a file cannot make a login allocate
  more.
- **Fail closed.** An invalid row (unknown role, oversized field, two rows
  with one username key, a refused hash or `system` role) raises: the section is rolled back
  (`section_failed:users`) and the current accounts stay.
- **Never log** a hash, a TOTP secret or a row; log lines carry counts.
- **API tokens.** The `api_tokens` section (`sensitive`, `restore_with="users"`,
  in `ACCOUNT_SECTIONS`) holds every live token (not revoked, not expired):
  id, owner id, name, SHA-256 hash, scopes, created, last used, expiry,
  origin. Never a secret: the database has none. Its restore replaces the
  tokens and keeps a row only when its owner exists after the accounts restore
  and that owner's role grants its scopes (`api_token_dropped:<id>`
  otherwise); a hash must be 64 lowercase hex. It runs in the accounts'
  transaction, right after them, so an owner id always names the restored
  account; when the accounts are kept (`accounts_no_admin`) the tokens are
  not touched (`section_skipped:api_tokens`). A `users` restore without this
  section leaves no token (the replaced accounts' rows cascade). The sockets of
  the replaced tokens close at once.
- **Extension point.** Account material goes inside `users` as a new version
  of its `data`, as the recovery codes (version 2, T50) and the passkeys
  (version 3, T52) did. The SQLite snapshot keeps `webauthn_credentials`
  (public keys).
- Tests: `tests/test_backup_accounts.py`.

## The `on_restore` hook

```python
from app.services.backup_sections import register_on_restore


async def regenerate_remote_identity(source: str) -> None:
    ...  # "json" or "sqlite"


register_on_restore("remote_identity", regenerate_remote_identity)
```

Each hook runs **once per restore**, in registration order. A hook that raises
is logged and the others still run.

- **JSON.** Once per flat v1 restore (`POST /settings/backup/restore` without
  `backup`, or `restore_json()`), and once per restore job that wrote
  something: the identity step done, or the `meshloom` step reached (done or
  failed). A job refused before writing, or with the `meshloom` step off and no
  identity change, runs no hook. The job runs them at its end, after every
  step; `restore_json(..., run_hooks=False)` is how the job avoids a second run.
- **SQLite.** `write_sqlite_backup()` adds a one-row `meshloom_backup_snapshot`
  table to the downloaded copy, never to the live database. When the app starts
  on a database that has it (a snapshot put back in place of `meshcore.db`),
  the lifespan runs the hooks with `"sqlite"` right after `db.connect()`, then
  drops the table. A snapshot made before this marker existed runs no hook.
  Its seeds are already NULL (see rule 4): a hook must create new ones when it
  finds none.

Consumers: `app/services/instance_identity.py` registers `instance_identity`
(Links & Files T45): it regenerates every per-service seed that exists, so a
restored instance never signs with the identity it had before; an empty seed (a
snapshot's, or a service never used) stays empty until its first use creates
one. Access (T38) adds its Remote seed to that module (`INSTANCE_SEED_SERVICES`)
rather than a hook of its own, so any restore regenerates the Remote identity
too; `services/backup_accounts.py` registers `accounts` (reloads the trusted
network rules).

## Tests

`tests/test_backup_sections.py` (fixtures in `tests/fixtures/backup/`: a real
4.18.2 export and a #156 full backup) covers the round trips, a registered
section, an unknown or too new one, `requires`, the per-section transactions,
sensitivity and the summary, failures, the seed guard and the snapshot scrub,
the body size limit and both hook paths. Unregister what a test registers: the
test file's autouse fixture saves and restores the registry.
