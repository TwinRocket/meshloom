# Hostile plugin corpus

One directory per misbehaving plugin, each pinned to one guarantee of
`docs/dev/plugins-security.md` (the case table there lists them all, and
`test_every_case_is_well_formed_and_documented` fails on a case it does not
name). `tests/test_plugin_corpus.py` runs them; a case must fail when the guard
it pins is removed.

A case is `case.json` plus, for the `runner` and `host` stages, a minimal plugin:
`manifest.json` holding only `{"entry": "plugin.py"}` (all the runner reads) and
`plugin.py`. Keep it to the one attempt it describes, with the `description`
saying what it tries and what must happen.

## `case.json`

- `description` (required).
- `stage`: `runner` (default), `host` or `install`.
- `platforms`: e.g. `["linux"]`; the case is skipped elsewhere.

### `runner` (a real runner child, this test as the server)

Run under `none`, `bwrap` and `setuid`, wherever this machine offers them. The
parent passes a polluted environment and an inherited descriptor on purpose.
`events` is a list of `{"params": ..., "expect": ...}` sent as `event.message`.
In `params`, `$DB` (a `0600` database file in a `0711` data dir), `$DATA_DIR`,
`$APP_ROOT` and `$SERVER_PID` (the test process) are replaced. After each event
the database and the app tree must be untouched and the child must answer
`event.health`. `expect` is one of:

- `{"error_type": "<exception class>"}`: the handler's JSON-RPC error. For
  `PermissionError` the message must come from the runner (`plugin sandbox:`),
  so a refusal the OS happens to make does not hide a removed guard.
- `{"error_code": <int>}`: a JSON-RPC error code (the runner's own refusals).
- `{"result": <value>}`.
- `{"check": "<name>"}`: a check written in the test (`hook_frame`, `environ`,
  `inherited_fds`; `spawn_refused` and `thread_refused`: refused by the runner,
  or the fork or clone failed: `EPERM` from the seccomp filter, `EAGAIN` from
  `RLIMIT_NPROC` 0).

An event may add `"open_path": {"note": "...", "python_below": "3.12",
"nproc_bypass": true, "closed_by": ["bwrap"], "os_check": "<name>"}` for a
documented path the runner does not close in-process (`python_below` optional;
`nproc_bypass`: the path is open only where neither the seccomp filter nor
`RLIMIT_NPROC` 0 holds, that is off Linux; a root child is covered by the filter,
MSHL-323). Where the expectation fails, a level in
`closed_by` must pass `os_check` (`exec_escape`: a program with no hook, run in
the same box, cannot read a `0600` database, `/proc/<server>/environ` or write
the app root, and under bwrap cannot read `app/` or connect), and any other
level reports `xfail`. When the path gets closed, the case passes.

### `host` (the real supervisor, `app/plugins/host.py`)

Run under each level too (the isolation selection is forced for the test). The
instance is created with `permissions` (granted as declared, default none) and
the host `budgets` (`Budgets` fields; the test starts from short ones). `events`
are `{"hook": "message", "params": ..., "expect": ...}` sent as test events, and
`expect` is `{"result": <value>}` (with `"same_process": true` when the child
must not have been replaced) or `{"killed": "<last_error code>"}` (the child is
killed, reaped and replaced). After each event a `{"ping": true}` event must
answer `"pong"`, so every host plugin answers it.

### `install` (archive validation, level-independent)

`archive` lists entries added to a package the validator accepts: `{"name",
"text"}`, `{"name", "hex"}`, `{"name", "symlink": "<target>"}` or `{"name",
"zip": [<entries>]}` (a nested archive). `expect` is `{"install_error": "<code>"}`;
`extract_package` must refuse it and leave nothing behind.

## Adding a case

1. Create the directory with `case.json` (and `manifest.json`, `plugin.py`).
2. Add a row to the case table in `docs/dev/plugins-security.md`, "Tests".
3. Run `uv run pytest tests/test_plugin_corpus.py -k <name>`, then remove the
   guard it pins and check it fails (on Linux with bubblewrap, and as root with
   the `meshloom-plugin` account for `setuid`; CI runs both).

`measure_footprint.py` is not a case: it measures what children cost on a target
(`docs/dev/plugins-security.md`, "Measurements").
