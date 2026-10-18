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
  registration order. One that raises is logged and left out; the file's
  `warnings` gets `section_export_failed:<name>`.
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
   seeds (share, Remote: `app/services/instance_identity.py` once it lands) are
   never in a file, even though the radio private key is. Name their columns
   `app_settings.<service>_seed` and mark them `excluded` in `BACKUP_COVERAGE`:
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
     table is emptied, `SNAPSHOT_EMPTIED_TABLES`), under `PRAGMA secure_delete`
     so the old values do not stay in the file's free space. The live database
     keeps them.

   The seeds are regenerated after a restore, through the hook below.
5. **Secrets in `data` are plain JSON.** Unlike `identity`, `wifi` and
   `meshloom_extra.vapid`, a section's `data` is not masked in other dumps.
   Keep secrets out of log lines and exception messages, and register the
   section as `sensitive`.

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

Consumers: Links & Files (T10/T45, share identity) and Access (T38/T65, Remote
identity and accounts).

## Tests

`tests/test_backup_sections.py` (fixtures in `tests/fixtures/backup/`: a real
4.18.2 export and a #156 full backup) covers the round trips, a registered
section, an unknown or too new one, `requires`, the per-section transactions,
sensitivity and the summary, failures, the seed guard and the snapshot scrub,
the body size limit and both hook paths. Unregister what a test registers: the
test file's autouse fixture saves and restores the registry.
