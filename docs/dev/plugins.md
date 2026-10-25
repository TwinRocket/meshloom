# Plugins

Status (5.0.0): **format, validators, data model, child runtime, SDK, supervisor,
capability API v1 with the global send budgets and loop guards, OS isolation,
`http.fetch`, the REST API, bundled and local plugins with the Settings → Extensions
UI, the global pause, and the conversion of the 4.18 bots** (MSHL-175 to MSHL-192,
MSHL-216, MSHL-337, MSHL-339; isolation: `docs/dev/plugins-security.md`, "OS
isolation"). The server starts one supervised child per enabled instance, isolated by
the OS, and answers its requests (see "Capabilities"). Not in 5.0.0: the online Store
(index, personalised repositories, updates from a store; the consent
`app_settings.store_online`, off by default, and its egress gate exist since MSHL-194, see
"Store egress" in `app/AGENTS.md`) and Windows (no plugins). Two bundled packages ship ("Bundled
plugins"). Like every doc here, this
file may be wrong: the code in `app/plugins/` and `meshloom_plugin/` wins.

## Package and manifest

A plugin is a zip archive (`plugin.zip`), all files at the archive root:

```
manifest.json            required, <= 64 KiB
plugin.py                entry module (or a package directory, see `entry`)
README.md                required, <= 200 KiB; README.<lang>.md as listed in `readme`
LICENSE                  required
config.schema.json       optional, <= 64 KiB (subset of JSON Schema 2020-12)
i18n/<lang>.json         optional, <= 256 KiB each, JSON objects
vendor/                  optional, pure-Python dependencies
```

Code map:

| Module | Role |
|---|---|
| `app/plugins/manifest.py` | pydantic `Manifest` model, `parse_manifest()`, `load_manifest_json()`, `manifest_json_schema()` |
| `app/plugins/permissions.py` | permission registry: names, parameter models, caps, `http` host rules, reserved names |
| `app/plugins/config_schema.py` | `check_config_schema()` (keyword subset pre-check, then the `jsonschema` metaschema), `validate_config_values()` |
| `app/plugins/package.py` | `validate_package()` (nothing is extracted), `extract_package()` (validates first, files 0644, directories 0755; on any failure the destination is emptied again, so no partial tree stays) |
| `app/plugins/scripts.py` | UTS #39 mixed-script check for names, on embedded Unicode tables |
| `app/plugins/licenses.py` | `ACCEPTED_LICENSES`, the one SPDX list (decision Q13; also for `vendor/`) |
| `app/plugins/errors.py` | `PluginValidationError` carrying `PluginIssue(code, path, message)` |
| `app/data/plugin_icons.json` | closed icon list (lucide names); `frontend/src/test/pluginIcons.test.ts` checks every name exists in `lucide-react` |

### Manifest rules

Everything is validated by the pydantic models in strict mode (no coercion: `"api": "1"`,
`"manifest": true` or `"stores_message_content": "yes"` are refused, as the exported
schema refuses them); unknown fields are refused. The model may be stricter than the schema
(it also refuses `1.0` for `api`), never looser: `tests/test_plugin_manifest.py` feeds the
vectors and a list of type probes to both. The rules
JSON Schema cannot express are enforced in `Manifest._cross_rules` and exercised by
the golden vectors.

- `manifest: 1`; `id` matches `^[a-z0-9]+(-[a-z0-9]+)*\.[a-z0-9]+(-[a-z0-9]+)*$`
  (`<publisher>.<name>`). `meshloom.*` and `local.*` are reserved: the caller says
  who submits through `origin` (`third_party` by default, `bundled`, `local`), and
  an origin must use its own namespace.
- Texts (`name`, `summary`, author names) refuse C0/C1 controls, private-use and surrogate
  code points, and an explicit list of invisible or direction-changing characters: bidi
  controls (U+061C, U+200E/F, U+202A-202E, U+2066-2069), U+200B, U+2060, U+FEFF, soft hyphen
  U+00AD, CGJ U+034F, the Hangul fillers (U+115F, U+1160, U+3164, U+FFA0), Khmer U+17B4/17B5,
  braille blank U+2800, the invisible operators and deprecated format controls. Tag characters
  (U+E0000-E007F) are accepted only inside one of the three RGI emoji tag sequences
  (U+1F3F4, the tags of `gbeng`, `gbsct` or `gbwls`, U+E007F: the England, Scotland and
  Wales flags), never as hidden text: any other tag text, even shaped like a flag, is
  refused (`name-hidden-tag-flag` vector). ZWNJ/ZWJ, variation selectors and emoji ZWJ sequences are
  allowed, and unassigned code points are not refused, so validity never depends on the
  Python version.
- `name` and author names also pass the UTS #39 "Highly Restrictive" check, word by word
  (`app/plugins/scripts.py`, after NFKC folding). UTS #39 applies the level to the whole
  string; here it runs per word on purpose, so `Mesh Сеть` (a Latin word, then a Cyrillic
  word) and `Погода GPS-трекер` pass: this is not full UTS #39 conformance, it refuses a
  look-alike inside a word. Per word: a word is single-script, or fits Latin +
  Han + Hiragana + Katakana, Latin + Han + Bopomofo or Latin + Han + Hangul. Characters use
  Script_Extensions (U+30FC is Hiragana and Katakana), Common and Inherited characters never
  count, so `ラーメン`, `佐々木` and `Tokyo東京` pass while `Meshlօom`
  (Armenian), `MeshloᎠm` (Cherokee) and a Cyrillic or Greek look-alike fail. The tables are
  `app/data/unicode_scripts.json` (Unicode 16.0.0 Scripts, ScriptExtensions and
  PropertyValueAliases), generated by `scripts/data/refresh_unicode_scripts.py`; the stdlib has
  no Script property. Regenerate it, never edit it by hand. The misleading-word check runs on
  the NFKC-folded name.
- `name` and `summary` are language maps (`en` required, codes like `fr` or `pt-BR`).
  Outside `meshloom.*` a name in any language must not contain `meshloom`,
  `official` or `officiel` (case-insensitive).
- `version` is strict SemVer (the semver.org expression) and must also be orderable
  by `packaging`: `1.0.0-rc.1` passes, `1.0.0-alpha.beta` does not.
- `license` is in `ACCEPTED_LICENSES`; `homepage` and `authors[].url` are absolute
  `https:` URLs without credentials.
- `icon` (optional) is in `plugin_icons.json`; `categories` are `bot`, `utility`,
  `monitoring`, `integration`, `fun` (1 to 3).
- `runtime.python` and `compat.meshloom` are PEP 440 specifier sets (`packaging`).
- `entry` is `path[:Class]`, a relative path to a `.py` module or to a package
  directory (`<path>/__init__.py`).
- `hooks` is a subset of `message`, `raw_packet`, `contact`, `telemetry`, `health`,
  `timer`. The host filters events by permission, so the manifest does not tie them.
- `config_schema`, `i18n` and `readme` values are safe relative paths (no `..`, no
  absolute path); `readme` requires `en`.

### Permissions

| Permission | Parameters |
|---|---|
| `messages.read` | `channels` `public` (default) or `all`; `direct`, `outgoing` (booleans, default false) |
| `messages.send` | `targets` `reply` (default), `scope`, `any`; `per_hour` 1 to 60; `per_day` 1 to 500 |
| `raw_packets.read`, `contacts.read`, `channels.read`, `telemetry.read`, `storage`, `timer` | none (`{}`) |
| `http` | at least one of `hosts` and `lan_hosts` (`http_hosts_required`), no entry in both lists and no duplicate (`lan_host_overlap`, `host_duplicate`; `lan_host_overlap` is defence in depth: the per-host validators already refuse any name valid in both lists, so no golden vector can reach it, `tests/test_plugin_manifest.py` calls the check directly). `hosts`: up to 20 exact lowercase hostnames or **public** IP literals (classified with the egress guard's range tables in `app/net/egress_guard.py`, never `ipaddress.is_global`); never a wildcard, a range, a URL or a port. A hostname's last label starts with a letter (so `127.1`, `2130706433` and `0x7f.1` are refused); an IP literal is canonical (no scope id, lowercase IPv6, not IPv4-mapped), and every range the guard refuses is refused (`host_private_ip`: private, loopback, link-local, multicast, NAT64 or 6to4 around a private IPv4, `169.254.169.254`, documentation ranges). Names `localhost`, `*.localhost`, `*.local`, `*.lan`, `*.home.arpa`, `*.internal` and single-label hosts are refused too (`host_private_name`). `meshloom.app` and its subdomains are refused unless the id is `meshloom.*`. `lan_hosts`: up to 10 devices on the user's network, each `host` or `host:port` (`[v6]:port` for IPv6: `fd00::1:80` is a full IPv6 address without a port, write `[fd00::1]:80`): a lowercase **local** name (a single label, or ending in `.local`, `.lan`, `.home.arpa` or `.internal`; any other dotted name is a public DNS name whose owner picks the address, refused with `lan_host_public_name`) or a canonical IP literal in a LAN range (`10/8`, `172.16/12`, `192.168/16`, CGNAT/Tailscale `100.64/10`, `fc00::/7`); never a wildcard or range (`host_wildcard`, `host_range`), `localhost`/`*.localhost`, a metadata name or a non-LAN literal (`lan_host_not_lan`; link-local stays refused), in the Home Assistant add-on only never a literal of the supervisor network `172.30.32.0/23` or `fd0c:ac1e:2100::/48` (`lan_host_supervisor`, MSHL-350; the add-on is the declared `MESHLOOM_INSTALL_KIND=addon`, so no golden vector carries it), never a bad port, scope id or uppercase (`lan_host_invalid`). Without a port only the scheme's default port is allowed |

Reserved and refused with `permission_reserved`: `notifications.send`, `feed.post`,
`ui.panel`, `channels.manage`, `contacts.manage`. Any other name is `extra_forbidden`.

`hosts` stays public-only; a device on the LAN is reachable only through `lan_hosts`
(MSHL-282), which the consent lists by name, one line per device.

### Config schema

A subset of JSON Schema 2020-12: a flat `object` whose properties are `string`,
`integer`, `number`, `boolean` or `array` of strings, with `enum`, `default`,
bounds, `title` and `description` (a string or a `{"en": ..., "fr": ...}` map).
Secrets are `"writeOnly": true` on a string (no default, no enum); the shared
`SchemaForm` keys on it. No `$ref`, `pattern` (ReDoS), `oneOf` or nesting: any other
keyword is `config_schema_keyword_not_allowed`. Value errors never echo the value. A malformed schema (wrong JSON types for `type`,
`required`, `enum`, ...) is a `PluginValidationError`, never a `TypeError`.

### Archive rules (`validate_package`)

Checked from the central directory and bounded reads, before any extraction:

- 5 MiB compressed, 20 MiB uncompressed (declared sizes, nested archives included),
  2000 entries (nested ones included).
- Entry names: no absolute path, drive letter, backslash, control or invisible character,
  `.` or `..` segment, empty segment. Duplicates and file-as-directory clashes are decided on
  the NFC-normalised, case-folded name (`café` in NFC and NFD, `Foo` and `foo/bar`), so no
  partial tree is written on a case- or normalisation-insensitive filesystem.
- No symlink or special file, no executable bit, no encryption, only stored and
  deflated entries.
- No native code anywhere, including `vendor/*.dist-info` and nested zips: names
  ending `.so` (also `.so.N`), `.pyd`, `.dll`, `.dylib`, and files starting with the
  ELF or Mach-O magic or with a real PE header (`MZ`, an `e_lfanew` inside the file and `PE\0\0` there; a text file starting with `MZ` is fine). Every file is
  read once (its CRC is checked, a corrupt archive is `package_bad_zip`). A file is opened
  as a nested zip when it starts with a zip header **or** carries an end-of-central-directory
  record near its tail (zipfile opens archives with junk prepended); nested zips (up to 3
  levels) are scanned the same way and share the size and entry budgets. gzip, bzip2, xz and
  tar content is refused (`package_nested_archive`) rather than scanned. This is best
  effort: it cannot catch native code hidden in a custom container; the sandbox and the
  index review are the real boundary.
- No compiled bytecode (`.pyc`, `.pyo`, `__pycache__`): ship the source, reviewers read it.
- `manifest.json`, `README.md` and `LICENSE` exist; every file the manifest references
  exists; READMEs, i18n files and the config schema respect their size caps and parse.

Structural failures stop at the first problem; manifest and config-schema problems
list every issue. Not checked yet: the licences of files under `vendor/` (index CI,
T16), Python syntax of the entry.

### Error codes

`PluginIssue.code` is stable. It is a pydantic error type (`missing`,
`extra_forbidden`, `literal_error`, `string_pattern_mismatch`, `less_than_equal`,
`too_short`) or one of ours: `id_namespace_reserved`, `id_namespace_mismatch`,
`name_misleading`, `license_not_accepted`, `url_not_https`, `icon_unknown`,
`permission_reserved`, `host_wildcard`, `host_range`, `host_invalid`,
`http_host_reserved`, `host_private_ip`, `name_homoglyph`, `text_invalid`, `i18n_en_required`, `path_unsafe`, `entry_invalid`,
`specifier_invalid`, `version_not_comparable`, `manifest_too_large`,
`manifest_not_json`, `config_schema_*`, `package_*`. The vectors list the ones the
manifest can produce.

## Data model

Migration 103 adds three tables (columns in `docs/dev/database.md`) and
`app_settings.store_online` (the Store egress consent, off; in `AppSettings` and
`PATCH /api/settings` since MSHL-194, read by `app/plugins/store_egress.py`). It was additive; migration 122 (MSHL-192) then converted the bots of
`fanout_configs` into local instances (see "Legacy bots"), so no release has bots without
a runtime.

### Instances

One row of `plugins` is one **instance**. A plugin (`plugin_id`, the manifest id)
can be installed several times ("Weather for #paris", "Weather for #lyon"):

- **Shared per `plugin_id`:** the code directory, so one version; updating a plugin
  updates all its instances.
- **Per instance (`instance_id`, a uuid):** `label`, `enabled`, `config`, the
  secrets, `scope`, `granted_permissions`, `quotas`, `auto_update`, `status`,
  `last_error`, `attention`, `sort_order`, and the KV store (`plugin_kv`).
- A local plugin (`kind = local`) is its own instance; its code is in `source`.

`status` is free text for now (`installed` by default); the runtime owns its states
(`disabled`, `starting`, `running`, `error`, `crash_loop`, `needs_attention`,
`incompatible`, `revoked`). `attention` is JSON or NULL: the caller's `type`
(`migrated`, `review_permissions`, ...) plus, for a scan, the `reasons` and
`http_hosts` of `AttentionReport.to_json()`.

`app/repository/plugins.py`: `PluginRepository` (CRUD, an in-memory copy of the rows
like `repository/fanout.py`) and `PluginKvRepository` (`get`, `set`, `delete`, `keys`,
`usage_bytes`, values stored as JSON). `delete(instance_id, keep_data=True)` keeps the
KV rows ("uninstall, keep data"); the secrets always go. Refusals are a
`PluginStoreError` with a stable `code` and the field or key names, never a value.

KV bounds (constants in `app/repository/plugins.py`; the `storage` capability
enforces the same quota, the repository is the backstop):

| Constant | Value | Refusal |
|---|---|---|
| `KV_MAX_KEY_BYTES` | 256 UTF-8 bytes; non-empty, no control, format, surrogate, private-use or unassigned character (Unicode category `C*`) | `kv_key_invalid` |
| `KV_MAX_VALUE_BYTES` | 256 KiB of JSON (UTF-8) per entry; NaN and non-JSON values refused (`kv_value_invalid`) | `kv_value_too_large` |
| `KV_QUOTA_BYTES` | 5 MiB per instance, keys plus values, a rewritten key counted at its new size | `kv_quota_exceeded` |

### Secrets

Which fields are secret comes from the instance's config schema, stored in
`plugins.config_schema` (the plugin's validated `config.schema.json`), never from the
caller: every `"writeOnly": true` field passed in `config` goes to `plugin_secrets`
and never sits in `plugins.config`. They follow the write-only pattern of
`app/secret_fields.py`:

- Every read (`get`, `get_all`, `get_enabled`, `get_by_plugin_id`, the result of
  `create` and `update`) shows a stored secret as `********` (`REDACTED`) in
  `config` and names it in `secret_fields`.
- `config_for_init(instance_id)` is the only accessor with the clear values, for
  the runtime's `plugin.init`. Never return it from a route, log it or export it.
- Writes go through `config`: a secret field omitted, `None` or `********` keeps the
  stored value, `""` clears it, any other string replaces it (a non-string is
  `secret_not_string`). `********` for a field with no stored value (after a clear,
  or at creation) is refused with `secret_required`: the mask is never stored as a
  value, so it never reaches `plugin.init`.
- `update(config_schema=...)` (a plugin update) moves the plain values of fields
  that became secret into `plugin_secrets` and deletes the stored secrets the new
  schema no longer declares.
- At rest: in clear for now, like the repeater passwords. Once the Notifications
  key file and its helper (`app/secret_store.py`, Notifications T32, AES-256-GCM)
  exist, the repository encrypts through that helper, never a cipher of its own.
- Never exported: no JSON backup section carries them (`BACKUP_COVERAGE` marks
  `plugin_secrets` excluded), and the SQLite download empties the table
  (`SNAPSHOT_EMPTIED_TABLES`). The JSON backup's `plugins` section
  (`app/services/backup_plugins.py`, `docs/dev/backup.md`, "Plugins") carries the
  instances with `secret_omitted` names only; `plugin_kv` is never exported.

### Attention scan

`app/plugins/attention.py` `scan_source(source)` parses a local source with `ast`
(nothing is imported or run) and returns an `AttentionReport`: `reasons`
(`code`, `line`, `detail`, `informative`), `http_hosts` and `truncated`, plus
`lan_hosts` and `lan_truncated` (LAN suggestions, below). Reused by
the switch (T9) and `dev check` (T4d, "Checking a plugin"). The reasons are a review aid, not an
exhaustive list of what the sandbox refuses.

It never raises on any `str`, so one hostile bot cannot abort a batch scan: a
source over `MAX_SOURCE_BYTES` (1 MiB) is `source_too_large` without parsing; a
`RecursionError` or `MemoryError` from the parser (deep `a+a+...`, `.b` or `()`
chains) or more than `MAX_AST_NODES` (200 000) nodes is `too_complex`; the walk
uses an explicit stack, never recursion. Each of these is the only reason then.

| Code | Detail | Informative |
|---|---|---|
| `import_not_allowed` | the module (`urllib.request`, `requests`) | no |
| `threading` | `threading`, `_thread`, `concurrent.*`, `multiprocessing` | no |
| `open_call`, `exec_call`, `eval_call`, `compile_call`, `import_call` (`__import__`) | none | no |
| `process_call` | `os.system`, `os.popen`, `os.fork`, `os.kill`, `os.exec*`, `os.spawn*`, `os.posix_spawn*`, any `subprocess.*` call | no |
| `long_literal` | a string literal over 156 UTF-8 bytes (a DM's limit); docstrings excluded | no |
| `print_call` | none (goes to the journal) | yes |
| `module_state` | a module-level mutable (`dict`, `list`, `set`, ...) or `_bot_globals` (lost on restart) | yes |
| `global_statement` | the names | yes |
| `syntax_error`, `source_too_large`, `too_complex` | none (the only reason then) | no |

The import allowlist and denylist are those of `meshloom_plugin/policy.py`
(MSHL-176), imported, so the scan and the runner cannot drift. A
local source has no `vendor/`, so any other module is flagged. Relative imports
(the plugin's own package) are allowed.

`http_hosts` are the hosts of the `https://` literals, lowercased, without port or
credentials, deduplicated in order, at most 20, kept only when the `http`
permission accepts them (`check_host`; private names and IPs and `meshloom.app`
are dropped). They **prefill** the `http` permission: the user still consents.
Extraction is bounded and linear: at most `MAX_URL_SCAN_BYTES` (256 KiB) of literal
text searched and `MAX_URL_MATCHES` (1000) URLs examined, stopping at 20 hosts;
`truncated` is true when a bound cut the search, so more hosts may exist.

`lan_hosts` (MSHL-342) are `LanHostSuggestion`s (`host`, `line`, `service_port`) from
the `http://` and `https://` literals whose authority `check_lan_host` accepts: the
entry as `lan_hosts` stores it (`name`, `name:port`, an IP, `[v6]:port`), lowercased,
userinfo (`user:pass@`) dropped, the scheme's default port dropped (an entry without
port allows only that port), deduplicated with the first line that names it. Names
of the Meshloom machine are dropped without any lookup: `socket.gethostname()` (full
and short, each also with `.local`), `meshloom`, `meshloom.local`, `localhost*`,
`ip6-localhost`, `ip6-loopback`, `host.docker.internal`, `gateway.docker.internal`,
`host.containers.internal`, `supervisor`, and `meshloom.app` names. A URL that runs to
the end of an f-string (or t-string) part followed by a placeholder (`f"http://nas{n}"`)
or to a byte-budget cut is skipped. `service_port` is true for 8123 (Home Assistant),
5051 (a radio proxy) and 8000 (a Meshloom app). Own cap: `MAX_LAN_SUGGESTIONS`
(`HTTP_MAX_LAN_HOSTS`, 10) with its own `lan_truncated`, so LAN and public literals
never crowd each other out; the byte and URL budgets are shared, and a budget cut sets
both flags. They are **suggestions only**: not in `to_json()` (nothing prefilled from
a conversion), and the local editor adds one per click through `PATCH
granted_permissions` (audited `plugin.config_changed`). The routes
(`_lan_suggestions` in `app/routers/plugins.py`) also drop an IP literal that
`is_self_address` reports (a bind probe; nothing is resolved or sent). The
fetch-time egress guard stays the boundary. Store and bundled plugins get none: their
`lan_hosts` come from the manifest and the grant only narrows them.

## Checking a plugin

```bash
uv run python -m meshloom_plugin.dev check path/to/plugin [--json] [--origin third_party|bundled|local] [--strict]
```

Run from a source checkout (`meshloom_plugin.dev` imports `app.plugins`, which is
not shipped to the child). `check` runs, in order, and reports every finding:

1. **Package.** The directory is zipped in memory (files at the root, mode 0644,
   `__pycache__` left out, a symlink refused) and goes through
   `validate_package`, so manifest, archive and config-schema failures carry the
   server's codes and messages. `--origin` picks the id-namespace rule (`bundled`
   for `meshloom.*`, `local` for `local.*`).
2. **Static scan.** `scan_source` on every `.py` outside `vendor/`. Findings are
   warnings (informative ones are marked `info`); `--strict` makes the blocking
   ones errors. A plugin's own absolute imports of its other modules are flagged
   by the scan, which cannot tell them from third-party modules.
3. **Dry run**, only when the package is valid: the real runner with
   `isolation: none` (the OS isolation is not exercised), `plugin.init`, one
   synthetic `event.message` carrying `test: true`, then `plugin.shutdown`. The
   plugin's calls to the host are captured and answered locally (`messages.send`
   gets `{"captured": true}`, everything else `null`); nothing is sent. Timeouts:
   20 s for `plugin.init`, 30 s for the event. An error answer, a runner start
   error (`entry_load_failed`...), a timeout or a plugin that exits is an error;
   the tail of the plugin's stderr is printed on failure. The `plugin.init`
   payload is provisional until the supervisor (MSHL-179) fixes it.

Exit status 0 passed, 1 failed, 2 usage (a path that is not a directory included). `--json` prints `{ok, plugin, errors,
warnings, dry_run: {isolation, ran, calls, stderr}}` (`isolation` is always `"none (development only)"`; the text output says so too; `informative` is set on informative findings). Importing `meshloom_plugin.dev` imports
neither FastAPI nor the database (`app/message_limits.py` imports FastAPI lazily
for that reason); `tests/test_plugin_dev_check.py` asserts it and feeds every
invalid manifest vector through `check`. `tests/plugin_dev/echo/` is the reference
valid plugin. `run --replay` and `pack` come later (T27).

## Contract

`docs/dev/plugins/manifest.schema.json` is the contract shared with
`TwinRocket/meshloom-plugins` (its CI copies the file and checks equality), and
`docs/dev/plugins/vectors/manifest.json` holds the golden vectors: valid manifests and
failing ones with the expected `(code, path)` pairs. The index CI and the site
verify the same vectors. The JSON Schema alone cannot express the cross rules above, so
a consumer must also run the vectors.

Both files are generated:

```bash
uv run python scripts/build/export_plugin_contract.py          # regenerate
uv run python scripts/build/export_plugin_contract.py --check  # fail if stale
```

`tests/test_plugin_manifest.py` fails when either file is stale, and every vector is
run through the validator when generating, so a vector cannot record a behaviour the
code does not have. A change to either file is a contract change: put it in a
dedicated `CONTRACTS:` commit and call it out in the PR. A pydantic upgrade that
changes the generated schema is such a change too. Archive vectors are not JSON;
`tests/test_plugin_package.py` builds its archives in code.

## Runner (child process)

One process per plugin instance runs `meshloom_plugin/`, a top-level package next
to `app/` (standard library only: the child never sees site-packages). It ships
wherever `app/` ships: the `Dockerfile` (`COPY meshloom_plugin/`), the deb/rpm
staging (`copy_project_files` in `scripts/build/build_nfpm_packages.sh`), the
armhf build image (`Dockerfile.nfpm-armhf`) and the tag overlay in
`.github/workflows/nfpm-armhf.yml`, and the upgrade-matrix image;
`tests/test_plugin_packaging.py` reads those files. Security model, what the
hooks block and what they cannot: `docs/dev/plugins-security.md`.

| Module | Role |
|---|---|
| `app/plugins/launch.py` | server side of the launch contract: `runner_command()`, `prepare_work_dir()`, `popen_kwargs()`, `runner_env()`, `runner_params()`, `bootstrap_source()` |
| `meshloom_plugin/runner.py` | the child: start-up stages, audit hook, import guard, JSON-RPC loop |
| `meshloom_plugin/rpc.py` | NDJSON framing and JSON-RPC 2.0 helpers, used by both ends |
| `meshloom_plugin/policy.py` | import allowlist and denylist, kept environment keys, default limits, handler concurrency |
| `meshloom_plugin/dev.py` | author tooling, not child code: `python -m meshloom_plugin.dev check <dir>` (see "Checking a plugin") |
| `meshloom_plugin/__init__.py` | `host()`: the RPC channel to the server; lazy exports `Plugin`, `Message`, `HostError` |
| `meshloom_plugin/sdk.py` | the SDK (below) |
| `meshloom_plugin/compat.py` | the legacy bot shim (`legacy_bot`) that runs converted bots, and their call plans (below) |
| `meshloom_plugin/errors.py` | exceptions plugin code may catch (`HostError`); never in `runner.py`, which runs as `__main__`, so a plugin importing `meshloom_plugin.runner` would get a second copy whose classes never match |

### Launch contract

```
<python> -I -S -B -X utf8 -c "<bootstrap>"
```

The project is not an installed package and `-I` (which implies `-E -P -s`) drops
the cwd, the script directory and `PYTHONPATH` from `sys.path`, so
`python -m meshloom_plugin.runner` cannot work. The fixed bootstrap sets
`sys.path = [app_root] + stdlib` (site-packages entries filtered out) and runs
`runpy.run_module("meshloom_plugin.runner", run_name="__main__")`. `-S` (not in the
cadrage's first draft) skips `site` too: no site-packages on the path and no
`.pth` file executed before the runner freezes the process. `<python>` is
`sys.executable`. The supervisor creates the instance's private work directory
with the isolation backend's `prepare_work_dir()` (`launch.prepare_work_dir()`:
0700, owned by the server user, not a symlink; handed to the instance's own
uid under the `setuid` level) and spawns with the backend's `launch()`, which wraps
`subprocess.Popen(runner_command(), stderr=..., **popen_kwargs(work_dir))`
(bwrap prefixes the command, `setuid` adds `user`, `group`, `extra_groups=[]`
and `umask`):
`cwd=work_dir` (the only writable place), `env=runner_env()` (`LANG=C.UTF-8`,
`PYTHONHASHSEED=random`), stdin and stdout as pipes (the RPC stream),
`close_fds=True` with no `pass_fds`, `start_new_session=True` (a process group
to kill). stderr is the plugin's log.

The first stdin line is one JSON object: `plugin_dir` (absolute), `work_dir`
(absolute), `instance_id`, `isolation` (one of `policy.ISOLATION_LEVELS`: `none`,
`setuid`, `bwrap`) and `limits` (keys of `policy.DEFAULT_LIMITS`, positive
integers). The runner refuses to start (`params_invalid`) when its cwd is not
`work_dir`, when `work_dir` is `/`, or when `work_dir` and `plugin_dir` nest
either way: a supervisor that forgot `cwd=` would otherwise make its own cwd,
possibly the data directory, writable through the hook. The runner reads `entry` from
`plugin_dir/manifest.json` (already validated at install). What follows that
line on stdin is the RPC stream.

### Start-up order

1. Close every descriptor from 3 up (whatever the parent passed), read the
   parameters line with raw reads.
2. `os.dup` the RPC descriptors and keep them private; fd 0 becomes `/dev/null`,
   fd 1 and `sys.stdout` become stderr: `print()` or `os.write(1, ...)` in a
   handler lands in the log and never corrupts the stream.
3. Clear `os.environ` but `policy.ENV_KEYS`; hard rlimits (soft = hard):
   `RLIMIT_NOFILE` 64, `RLIMIT_FSIZE` 50 MiB, `RLIMIT_CORE` 0, `RLIMIT_CPU`
   3600 s, `RLIMIT_AS` 256 MiB on Linux only (macOS does not enforce it),
   `RLIMIT_NPROC` 0 on Linux at every level (no thread, no process: it closes
   what the hook cannot see; root ignores it); `_thread.stack_size(256 KiB)`;
   `PR_SET_NO_NEW_PRIVS` and `PR_SET_DUMPABLE` 0 on Linux (through `ctypes`,
   purged afterwards; dumpable 0 keeps a same-uid sibling out of
   `/proc/<pid>/mem`); then, on Linux, inherited capabilities are dropped and a
   seccomp filter refuses `clone`, `fork` and `exec` (`meshloom_plugin/seccomp.py`,
   the guard that holds for a root child; a root child that cannot install it
   does not start, see `plugins-security.md`). Then
   `asyncio` is imported: it loads `socket`, `ssl` and `subprocess` at module
   level, so it cannot come after the hook, and step 5 removes them again.
4. Install the audit hook, then replace `sys.addaudithook` with a stub that
   raises `PermissionError` (CPython alone swallows the hook's refusal).
5. Purge `sys.modules` of every module that is neither loaded at interpreter
   start-up nor allowlisted (`importlib`, `runpy`, `pkgutil` and `zipimport` are
   purged although loaded at start-up), clear the dicts of the denied modules and
   replace every reference another module holds to them
   (`asyncio.base_events.socket`) with a stub that raises `PermissionError`.
   Install the import guard: a `sys.meta_path` finder and a
   `builtins.__import__` wrapper.
6. `sys.path = [plugin_dir, plugin_dir/vendor, stdlib]`, create the event loop
   (a selector loop whose self-pipe is an `os.pipe`, so the child holds no
   socket), set `meshloom_plugin.host()`, import `entry` and instantiate its
   class when it names one, then serve.

Any failure before serving is a typed line on stderr,
`{"runner_error": "<code>", "message": "..."}`, and exit code 3. Codes:
`params_invalid`, `platform_unsupported` (no `resource` module: Windows),
`limits_failed`, `audit_hook_failed`, `entry_invalid`, `entry_load_failed` (the
entry raised, including a refused import). Exit 2 is a framing violation,
exit 0 a `plugin.shutdown` or stdin closed.

### Imports

`policy.ALLOWED_IMPORTS` lists the standard-library modules plugin code may
import (with their submodules); any name that is not a stdlib module is the
plugin's own code or `vendor/` and is allowed. `policy.DENIED_MODULES` (`socket`,
`ssl`, `subprocess`, `_posixsubprocess`, `ctypes`, `multiprocessing`, `sqlite3`,
`http`, `urllib.request`, `logging.handlers`...) is refused to every importer
once the process is frozen. Plugin code is a frame whose file is under
`plugin_dir` or that was compiled at run time (`exec`); the standard library
importing what it needs is not checked against the allowlist. A refused import
raises `PermissionError`, not `ImportError`: a vendored library that does
`try: import ssl except ImportError:` needs `except (ImportError, PermissionError)`.

### Protocol (child side)

JSON-RPC 2.0, one object per line (`\n`), UTF-8, at most
`rpc.MAX_MESSAGE_BYTES` (100 KiB) per line, arrays and objects nested at most
`rpc.MAX_NESTING_DEPTH` (64) deep (checked before parsing); no batches. Only the
two `http.fetch` lines may be longer, each in one direction (`rpc.LargeLines`):
the request a child sends, up to `rpc.HTTP_FETCH_REQUEST_BYTES` (384 KiB, for a
256 KiB body), which the server's reader (`rpc.HOST_INBOUND`, the decoder's
default) accepts and nothing else that long; and an answer the child reads, up to
`rpc.HTTP_FETCH_RESULT_BYTES` (1.5 MiB, for 1 MiB in base64), which the child's
reader (`rpc.CHILD_INBOUND`) accepts for a response only (the server builds a
line that long only for an `http.fetch` answer: `rpc.result_limit(method)`). An
over-cap line is parsed once to check its kind; a partial line is refused once
it passes the larger bound. A line over the cap ends the child (exit 2); a line that is not JSON, nested too deep or
not a JSON-RPC 2.0 object gets an error with `id: null` (`-32700`, `-32600`) and
the child keeps serving. The server decodes child output with the same
`rpc.decode`.

| Server → child | Plugin attribute called with `params` |
|---|---|
| `plugin.init` | `on_init` |
| `event.message`, `event.raw_packet`, `event.contact`, `event.telemetry`, `event.health`, `event.timer` | `on_message`, `on_raw_packet`, ... |
| `config.changed` | `on_config_changed` |
| `plugin.shutdown` | `on_shutdown`, then the child answers and exits 0 |
| `event.cancel {"id": X}` | cancels the task of request X (answer `true` when it existed) |

The target is the entry module, or an instance of the class `entry` names
(`plugin.py:Weather`). A missing attribute answers `null`; a handler may be sync
or async. At most `policy.MAX_CONCURRENT_HANDLERS` (8) handlers run at once, the
others wait; past 128 pending requests the child answers `-32001` (overloaded).
Errors: `-32601` unknown method, `-32000` the handler raised (`data:
{"code": "handler_error", "type": "<exception class>"}`, traceback in stderr),
`-32800` cancelled, `-32603` a result that is not JSON or is over the cap.

Child → server: `await meshloom_plugin.host().call(method, params)` sends a
request with an integer id and returns its `result`, or raises
`meshloom_plugin.errors.HostError` carrying the `error` object;
`host().notify(method, params)` sends a notification. The methods themselves
(`messages.send`, `storage.*`...) and their checks are the host's (see
"Capabilities"). The SDK wraps `host()`.

`host().call_sync(method, params, timeout=25)` is the blocking variant, for
synchronous plugin code that cannot await (the legacy shim): it writes the
request, then reads the stream itself until the matching answer. Threads are
denied, so the whole loop is blocked meanwhile and the other handlers wait.
Lines read meanwhile are held back and handled when the call ends: at most
`MAX_DEFERRED_LINES` (128) lines and `MAX_DEFERRED_BYTES` (1 MiB), past which an
event is refused with `-32001` and a notification dropped. Answers to other
`call()`s are delivered at once. It raises `HostError` on an error answer, on a
timeout (`error.code == "timeout"`; a late answer is ignored) and when the stream
ends (`host_closed`; an oversized line still ends the child with exit 2), and
`asyncio.CancelledError` when the server cancels the calling handler with
`event.cancel`. It is not re-entrant. Tests: `tests/test_plugin_runner_sync.py`.

Tests: `tests/test_plugin_runner.py` (real subprocesses; standard library and
pytest only, so it also runs with `uv run --no-project --python 3.13 --with
pytest pytest --noconftest tests/test_plugin_runner.py`), the hostile corpus in
`tests/plugin_corpus/` (one directory per hostile plugin with its `case.json`,
run by `tests/test_plugin_corpus.py` through the runner, the supervisor or the
archive validator under each isolation level this machine offers), the RPC fuzz
`tests/test_plugin_rpc_fuzz.py`, `tests/test_plugin_isolation.py` and
`tests/test_plugin_packaging.py` (docs/dev/plugins-security.md, "Tests").

## SDK (`meshloom_plugin.sdk`)

```python
from meshloom_plugin import Message, Plugin

class Weather(Plugin):
    async def on_message(self, msg: Message) -> None:
        if msg.text.startswith("!weather"):
            data = await self.http.get_json(f"https://api.open-meteo.com/v1/forecast?{self.config['query']}")
            await self.reply(msg, self.fit(f"{data['current']['temperature_2m']} °C"))
```

A plugin subclasses `Plugin` (entry `plugin.py:Weather`) and overrides the handlers
it subscribed to: `on_message(msg: Message)` (the runner's raw parameters are
converted to a `Message`), `on_raw_packet`, `on_contact`, `on_telemetry`,
`on_health`, `on_timer` (raw parameters), `on_config_changed(params)` (the base
class replaces `self.config`; call `super()` when you override it), plus the
lifecycle `on_start()` (once `plugin.init` was stored) and `on_shutdown(params)`.
Every helper is a JSON-RPC request awaited until its result; the server checks
permissions and quotas and answers a typed error, raised as
`meshloom_plugin.errors.HostError` (`sdk.error_code(exc)` returns the typed code,
`error.data.code`).

| Helper | RPC | Notes |
|---|---|---|
| `self.reply(msg, text, region=...)` | `messages.send {to, text, region?, event_id?}` | to the sender for a DM, else to the message's channel. `region` left out = the channel default, `None` = unscoped, a name = scoped; ignored for DMs. Forwards the message's `event_id`, so the host ties the send to its event (a test event's sends are captured) |
| `self.send(text, channel_key=\|contact_key=, region=..., event_id=None)` | `messages.send` | exactly one target; `event_id` as for `reply()` |
| `self.fit(text)` / `sdk.fit(text, limit)` | none | cut to `limits.message_bytes` of `plugin.init` (default 156) on a UTF-8 boundary |
| `self.storage.get/set/delete/list` | `storage.get {key}`, `storage.set {key, value}`, `storage.delete {key}`, `storage.list {prefix}` | JSON values; `get` returns the value or `null` (a stored `null` reads as absent) |
| `self.contacts.list(limit, offset)` / `self.contacts.get(key)` | `contacts.list {limit, offset}`, `contacts.get {key}` | dicts (see "Capabilities"); `get` returns `None` when unknown |
| `self.channels.list()` | `channels.list {}` | dicts `key`, `name`, `is_public` |
| `self.http.fetch/get_json/post_json` | `http.fetch {url, method, headers?, body?, timeout, verify_tls?}` | `get_json`/`post_json` raise `HttpStatusError` from status 400, without the query string |
| `await self.timer(seconds)` | `timer.set {seconds}` | answered later by `on_timer` |
| `self.log(level, message)` | notification `log {level, message}` | stderr when there is no host |
| `await self.set_status(summary)` | `status.set {summary}` | |

What the supervisor (MSHL-179) must provide, as the SDK uses it:

- `plugin.init` params: `api` (int), `instance` (object), `config` (object, secret
  values included), `lang`, `granted` (the effective permission names), `limits`
  (`message_bytes` int, others free), `isolation`. Missing fields are tolerated.
- `limits.message_bytes` is the **smallest** text allowance the instance may meet,
  because `self.fit()` knows no target: a direct message allows 156 bytes, but a
  channel message allows `156 - len(sender name) - 2` (`app/message_limits.py`,
  `channel_text_allowance`). The supervisor should send the channel-effective
  allowance (radio name known, else the 10-byte fallback: 144), not 156; otherwise
  `fit()` output on a channel reply is refused with `message_too_long`. A plugin
  that needs the exact bound reads `params.limit` of that refusal.
- `event.message` params: the fields of the 4.18 bots' call (`sender_name`, `sender_key`,
  `message_text` without the `"Name: "` prefix, `is_dm`, `channel_key`,
  `channel_name`, `sender_timestamp`, `path`, `path_bytes_per_hop`, `packet_hash`,
  `region`, `scoped`, `is_outgoing`) plus `conversation_key`, `is_public_channel`,
  `received_at`. The 2 s settle delay stays server-side, before the event.
- `messages.send` takes an explicit target; `region` absent keeps the channel's
  default. A result of `http.fetch` is `{status, headers, body, encoding?, truncated}`,
  `body` a string (UTF-8 text, or base64 when `encoding` is `"base64"`).
- Result shapes the SDK expects: `messages.send` and `status.set` and
  `storage.set`/`storage.delete`: any JSON (ignored; `null` is fine);
  `timer.set`: ignored; `storage.get`: the stored JSON value, or `null` when absent;
  `storage.list`: an array of key strings (anything else reads as empty).
- **Fail closed.** The SDK checks nothing against `granted`: the guarantee is the
  server's. A method outside the instance's granted permissions gets
  `permission_denied` (before any side effect, quota or network access), a method the
  server does not know gets `-32601`, and invalid params get `invalid_params`.
- **No head-of-line blocking.** The server must answer a child's requests while that
  child still has events outstanding (read the child's stream concurrently with
  waiting for event results). `call_sync` blocks the child's loop until the answer
  arrives; a server that handles one child message at a time behind a pending event
  would stall that child until the call times out.
- **`http.fetch` timeout.** The server clamps `timeout` to at most 10 s (cadrage §4.3)
  whatever the child sends: it must stay below the 25 s `call_sync` timeout and the 30 s
  event deadline. The shim and the SDK pass the value through unchanged.
- **`http.fetch` answers.** `HttpResponse.truncated` is true when the server cut the
  body at 1 MiB. While `call_sync` waits, an answer to another `call()` read before
  the sync answer is delivered at once; events and notifications go to the deferral
  buffer, and so do the complete lines that follow the sync answer in the same read
  (any kind, answers included, so each is under the 64 KiB read size). The buffer is
  bounded by `MAX_DEFERRED_LINES` and `MAX_DEFERRED_BYTES` (1 MiB); past it an event
  is refused (`-32001`) and any other line dropped. A partial line stays in the
  decoder and is read after the call, as usual.
- **`http.fetch` bodies.** The server takes a body of at most 256 KiB (UTF-8 bytes,
  `HTTP_BODY_MAX_BYTES`), and the request line, JSON escaping included, must fit
  `rpc.HTTP_FETCH_REQUEST_BYTES` (384 KiB): quotes and backslashes count twice,
  control characters six times. `fetch_params` (the SDK and the shim) checks the
  encoded line and raises `HostError` with `data.code` `http_body_too_large`
  (`params.limit`, `params.encoded_limit`) before anything is sent.
- Refusals carry `error.data.code` (`permission_denied`, `host_not_allowed` (T7),
  `quota_exceeded`, `target_not_allowed`, `target_unknown`, `region_unknown`,
  `message_too_long`, `send_failed`, `invalid_params`, plus `https_required` and
  `fetch_failed` (T7); "Capabilities", "Error codes"). `http_body_too_large` comes from
  the SDK itself, never from the server.

Tests: `tests/test_plugin_sdk.py` (the test is the server, over a real child).

## Bundled plugins

`meshloom_plugin/official/<dir>/` holds the packages shipped with the release
(`meshloom_plugin/official/README.md`), installed offline from Settings → Extensions →
Installed ("Add a bundled extension") through `POST /plugins/install` and
`installer.install_bundled`. They are ordinary packages: validated with `origin =
"bundled"` (id `meshloom.*`), the same sandbox, quotas and host guards as any third
party plugin, no special permission, not listed in any index. The directory is copied
along with the rest of `meshloom_plugin/` by the Dockerfile and the packages. A package's
`version` changes only with its files; `install_bundled` is a no-op for the same
version and replaces `<data>/plugins/<id>/` for another (the old tree is moved aside
until the swap succeeds, so a failure keeps it; if putting it back fails too, it stays in
`_staging/previous-<hex>`). At start, before the bundled update, `installer.sweep_staging()`
(MSHL-347) clears `<data>/plugins/_staging/`: it never follows a symlink (a symlinked
`_staging` is left alone, a symlinked entry is unlinked), deletes the leftovers of a crashed
install, and deletes a `previous-*` tree only when its manifest names a valid plugin id
whose tree is in place; otherwise it keeps it and logs a warning. `app/plugins/bundled_update.py`
(MSHL-337) re-installs the instances after a Meshloom upgrade: `lifecycle.start()` calls
`update_bundled()` before `load_from_db()`, so no child runs during the swap. For each
`kind = bundled` row whose packaged version is newer (PEP 440) it installs the code once
per plugin (`swap_bundled`, old tree kept aside), then rewrites `version`, `manifest` and
`config_schema` of all its rows in one transaction (`PluginRepository.update_many`); if
that write fails the old tree goes back (`restore_previous`). If putting it back fails too,
the new code runs under the old rows: the host refuses to start such an instance
(`PluginManager.code_matches`: the installed `manifest.json`, read without following a
symlink, must carry the row's id and version; status `error`, `last_error =
plugin_code_mismatch` in health and the API, 409 on start) until the next start, where
`swap_bundled` finds the tree already new and the row write is retried. Config, secrets, scope,
quotas, label and KV are kept. Permissions the new manifest adds or changes (`added`;
any different normalised parameters, narrower included; a removed one only loses its
grant), or a widened privacy declaration (a new `sends_to_third_parties` host, or
`stores_message_content` turning true: `privacy_widened`), set `needs_attention` /
`attention.type = review_permissions` (`added`, `privacy_widened`, `from_version`,
`to_version`; the UI says "privacy changed" when `privacy_widened` is the only reason, and
names both when `added` is not empty); the changed names drop their grant while `enabled` stays, so the
admin's usual acceptance (`PATCH` with `granted_permissions`, see "Instances") starts it;
otherwise grants and status are untouched (a newly required, unset config field switches
the instance off with `config_required`, like an install). Same or older version: no-op;
the row carries the new version afterwards, so a second start does nothing. An invalid
package (`list_bundled` skips it), a failed install or a failed row write is logged and
the old version stays, startup goes on. Skipped: the host lock (`lifecycle.start()`
returns first), `reinstall`, `revoked` and `incompatible` rows. Each update writes a
`plugin.updated` audit row (system actor, versions, `held`); tests:
`tests/test_bundled_update.py`. `tests/test_official_plugins.py` runs `dev check` on both
(`--origin bundled`), tests the rules, installs them through the API and runs the
two-host simulation.

- `meshloom.echo` (`echo/plugin.py`) is the documented reference: a `Plugin` subclass,
  `on_message`, `self.reply`. `!pling` answers `Plong!`. It replaced the example
  of the bot editor (`DEFAULT_BOT_CODE`), which is gone with the bots.
- `meshloom.auto-responder` (`auto_responder/`) is the no-code option. The config schema
  subset allows arrays of strings only, so each rule is one string,
  `trigger[,option=value]: text => reply`: triggers `keyword` (whole words), `prefix`
  and `regex`; options `channel=#name|dm` and `cooldown=30..86400`; the reply takes
  `{sender}`, `{channel}`, `{text}` (substituted, never `format`ted). Other settings:
  `case_sensitive`, `answer_direct_messages`, `cooldown_seconds` (default 120),
  `max_replies_per_hour` (default 20, below the instance's 30). The first rule that
  matches decides, even when its cooldown keeps it silent. Cooldowns are per rule and
  per sender and live in memory; the hourly stamps are kept in `storage` so a restart
  keeps the cap. Regex safety, since `re` has no timeout: a structural whitelist on the
  `re._parser` tree (`check_regex`), applied when the rule is read. Refused: more than
  100 characters, back-references and lookarounds, a repeat above 5 whose body holds a
  repeat or a branch, more than 2 such repeats in all; the compile time is capped at
  100 ms and the pattern only sees the first 200 characters of the message. What is
  accepted is at worst quadratic or cubic on 200 characters
  (`test_accepted_patterns_are_cheap_on_the_longest_input`, under 100 ms; the
  catastrophic shapes are in `CATASTROPHIC`). A rule that does not parse is skipped and logged with its
  line number. `language` is the standard instance field (the status line is localised).
  The host guards (`SendGuard`) stay the authority: the plugin's own limits only keep it
  below them, and the two-host simulation in the tests shows two auto-responders
  answering each other stop within a few messages.

## Legacy bots (`meshloom_plugin.compat`)

The Python bot of 4.18.x no longer exists as a fanout type or a runtime: MSHL-192 removed
`app/fanout/bot.py`, `app/fanout/bot_exec.py` and every bot branch of the fanout router and
manager, so no user code is `exec`'d inside the server process any more. What remains is
the shim and the conversion.

`legacy_bot(source)` returns a `Plugin` class that runs a 4.18.x `bot(...)` source:

```python
from meshloom_plugin.compat import legacy_bot
Bot = legacy_bot(open("bot.py", encoding="utf-8").read())   # manifest entry "compat.py:Bot"
```

**Migration 122** (`app/migrations/_122_bots_to_plugins.py`) turns each `fanout_configs` row
of type `bot` into a `plugins` row: kind `local`, `plugin_id` `local.<uuid4>`, label the bot's
name, source the bot code unchanged, `enabled` and the `messages` scope copied, and a
generated manifest (`entry: "compat.py:Bot"`, hook `message`, permissions `messages.read`
`{channels: all, direct: true, outgoing: true}` and `messages.send` `{targets: reply}`). The
row starts as `needs_attention` with the attention
`{"type": "review_permissions", "converted_bot": true, ...}`: it never starts until an
administrator accepts the permissions, like any `review_permissions` attention. If
the retired bot-lock environment variable is true when the migration runs (its one and
only read), every converted instance is stored with `enabled = 0`. The originals are copied
to `fanout_configs_bots_archive` (excluded from backups and `/api/debug`; purge planned in
5.2.0) and deleted from `fanout_configs`. At each start the attention scan
(`app/plugins/attention.py`) runs on the converted sources not scanned yet and adds its
reasons to the attention JSON. The plugin host writes a converted instance as `bot.py` (the
user's code, editable as the local source) next to a fixed `compat.py` wrapper around
`legacy_bot`.

`/api/health` carries `converted_bots` (`{"converted": n, "attention": m}`, `null` once
acknowledged) and `POST /api/plugins/converted-bots/acknowledge` (admin) clears it. A backup
entry of type `bot` restores the same way, disabled, with the attention
`{"type": "restored", "converted_bot": true, "review_permissions": true}` (`docs/dev/backup.md`).
The call plans (`BotCallPlan`, `analyze_bot_signature`, `call_bot`) and the return
shapes (`coerce_result`: `None`, `str`, `list[str]`, `{"region", "message"}`) live
in `compat.py`; `tests/test_plugin_compat.py` covers them. A bot's replies are sent with
`messages.send` to the conversation of the message; a dict's `region` goes to the
host as given (the host normalises it), an absent one stays absent.

Differences, on purpose:

- `_bot_globals` belongs to the instance and is lost when the process restarts; bots
  no longer share it.
- No network without the `http` permission, no disk, no threads (the sandbox).
- Two new keyword arguments, delivered to a bot that names them or takes `**kwargs`
  (and, for a `**kwargs` bot, only by the shim): `http` (`get_json(url)`,
  `fetch(url, method=, headers=, body=, timeout=)`) and `storage` (`get`, `set`,
  `delete`, `list`). Both are synchronous: they use `call_sync`, so the bot blocks
  the child, and the other events of that instance wait. A refusal surfaces as a
  `PermissionError` naming the permission and the host (never the query string),
  which the shim logs like any exception of the bot.
- `print()` goes to the child's **stderr** (fd 1 is redirected there so it cannot
  corrupt the RPC stream), not to the RPC `log` method. The supervisor collects
  stderr as the instance's raw log; use `self.log()` or an exception to get a
  structured entry.
- A bot that does not load (syntax error, no callable `bot`, unsupported signature)
  is logged at `plugin.init` and at each message; it does not stop the instance.

## Supervisor

`app/plugins/host.py`: `PluginManager` (the `plugin_manager` singleton) owns one
`PluginInstance` per running instance, and each instance runs one
`PluginProcess` at a time. `app/plugins/lifecycle.py` is all the app sees:
`start()` after the database opens and the fanout modules load, `stop()` as a
shutdown step (never over 10 s), `health_fields()` for `/api/health`. Every
bound below is a field of `Budgets`, so tests run real children with shorter
ones.

### Gates

- **Host lock.** `MESHCORE_DISABLE_PLUGINS=true` (`Settings.disable_plugins`):
  nothing starts, `start()` raises `PluginHostError("plugins_disabled")`, health
  says `plugins_disabled_source: "env"`. The API refuses install, change and
  start (403 `plugins_disabled`) and allows reads, `DELETE` and the global pause;
  a backup restored under the lock brings its instances back disabled, and they
  stay inert since enabling them is refused (MSHL-339). It is not an alias of
  the old bot lock, retired: its setting is gone, a startup warning
  names `MESHCORE_DISABLE_PLUGINS` when it is still set (`app/services/legacy_env_settings.py`),
  and migration 122 reads it once.
  `/api/health` still returns `bots_disabled` and `bots_disabled_source` for one release
  (deprecated), derived from this lock and the pause (`env` or `paused`; `until_restart`
  never appears).
- **Platform.** No `resource` module (Windows): the system is off,
  `plugins_disabled_source: "platform"`.
- **Global pause.** `app/plugins/pause.py` (MSHL-216, migration 120:
  `app_settings.plugins_paused`, `plugins_paused_at`, `plugins_paused_by`),
  loaded at boot before `start()`, sets `plugin_manager.paused`. `pause_all()`
  stops every running instance cleanly and keeps the `enabled` flags; while
  paused nothing starts (boot, install, enable, config change; a manual restart
  is 409 `plugins_paused`), `dispatch()` drops events instead of queueing them
  and `dispatch_test()` raises `plugins_paused`. `resume_all()` starts exactly the
  enabled instances again, staggered like the start-up. Converted bots are plain
  instances, so they pause with the rest. The
  pause survives a restart. Health says `plugins_disabled_source: "paused"` and
  `plugins_paused_since` only when no other gate applies: the host lock, the
  platform and the isolation failure take precedence. `start()` checks the pause
  again right before it registers and launches the child, with no await in
  between (MSHL-341): a start or enable in flight when the pause lands never
  launches, since `pause_all()` only stops registered instances. If the stored
  state cannot be read at boot, the pause fails closed: plugins are held
  as paused (`since` and `by` unknown), the log has the error and health adds
  `plugins_pause_unreadable: true`; an admin's resume (or pause) writes the state
  again.
- **Isolation setup.** When `isolation.startup()` fails at start (detection, or
  the dedicated uid's file model: a database left `0644` would be readable by the
  plugin uid), the system fails closed: `fail_closed()`, nothing starts until a
  restart, `start()` raises `plugins_disabled`, health says
  `plugins_disabled_source: "isolation"` and the log has the error.
- **Cap.** At most one running instance per 128 MB of `MemTotal` (sysconf
  elsewhere), clamped to 3..20 (`instance_cap()`); one more is
  `PluginCapacityError` (`code` `instance_cap_reached`, `cap`).
- **Start-up.** `load_from_db()` starts the enabled instances 500 ms apart, in a
  task the manager keeps (cancelled by `stop_all()`), and skips the ones whose
  stored status is `crash_loop`, `needs_attention`, `incompatible` or `revoked`
  until someone acts on them.

### One child

Spawned with `asyncio.create_subprocess_exec(launch.runner_command(),
**launch.popen_kwargs(work_dir))`: its own session, `close_fds`, the scrubbed
environment, a fresh `mkdtemp` work directory as cwd (removed when the child
ends), the parameters line on stdin. The code directory is
`<data>/plugins/<plugin_id>/` (shared by the instances of a plugin; `<data>` is
the database's directory); a local plugin's `source` and manifest are written to
`<data>/plugins/local/<instance_id>/` at each start. stderr goes to a ring of
1 MiB per instance, each line cut at 2 KiB (`[truncated]`), never to the server
log; the runner's typed start error is kept for `last_error`. Each child is
launched through the OS isolation backend (`isolation.current().backend`:
`prepare_plugin_dir()`, `prepare_work_dir()`, `launch()`); `isolation_level()`
is the level health and `plugin.init` report, and `lifecycle.start()` detects it
off the event loop (`isolation.startup()`) before the first child. Under bwrap a
child killed by a signal exits `128 + signal` (mapped back, so `SIGXCPU` is still
`cpu_rlimit`), and the CPU budget reads the whole process tree
(`read_proc_tree_cpu_seconds`: the runner is bwrap's grandchild).

`plugin.init` carries `api`, `instance` (`id`, `plugin_id`, `label`), `config`
(secrets in clear, from `config_for_init()`, plus the standard field `language`),
`lang` (the config's `language` when it is `en` or `fr`, else `en`), `granted`
(the names of the effective permissions, see "Capabilities"), `limits`
(`message_bytes`: the channel allowance of `app/message_limits.py` for the radio's
name at start, 144 when it is unknown; `message_bytes_limit()`), `scope`, `quotas`
(the effective send limits, `{"messages.send": {"per_hour", "per_day"}}`, empty
without `messages.send`) and `isolation` (the effective level), and must answer
within 20 s. Then the status is `running`.

### Events

- `FanoutManager.broadcast_*` call `plugin_manager.dispatch(hook, data)`
  (`message` after the pending-channel check, `raw_packet`, `contact`,
  `telemetry`, `health`). Only running instances whose manifest lists the hook
  get it, after the permission and scope filter ("Capabilities", "Reads").
  `event.message` keeps the 2 s settle of the old bots,
  then carries the fields the SDK reads (`app/plugins/events.py`, the 4.18 bots'
  derivation: `message_text` without the `"Name: "` prefix, `is_dm`,
  `channel_key`, `path_bytes_per_hop`, `scoped`... plus `conversation_key`,
  `is_public_channel`, `received_at`); the other hooks get the fanout dict as is.
- Each instance has an inbox of 100 events; a full inbox drops its oldest event,
  counts it (`dropped_events`) and logs a warning at most every 10 s with the
  total. At most `policy.MAX_CONCURRENT_HANDLERS` (8) events are in flight, so the
  queue builds on the host side and every in-flight event is running.
- Every event carries `event_id` (its JSON-RPC id) in its params; a test event
  also carries `test: true`. `dispatch_test()` returns `{"result", "sends",
  "truncated"}`: a child request `messages.send` naming the `event_id` of a test
  event (or carrying no `event_id` while that test event is the only one in
  flight) passes every check of the capability, then is captured there (answer
  `{"captured": true}`, no quota used) and never sent. At most
  `MAX_TEST_SENDS` (32) sends and `MAX_TEST_SEND_BYTES` (64 KiB of JSON) are
  captured; past either, the child gets `-32001` with `data.code`
  `test_sends_truncated` and `truncated` is true.
- An event gets 30 s, counted from the moment the host starts writing it to the
  child: every write to the child's stdin is bounded (`PluginProcess._write`, the
  event budget by default), so a child that does not read its input cannot hold
  the budget; past the bound its process group is killed (`stdin_blocked`). Once
  the budget is spent the host sends `event.cancel {"id"}`. A handler that stops
  within 5 s answers `-32800`: the instance keeps running and `last_error` says it
  timed out. Otherwise the process group is killed (`SIGKILL`).
- A handler error (`-32000`) sets `last_error` (`handler_error: event.<hook>
  raised <ExceptionType>`) and the instance keeps running; the child's own message
  goes to the server log only (scrubbed, capped, at most once per 10 s per instance).
- Child requests go to `capabilities.dispatch()` ("Capabilities");
  `PluginManager.host_methods` (name to coroutine) overrides it, for tests. A
  child notification `log` lands in the log ring; other notifications are
  ignored. An unknown method is `-32601`. A child has at most
  `MAX_CHILD_REQUESTS` (32) requests pending at the host; past that each one is
  answered `-32001` (overloaded) at once.

### Failures and restarts

A child that exits, is killed from outside, breaks the framing, misses the init
budget or is killed by a budget is a failure: everything pending (in flight or
queued) is dropped and counted, never replayed; the status is `error` with the
reason in `last_error`; the child is
restarted after 1, 2, 4 ... 60 s. A run that handled an event or lived 60 s resets
the consecutive count. Five consecutive failures: `crash_loop`, stored, no more
restarts. `restart()` builds a new supervisor, so its count starts at zero. A stop
during the backoff pause ends it at once.

CPU: on Linux the host samples `utime + stime + cutime + cstime` from
`/proc/<pid>/stat` of the child and every descendant (`/proc/<pid>/task/*/children`)
every 5 s and kills a child that used more than 20 CPU-seconds in the last 60 s. The
runner's hard `RLIMIT_CPU` (3600 s, `policy.DEFAULT_LIMITS`) is the portable
backstop: the kernel ends the child (`SIGXCPU`, `last_error` `cpu_rlimit`) and
the restart starts a new process, so the count starts again.

`last_error` is a stable code, a colon, then a short message the host writes:
`start_failed`, `start_refused` (with the runner's typed code), `init_timeout`,
`init_failed`, `event_timeout`, `event_cancel_ignored`, `handler_error`,
`stdin_blocked`, `rpc_framing` (the child wrote a line over the cap: killed),
`protocol_violation` (the child ended itself with exit 2: it refused a line
from the host, or its RPC read failed), `cpu_budget`, `cpu_rlimit`, `killed`, `crash`, `stopped`. Nothing the child wrote reaches it beyond an exception type
that is a short identifier; it is scrubbed of the instance's secret values, then
`sanitize_error()` drops control characters and filesystem paths and caps it at
`LAST_ERROR_MAX_CHARS` (200). Details (exception messages, the runner's message,
`OSError` texts with paths) go to the server log only, capped at 2000 characters.
The UI translates a `last_error` only when it is one of the bare host codes in
`HOST_LAST_ERROR_CODES` (`frontend/src/components/settings/extensions/pluginsShared.ts`,
today `plugin_code_mismatch`); any other value, snake_case or not, is shown as written,
so text from a plugin can never pass for a host message (MSHL-348).

### Statuses

`installed`, `disabled`, `starting`, `running`, `error`, `crash_loop`,
`needs_attention`, `incompatible`, `revoked`, and `paused`, an overlay of
`statuses()` while the global pause holds (never stored) on every enabled instance. Each change of status or
`last_error` is stored in `plugins.status`/`last_error`, sent to the WebSocket
clients as `plugin_status` (`instance_id`, `plugin_id`, `label`, `status`,
`last_error`, `dropped_events`, `restarts`, `summary`: the plugin's `status.set`
line, in memory only; `send_guard`: what the send guards refused since start,
`{total, by_scope, last}` or `{}`, blanked in health with `last_error` for a
principal without the `send` permission; `realtime=False`, so not to the
proxy or fanout) and triggers a `health` broadcast. `statuses()` covers every
instance of the repository cache (stored status, `disabled` when not enabled),
with live values for the running ones. Health carries `plugins_isolation`,
`plugins_disabled`, `plugins_disabled_source`, `plugins_paused_since`,
`plugins_pause_unreadable`, `plugins_purge_blocked` (under `setuid`: temp dirs a uid
purge could not list, or the lease file of another install holding the uid, MSHL-357;
the uid kept; empty below admin) and `plugin_statuses`; an
anonymous caller gets none of them.

Who sees errors: `plugin_status` is admin-only. `health` (REST and WebSocket)
keeps the statuses for every signed-in principal, but a principal below `admin`
gets each `last_error` as `null` and `send_guard` empty
(`lifecycle.without_plugin_errors()`, `PLUGIN_ERROR_PERMISSION`, applied by
`without_admin_details()` in `app/routers/health.py`).

### Shutdown

Stopping one instance (`stop()`, `restart()`, `remove()`) is bounded as a whole
by `Budgets.stop_timeout` (10 s): `plugin.shutdown` is written with a bounded
write (at most 1 s: a child whose stdin is full cannot hold it), a grace for the
exit, `SIGTERM` to the process group, then `SIGKILL` and the reap, which always
run (`finally`), and the supervision task is cancelled if it is still running.
`stop_all(timeout)` stops every instance at once within `timeout` (10 s from the
lifespan); any instance whose stop is still pending or failed is killed, its task
cancelled and its child reaped. A stopped instance's pending events are dropped.

Tests: `tests/test_plugin_host.py` (real children, granted what a converted bot
holds; fixture plugins echo, sleep,
ignore-cancel, crash, cpu-burn, ignore-shutdown, flood, deaf (never reads its
stdin), spammer, sender; the `/proc` CPU test
runs on Linux only).

## API (`app/routers/plugins.py`)

Prefix `/api/plugins`, tag `plugins`. Errors are `{detail: {code, message, params?}}`; `params` never
holds a config value or a secret. Reads need `send` (member and up: `config`, `source`, `scope`,
`quotas`, `secret_fields`, `last_error` and `attention` are emptied below admin); everything that installs,
changes, runs or reads the logs of an instance is `admin`. `kiosk` and `viewer` are refused
everywhere. While the host lock or the platform turns the system off, every write but `DELETE` and
`PUT /pause` is 403 `plugins_disabled` (`params.source`).

Audit (MSHL-215, `app/auth/audit.py`, `docs/dev/auth.md`): every successful or failed admin write
records the actor, the target `plugin:<instance_id>` and `{plugin_id, kind, fields?}`, where
`fields` lists the names of the changed fields only: never a config value, a secret or the source.
`POST /install` is `plugin.installed`, `POST /plugins` `plugin.created`, `PATCH` `plugin.enabled`,
`plugin.disabled`, `plugin.config_changed` (`config`, `scope`, `granted_permissions`, `quotas`,
`hooks`) and `plugin.changed` (the other fields: `label`), one row per kind, `PUT source` `plugin.updated`
(plus `source_bytes`, the size of the submitted source, never its text), `DELETE` `plugin.removed`. A
non-admin refused by the permission gate writes no row and changes nothing.

| Route | Role | Behaviour |
|---|---|---|
| `GET /plugins` | member | Instances with the live status (`summary`, `restarts`, `dropped_events`); secrets read `********`. |
| `GET /plugins/pause` | member | `{paused, since, by}`: the global pause, since when (Unix time) and the user name of who set it. |
| `PUT /plugins/pause` | admin | `{paused}`, idempotent: pauses or resumes every instance (see "Gates", global pause) and broadcasts `health`. A change of state is audited `plugins.paused` or `plugins.resumed` with the actor only (no target, no details); a repeat writes nothing. Allowed under the host lock. |
| `GET /plugins/bundled` | member | The valid packages of `meshloom_plugin/official/*` (`app/plugins/installer.py`, `BUNDLED_ROOT`): manifest texts, declared permissions, `privacy` declaration (shown on the consent screen), config schema, instance count: `meshloom.echo` and `meshloom.auto-responder` ("Bundled plugins"). |
| `POST /plugins/install` | admin | `{kind: "bundled", plugin_id, label?, granted_permissions, config?}`. 404 `plugin_not_bundled`; 400 `consent_required` (`params.missing`: the declared permissions the grant does not cover in full); 400 `package_invalid` (the bundled package fails validation), or 400 `lan_host_supervisor` when a `lan_hosts` entry is on the Home Assistant supervisor network (add-on only; MSHL-355); 400 `config_invalid` (`params.issues`: code and field); a required field missing creates the instance **disabled**, `status: needs_attention`, `attention: {type: "config_required", fields}`, no process. Otherwise it is enabled and started; 409 `instance_cap_reached` deletes it again. The code is copied (validated like an archive, origin `bundled`) to `<data>/plugins/<plugin_id>/` once per version, shared by the instances. Other kinds (`custom`, `store`) are M2. |
| `POST /plugins` | admin | A local plugin: `{label, hooks?, permissions?, template: "echo"\|"blank"}`; the manifest is built and validated (`origin=local`, id `local.<slug>-<hex>`), the permissions are declared and granted together, created disabled. 400 `manifest_invalid`, or 400 `lan_host_supervisor` when a `lan_hosts` entry is on the Home Assistant supervisor network (add-on only; MSHL-354). |
| `PATCH /plugins/{id}` | admin | `enabled` starts or stops the process (409 `config_required`, 409 `instance_cap_reached`: `enabled` is put back to false); a change of `config`, `scope`, `granted_permissions`, `quotas` or `label` restarts a running instance. `config` **replaces** the plain fields and merges the secrets (omitted, null or `********` keep, `""` clears; 400 `secret_required` for a mask with no stored value), validated against the stored schema (400 `config_invalid`; unknown fields refused; `language` is always allowed). `granted_permissions` must be a subset of the manifest (400 `permission_exceeds_manifest`); `quotas` only `messages.send.per_hour|per_day`, 1 to the caps 60 and 500 (400 `quota_invalid`); `scope` is `{messages: all\|none\|{channels, contacts}}` (400 `scope_invalid`). Completing a `config_required` config clears the attention. The required fields are re-checked from the stored config on every enable and restart (409 `config_required`); clearing one on a stopped instance sets `needs_attention`/`config_required`. A `revoked` or `incompatible` instance never starts (409 `plugin_revoked`/`plugin_incompatible`); `needs_attention` for another reason (`review_permissions`, `consent_required`...) is 409 `attention_required` until the request resends `granted_permissions` (the acknowledgement). For a `local` plugin `granted_permissions` rewrites the manifest's `permissions` and `hooks` its `hooks` (both revalidated, 400 `manifest_invalid`; `hooks` on another kind is 409 `not_local`), and the instance restarts when enabled. |
| `PUT /plugins/{id}/source` | admin | Local plugins only (409 `not_local`); 400 `source_too_large` (1 MiB), `source_syntax_error` (`params.line`); restarts when enabled. Answers the instance, the `scan_source` reasons (a review aid) and `http_hosts`, the `https://` hosts found in the code (the UI offers them as the `http` permission to accept), plus `lan_hosts` (`{host, line, service_port}`, at most 10) and `lan_hosts_truncated`, the local-network devices found in the code that the editor suggests one by one ("Attention scan"). Nothing is resolved or sent. |
| `POST /plugins/{id}/restart` | admin | New supervisor, failure count at zero (clears `crash_loop`). 409 when disabled or awaiting config, 409 `plugins_paused` while the global pause holds. |
| `POST /plugins/{id}/check` | admin | `dev check` stages 1 and 2 (package validation and static scan, `check_directory(dry_run=False)`; a local plugin: manifest and scan). **Nothing is executed**: the dry run uses `isolation: none`, so it stays a developer tool; `test` is the live check. A local plugin's answer also carries `http_hosts`, `lan_hosts` and `lan_hosts_truncated` (empty for other kinds). |
| `POST /plugins/{id}/test` | admin | `{text, channel?\|contact?}`: one `test: true` `event.message` through `dispatch_test` (no scope or `messages.read` filtering). The sends are captured by the host and never reach the radio send path; the answer is `{result, sends, truncated, logs}` with every string scrubbed of the instance's secrets (`last_error` and `summary` too). 409 `plugins_paused`, 409 `instance_not_running`, 404 `channel_not_found`/`contact_not_found`, 504 `test_timeout`. |
| `GET /plugins/{id}/logs?limit=` | admin | Tail of the stderr ring (default 200, 1 to 500), lines cut at 1000 characters, scrubbed of the instance's secrets and passed through `redact_text`. Not part of `/api/debug` (asserted). |
| `GET /plugins/{id}/diagnostic` | admin | Plugin id and version, kind, Meshloom and Python versions, isolation level, status, last error, restarts, effective permission names, the last 50 log lines. No config value. |
| `DELETE /plugins/{id}?keep_data=` | admin | Stops the process, deletes the row and secrets (and the KV unless `keep_data`), and a local plugin's directory; 204. |

There is no "disable until restart" route (the bots' own route was removed in MSHL-192); the global pause (`/plugins/pause`) replaces it. Typed codes are shown by the UI as `errors.<code>` (texts: Store T11a).

Every change of status the API makes is announced as `plugin_status` (admin-only,
`PluginManager.announce`); the payload is the supervisor's snapshot plus `attention`.
A disabled instance waiting for the user keeps `needs_attention`, `incompatible` or
`revoked` as its status (`ATTENTION_STATUSES`); other disabled instances read `disabled`.

## Capabilities

`app/plugins/capabilities/`: one module per capability (`messages.py`,
`storage.py`, `timer.py`, `contacts.py`, `channels.py`, `status.py`, `log.py`,
`http.py`),
each registering its methods in `base.REGISTRY` with the permission it needs;
`grants.py` holds the effective permissions and the read filters;
`app/plugins/quotas.py` the send quotas, the send guards and the global spacing. The supervisor's
only entry point is `capabilities.dispatch(instance, method, params, event)`.
Tests: `tests/test_plugin_capabilities.py` (real children through the host, the
radio send replaced by a recorder, quota windows on a controlled clock).

### Effective permissions

A permission is effective when the manifest declares it **and** the user granted
it (`plugins.granted_permissions`); its parameters are the narrower of the two
(`messages.read` `channels: all`, `direct` and `outgoing` need both;
`messages.send` `targets` is the lower of `reply` < `scope` < `any`, `per_hour` and
`per_day` the smaller; `http` the `hosts` and the `lan_hosts` both list, the permission
dropped when both lists end up empty). Parameters that fail the
registry's validation drop the permission. They are computed when the instance
starts: a changed grant takes effect at the next start (`reload()`).

### Methods

Every method is checked against the effective permissions **before** anything
else: a refusal has no side effect, uses no quota and touches no network.

| Method | Permission | Params | Result |
|---|---|---|---|
| `messages.send` | `messages.send` | `to` (`"reply"`, `{"channel_key": <32 hex>}` or `{"contact_key": <64 hex>}`), `text` (1 to 1024 characters), `region?`, `event_id?` | `{"sent": true, "message_id"}`, or `{"captured": true}` for a test event |
| `storage.get` | `storage` | `key` | the stored value, `null` when absent |
| `storage.set` | `storage` | `key`, `value` (JSON) | `null` |
| `storage.delete` | `storage` | `key` | `null` |
| `storage.list` | `storage` | `prefix?`, `after?`, `limit?` (1 to 200, default 200) | the keys in order, after `after` |
| `timer.set` | `timer` | `seconds` (10 to 86400) | `{"timer_id"}`; later `event.timer {timer_id, seconds}` |
| `contacts.list` | `contacts.read` | `limit?` (1 to 200, default 100), `offset?` | contacts |
| `contacts.get` | `contacts.read` | `key` (64 hex) | a contact or `null` |
| `channels.list` | `channels.read` | none | `{key, name, is_public}` per channel |
| `http.fetch` | `http` | `url`, `method?` (`GET` default, `POST`, `PUT`, `PATCH`, `DELETE`), `headers?` (at most 32 strings), `body?` (text, at most 256 KiB, and a request line of at most 384 KiB once escaped), `timeout?` (seconds, clamped to 10), `verify_tls?` (boolean, default true; false only for a `lan_hosts` device) | `{status, headers, body, encoding?, truncated}` |
| `status.set` | none | `summary` (`""` clears) | `null` |
| `log` | none | `level` (`debug`, `info`, `warning`, `error`), `message` | `null` (usually sent as a notification) |

- **`messages.send`**, checks in order: the params (`invalid_params`,
  `params.fields`), the permission's `targets` (`target_not_allowed`,
  `params.targets`), the destination exists and is not a pending channel
  (`target_unknown`, only under `targets: any`: under `reply` and `scope` an
  unknown destination is `target_not_allowed` like a forbidden one, so a plugin
  cannot probe which contacts exist), the region (`region_unknown`), the server byte rule of
  `app/message_limits.py` (`message_too_long`, `params.limit` and `params.bytes`),
  then the quotas (`quota_exceeded`, `params.scope` `plugin`, `params.period` `hour` or
  `day`, `params.limit`, `params.retry_after` in seconds), then the send guards
  (`quota_exceeded` with `params.scope` `sender_cooldown`, `quiet_window`, `destination` or
  `instance`; see "Quotas and spacing"). `targets`: `reply` allows
  only the conversation of the message event being handled (named by `event_id`,
  or the only event in flight; a timer has none); `scope` also allows the
  channels and contacts the instance may read ("Reads"); `any` every known
  channel and contact. `region` applies to channels: absent keeps the channel's
  default, `null` or `""` sends unscoped, a name must be in `known_regions`, the
  default flood scope or the channel's own override. A test event's send stops
  after the checks (captured, no quota used; the destination need not exist). A
  real send waits for the global spacing, re-checks the quotas, then goes
  through the REST send services (radio connected, pending channel, byte
  limit); a refusal there is `message_too_long` or `send_failed`
  (`params.status` for an HTTP refusal; any other error is `send_failed` without
  params, logged with its type). Only a send that went out counts; once it went
  out the spacing moves even if counting it fails (logged).
- **Own sends.** While a send runs, `capabilities.messages.SENDING_INSTANCE` holds
  the instance id; the fanout dispatch of the outgoing message it produces reads
  it. An outgoing message reaches an instance only with `messages.read`
  `outgoing: true`, and carries `own_send: true` for the instance that sent it.
- **Storage**: the instance's `plugin_kv` rows ("Data model"); a write past the
  5 MiB is `quota_exceeded` (`params.limit`), a bad key or a value over 256 KiB
  or not JSON is `invalid_params`. An RPC line is at most 100 KiB, so a value
  sent by a child is smaller than that.
- **Timer**: under 10 s (or a boolean, NaN) is `invalid_params` with
  `params.min`/`params.max`; at most 16 pending per instance (`quota_exceeded`).
  Timers belong to the running child: a restart or a stop drops them.
- **Contacts**: `public_key`, `name`, `type`, `last_seen`, `last_advert`, `lat`,
  `lon`, `favorite`; never routes or read state.
- **Channels**: a channel key is its secret. Public channels (the Public channel
  and the `#` channels whose key derives from their name) are listed; a private
  channel only when the instance has `messages.read` with `channels: all`.
  Pending channels are left out.
- **`http.fetch`** (`capabilities/http.py`), checks in order, all before any DNS
  lookup or connection: the params (`invalid_params`, `params.fields`: an `http(s)`
  URL of at most 2048 characters **without user info**, a known method, header
  names that are HTTP tokens and values without CR/LF, a body over 256 KiB with
  `params.limit`, a positive timeout, a boolean `verify_tls` that is false only
  when the URL's host is a `lan_hosts` device); the host is one of the effective
  `http.hosts` or `http.lan_hosts`, exactly, on the declared port or else the scheme's default port (`host_not_allowed`;
  `meshloom.app` and its subdomains only for a `meshloom.*` id, checked again here
  although the manifest already refuses them); HTTPS (`https_required`), except for a
  `lan_hosts` entry, which may use plain HTTP; then the quota, 60 requests per instance and clock hour, in
  memory (`quota_exceeded`, `params.period: "hour"`, `params.limit`,
  `params.retry_after`; a restart of Meshloom resets it, a restart of the child
  does not). A refused call uses no quota. Then the egress guard's fixed-host path
  (`follow_checked_redirects`, `app/net/AGENTS.md`) with the same hosts as the
  allowlist (`https` only for `hosts`; `lan_hosts` passed as `lan_hosts`): pinned
  resolution, private and reserved addresses refused for `hosts`, and for a
  `lan_hosts` name every resolved address must be in a LAN range (DNS-rebinding
  safe: a name that resolves to loopback, link-local, metadata, a mapped
  form or a public address is refused, and so is any address of the Meshloom host
  itself, `egress_self_address`, MSHL-330, and in the Home Assistant add-on any
  address of the supervisor network, `egress_supervisor_address`, MSHL-350)
  (`host_not_allowed`); `verify_tls: false`
  skips the certificate check (self-signed LAN device, MSHL-331) on the hops that
  reach a `lan_hosts` device only, so a redirect to a public host is verified; at most 3 redirects, each hop re-validated
  against both lists (`host_not_allowed`, or `https_required` for a hop to `http:`
  outside `lan_hosts`), credentials
  (`Authorization`, API key headers...) dropped once a hop leaves the origin,
  `Host`, `Content-Length`, `Transfer-Encoding`, `Cookie` and the hop-by-hop
  headers never sent, no cookie jar, `Accept-Encoding: identity`, a total of
  `timeout` seconds for the whole chain, and the answer cut at 1 MiB while it
  streams (`truncated: true`, the rest left unread). The server adds
  `User-Agent: Meshloom-Plugin` when the plugin sets none and never adds a
  credential of its own. Any other failure is `fetch_failed` with
  `params.reason` (`timeout`, `tls_error`, `connect_error`, `egress_unresolvable`,
  `egress_too_many_redirects`, `egress_response_encoding`...). An HTTP error
  status is an answer. The answer's `headers` are a fixed subset (content type,
  caching, `Location`, `Retry-After`, rate-limit headers; never `Set-Cookie`),
  each value cut at 1024 characters; `body` is the UTF-8 text, or base64 with
  `encoding: "base64"` when the bytes are not UTF-8 or the escaped text would be
  longer. Each call is logged at INFO with the instance, the method, the URL
  without user info, query or fragment (`sdk.safe_url`) and the status or the
  refusal code. `store_online` (the Store's consent) does not gate it: installed
  plugins keep working when the Store is off. Tests: `tests/test_plugin_http.py`.
- **Status**: control characters dropped, cut at 140 characters, kept in memory
  and sent in `plugin_status` (`summary`); at most one change per 2 s
  (`quota_exceeded`, `params.retry_after`).

### Reads

- `event.message` needs `messages.read`: a channel message only when the channel
  is public (as above) or with `channels: all`; a direct message only with
  `direct: true`; an outgoing message only with `outgoing: true`. Then the user's
  scope (`plugins.scope`, the fanout `ScopeSelector` shape): no `messages` entry
  means no restriction, else `all`, `none` or `{"channels": ..., "contacts": ...}`
  with the fanout filters (`all`, `none`, a key list, `{"except": [...]}`), keys
  compared case-insensitively.
- `event.raw_packet` needs `raw_packets.read` (and a scope whose `raw_packets` is
  not `none`); the packet keeps its decryption (`decrypted_info`) only when the
  instance could read that message, else it arrives with `decrypted: false`.
- `event.contact` needs `contacts.read`; `event.telemetry` and `event.health`
  need `telemetry.read`. `event.timer` comes from the instance's own timers.

### Quotas and spacing (`app/plugins/quotas.py`)

- Per instance: `per_hour` (default 30, cap 60) and `per_day` (default 200, cap
  500): the manifest's `messages.send` request or the default, capped, then
  lowered by the user's `plugins.quotas` `{"messages.send": {"per_hour",
  "per_day"}}` (never raised). Fixed windows (clock hour, UTC day) counted in
  `plugin_send_quota` (`PluginSendQuotaRepository`), so a restart keeps them;
  `SendQuota.clock` is injectable. Deleting an instance deletes its counters.
- Global spacing: `send_spacer` keeps 2 s (`SEND_SPACING_SECONDS`) between two
  automatic sends of every plugin instance, converted bots included; a failed send does not move it. The quota check runs once before waiting
  (a full quota is refused at once) and again inside the spacing slot.

### Send guards (`SendGuard`, `send_guard`)

Host-side, for every plugin instance together (converted bots are plugin instances and counted like any), checked after
the per-instance quotas, once before waiting for the spacing and again inside the slot, and
counted only for a send that went out. A refusal is `quota_exceeded` with `params.scope`:

| `scope` | Guard | Extra params |
|---|---|---|
| `plugin` | the instance's own `per_hour`/`per_day` (above) | `period`, `limit`, `retry_after` |
| `sender_cooldown` | one reply per 30 s (`SENDER_COOLDOWN_SECONDS`) to the same sender per plugin instance | `limit`, `retry_after` |
| `quiet_window` | no reply to a message received less than 5 s (`QUIET_WINDOW_SECONDS`) after a send of the same instance to the same destination | `limit` |
| `destination` | per clock hour and destination, all plugins together: 20 per channel (cap 60), 10 per contact (cap 30) | `period`, `limit`, `retry_after`, `kind` (`channel` or `contact`) |
| `instance` | per clock hour, all plugins together: 120 (cap 300) | `period`, `limit`, `retry_after` |

- **Order**: `sender_cooldown`, `quiet_window`, `destination`, `instance`, so the loop cuts
  are named before the budgets. The cooldown and the quiet window apply only to a send
  made while handling a message event that is not an outgoing one (a timer's send is not a
  reply). The sender is `key:<sender_key>` for a direct message, else `name:<sender_name>`
  (a channel message has no key); the quiet window compares the message's `received_at`
  (Unix seconds) with the last send, and uses the current time when the event has none.
  Several sends answering one event therefore need one message: the second is refused.
  The guards do not depend on the plugin tying a send to an event: a send with no usable
  event (several events in flight and no `event_id`, after the handler returned, a
  non-message `event_id`, a timer) counts as a reply to the most recent message the
  instance was handed from that destination (`SendGuard.note_inbound()`, called by the
  host when it enqueues a message event; memory only, a restart forgets it), but only
  while that message is under `UNTIED_REPLY_HORIZON_SECONDS` (30) old: an older one, or
  none, makes the send unsolicited (a timer, a daily reminder), subject only to the
  budgets and quotas. A timer firing more often than every 30 s into a destination with
  inbound traffic is therefore held to one send per 30 s (each counts as a reply; the
  destination quota bounds it anyway).
- **Fail closed**: the guards are counted before the send leaves, under the spacing lock;
  if they cannot be checked or counted nothing is sent and the child gets
  `quota_unavailable`. A send that then fails stays counted.
- **Persistence**: `plugin_send_guard` (hourly windows, last-reply and last-send stamps) and
  `plugin_send_limits` (migration 106; `PluginSendGuardRepository`), so a restart resets
  nothing. `SendGuard.clock` and `.store` are injectable.
- **Tuning**: `send_guard.set_limits(instance_per_hour=..., channel_per_hour=...,
  contact_per_hour=...)` lowers a limit (`None` restores the default); a value above the
  default (which is below the cap) or below 1 raises `ValueError`, and a stored value above
  the default reads as the default. The cooldown and the quiet window are not tunable. The
  Settings UI (T11a) calls `set_limits`; no route exists yet.
- **Visibility**: a refusal is logged at WARNING (once a minute per instance and scope) and
  counted in memory; `send_guard.hits(instance_id)` returns `{total, by_scope, last}` for
  the plugin status. The user-facing wording is the i18n slice `ap`
  (`plugins.sendGuards.*`, FR and EN).
- **Simulation**: `tests/test_plugin_quotas.py` runs two hosts, each with its own guard store
  and an echo plugin, on one fake channel on a controlled clock: the exchange stops after a
  few messages with the guards' reasons logged, and with the loop cuts off the destination
  quota still bounds it.

### Error codes

A refusal is a JSON-RPC error whose `data.code` is stable and whose `data.params`
carries the values above: `-32602` for `invalid_params`, `-32010` (`REFUSED`) for
`permission_denied` (`params.permission`), `quota_exceeded`,
`target_not_allowed`, `target_unknown`, `region_unknown`, `message_too_long`,
`send_failed`, `quota_unavailable`, `host_not_allowed`, `https_required` and `fetch_failed`
(`params.reason`). An unknown method is `-32601`; a test event's capture past its
bound is `-32001` (`test_sends_truncated`). The SDK's `error_code(exc)` returns
`data.code`. The user-facing wording of the permissions and of these codes is
the i18n slice `al` (`plugins.permissions.*`, `plugins.capabilityErrors.*`, FR and
EN; the `http.fetch` codes are in slice `aq`); `tests/test_plugin_capabilities.py` checks every registry permission has it.
