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
  "plugins": {"version": 1, "secrets_omitted": true, "data": {"...": "..."}}
}
```

- `sections` is a map from a section name (`^[a-z][a-z0-9_]{0,47}$`) to a
  `BackupSection` (`app/models.py`): the section's own `version` (starts at 1,
  bump it when `data` changes shape), `secrets_omitted` and `data` (any JSON).
- A file without `sections` (a #156 export) and a v1 file restore exactly as
  before. A v2 file whose `sections` is empty restores the same data as the v1
  round trip of the same database (`tests/test_backup_sections.py`).
- The encrypted envelope (`meshloom-backup-v2-encrypted`) wraps the whole
  document, sections included: nothing changes there.
- `GET /settings/backup/json` stays `meshloom-backup-v1`, without sections.

## Registering a section

```python
from app.models import BackupSection
from app.services.backup_sections import register_backup_section


async def export_plugins() -> BackupSection | None:
    rows = await PluginRepository.list_installed()
    if not rows:
        return None  # nothing to write: the section is left out of the file
    return BackupSection(secrets_omitted=True, data={"plugins": [...]})


async def restore_plugins(section: BackupSection) -> list[str]:
    ...  # write it back through the same checks as the routes
    return []  # warnings: "code" or "code:param"


register_backup_section("plugins", export_plugins, restore_plugins)
```

Register at import time of a module the app always imports (a service module
imported by its router), so the section exists before the first export or
restore. Registering a name twice raises `ValueError`.

- **Export.** `export_backup_v2()` calls every export function in
  registration order. One that raises is logged and left out; the file's
  `warnings` gets `section_export_failed:<name>`.
- **Restore.** The restore job restores the file's sections inside its
  `meshloom` step, after `meshloom_extra`, so turning that step off skips them
  too. A restore function returns warnings for `result.warnings`; one that
  raises is logged and becomes `section_failed:<name>`, and the job goes on.
  The UI translates every warning code
  (`settings.fullRestore.warnings.<code>`, with the parameter as `{{value}}`):
  add your codes to the i18n files (fr and en) and to the code list in
  `frontend/src/test/backupRestoreFlow.test.tsx`.
- **`requires`.** `register_backup_section(name, ..., requires=["users"])`:
  the named sections are restored first, in the same restore. When one of them
  is missing from the file or failed, this section is skipped
  (`section_skipped:<name>`). A cycle is refused at registration. A required
  name nobody registered counts as missing.

## Restore rules

These hold for every section; a restore function must keep them:

1. **A missing section leaves its data untouched.** A section absent from the
   file is not restored at all; its restore function is never called with an
   empty value.
2. **`secrets_omitted` never clears a stored secret.** A section exported
   without its secrets says so with `secrets_omitted: true`; its restore keeps
   the stored secret wherever the file has none, and never writes an empty one.
3. **An unknown section is ignored** with an INFO log line naming it: a file
   from a newer version restores what this version knows.
4. **Identity seeds are never exported.** The per-service instance identity
   seeds (share, Remote: `app/services/instance_identity.py` once it lands) are
   never in a file, even though the radio private key is. Name their columns
   `app_settings.<service>_seed` and mark them `excluded` in `BACKUP_COVERAGE`:
   the export reads every `app_settings.*_seed` value and refuses a document
   (v1 or v2, sections included) that contains one, with a 500
   `backup_identity_seed_leak`. The seeds are regenerated after a restore,
   through the hook below.
5. **Secrets in `data` are plain JSON.** Unlike `identity`, `wifi` and
   `meshloom_extra.vapid`, a section's `data` is not masked in other dumps.
   Keep secrets out of log lines and exception messages; prefer hashes, or
   `secrets_omitted`.

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

Consumers: Links & Files (T10/T45, share identity) and Access (T38/T65, Remote
identity and accounts).

## Tests

`tests/test_backup_sections.py` (fixtures in `tests/fixtures/backup/`: a real
4.18.2 export and a #156 full backup) covers the round trips, a registered
section, an unknown one, `requires`, failures, the seed guard and both hook
paths. Unregister what a test registers: the test file's autouse fixture saves
and restores the registry.
