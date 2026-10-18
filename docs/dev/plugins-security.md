# Plugin sandbox: what the child runtime blocks, and what it cannot

The plugin child (`meshloom_plugin/runner.py`, launch and protocol in
`docs/dev/plugins.md`, "Runner") restricts itself before it loads plugin code.
**Those restrictions are defence in depth, not the security boundary.** Python
code running in a process can, by construction, undo or walk around anything
installed in that same process. What contains a hostile plugin is the OS
isolation around the child ("OS isolation" below: bubblewrap on deb/rpm, a
dedicated uid in Docker and the Home Assistant add-on) plus the capability API
(a plugin acts on the server only through RPC methods the server checks). On a
target where neither is available ("hooks only", level `none`), a hostile plugin
can read the database and the code; the level is logged with a warning and
reported in `/api/health` and `plugin.init`. Like every doc here, this file may
be wrong: the code wins.

## OS isolation (`app/plugins/isolation/`)

One backend per server process, detected once (`selection.current()`, logged:
INFO for the level, WARNING for each fallback and once for hooks only). Wire
values `bwrap`, `setuid`, `none` (`policy.ISOLATION_LEVELS`), in `/api/health`
(`plugins_isolation`) and `plugin.init` (`isolation`); `LEVEL_LABELS` names them
"isolated by bubblewrap", "dedicated uid", "hooks only".

| Server | Level | Fallback |
|---|---|---|
| Linux, root, account `meshloom-plugin` exists (Docker image, add-on) | `setuid` | — |
| Linux, root, no such account | `bwrap` if its self-test passes | `none` |
| Linux, not root (deb/rpm, RPi image, source, Docker with `MESHLOOM_RUN_AS_USER`) | `bwrap` if its self-test passes | `none` |
| not Linux (macOS, development) | `none` | — |

The bwrap self-test starts the interpreter in the exact sandbox a child gets
(not just `bwrap true`, which has no `true` to run in an empty root): missing
package, user namespaces refused (Docker's seccomp profile, Ubuntu 24.04's
AppArmor restriction without a bwrap profile, a sysctl) or a missing path all
end in `none` with the reason logged.

| | `bwrap` | `setuid` | `none` |
|---|---|---|---|
| Database and data directory | not mounted | `0600` files in a `0711` directory, owned by root | readable |
| Server code (`app/`) | not mounted | readable (world-readable code, no secret) | readable |
| `/proc/<server>/environ`, `mem` | not there (own pid namespace and `/proc`) | refused (root's process) | refused by `PR_SET_DUMPABLE` (non-root servers), readable when the server is root |
| Write outside the work dir | refused (read-only binds, `--remount-ro /`) | refused (root-owned tree) | only the hooks |
| Network | none (own network namespace, loopback down) | open (the hooks refuse sockets in-process) | the hooks only |
| A process it execs (`fork_exec` rebuilt) | stays in the sandbox | stays the uid, `RLIMIT_NPROC` | none of the above |
| SIGKILL of the group | kills everything (the sandbox's pid 1 goes, the kernel kills the namespace) | kills the group; a grandchild that called `setsid()` survives until the service or container stops | same as `setuid` |

### `bwrap` (`bwrap.py`)

`bwrap --unshare-all --unshare-net --die-with-parent --new-session --cap-drop ALL`,
read-only binds of `/usr` (and `/bin`, `/sbin`, `/lib*` as links or directories),
`/etc/ld.so.cache`, `/etc/localtime`, the interpreter's prefix when outside
`/usr` (python-build-standalone under `/opt/meshloom/python`), the runner package
`<app_root>/meshloom_plugin` and the plugin directory; `--proc /proc`,
`--dev /dev`, `--tmpfs /tmp`, the work dir bound read-write on top, then
`--remount-ro /` and `--chdir <work_dir>`. The command runs the real interpreter
(`realpath(sys.executable)`), not the venv link: `-I -S` reads nothing from the
venv. **Only the runner package is bound, not the app root** (the scoping draft
said `--ro-bind <app_root>`): a source checkout keeps its database in
`<app_root>/data` and the Docker image in `/app/data`. bwrap returns the child's
status, and `128 + signal` for a child killed by a signal. The package
`Recommends: bubblewrap` (deb and rpm); the Raspberry Pi images install it
(`dpkg -i` skips Recommends). The systemd unit sets no `RestrictNamespaces`, and
`NoNewPrivileges=yes` is fine: Debian, Raspberry Pi OS and Fedora ship bwrap
without the setuid bit.

### `setuid` (`setuid.py`, `permissions.py`)

`subprocess.Popen(user=, group=, extra_groups=[], umask=0o077)` with the
`meshloom-plugin` account (uid and gid pinned to 10002, `PLUGIN_UID` in the
`Dockerfile`, next to `MESHLOOM_RUN_AS_USER`'s 10001: Docker does not remap
users, so it is that uid on the host too, and the first system uid, 999, is
often a real host account; no shell, no home; an account that already exists is
kept; the add-on is `FROM` that image). The work dir is created by
the server and handed to the uid (`chown`, `0700`). The runner sets
`RLIMIT_NPROC` (64, counted over every task of the uid) and refuses to start
when told `setuid` while still root. At startup (`isolation.startup()`) the
file model is applied to the data directory: `0711` on the directory, `0600` on
each file in it, `0700` on each subdirectory but `plugins/`, whose tree is
`0755`/`0644`; the server's umask becomes `077`, so later files are born
private, and each plugin directory is opened up again (`0755`/`0644`) right
before its child starts. `/data/options.json` (the add-on's options) becomes
`0600`. Symbolic links are never followed.

### Known limits of the OS layer

- Under `setuid`, every instance shares the uid: one plugin can read another
  one's work dir, signal its process and use up the shared `RLIMIT_NPROC`. It
  cannot read or write another child's memory: the runner sets
  `PR_SET_DUMPABLE` 0 (every level), so `/proc/<pid>/mem` and `ptrace` of a
  sibling are refused (`test_a_sibling_under_the_same_uid_cannot_open_the_child_memory`).
  A uid per instance is a follow-up.
- Under `setuid` the network is open to an exec'd program (only the hooks
  refuse sockets in-process), **loopback included**: that reaches the radio
  proxy, which has no authentication and accepts SEND commands
  (`app/radio_proxy/policy.py`; only private key export is refused), so a plugin
  past its hooks can send messages beyond its own send quota. A network namespace
  is not available in default Docker: `unshare(CLONE_NEWNET)` needs
  `CAP_SYS_ADMIN`, which Docker's default root does not have; the alternatives
  are an extra capability, or nftables owner matching on the plugin uid with
  `CAP_NET_ADMIN` (follow-up, MSHL-191 measures it).
- Without a pid namespace (`setuid`, `none`), a process that left the session
  survives the group kill; the service's cgroup (`KillMode=control-group`) or
  the container's end still stops it.
- Docker with `MESHLOOM_RUN_AS_USER`: no `CAP_SETUID`, no user namespaces, so
  `none` (cadrage Q12 proposes keeping `CAP_SETUID`/`CAP_SETGID`).
- No seccomp filter of our own (no `pyseccomp`): bwrap and the uid are the
  boundary; a filter is a defence-in-depth study (T31).

## What the runner does

| Measure | What it gives |
|---|---|
| Descriptors from 3 up closed at start; the RPC descriptors duplicated, fd 0 = `/dev/null`, fd 1 = stderr | nothing inherited from the server; `print()` cannot corrupt the RPC stream |
| The cwd must be the `work_dir` the supervisor named (`launch.prepare_work_dir()`, 0700), not `/`, not nested with the plugin directory; otherwise no start | the only writable place is a private directory, never the server's cwd or the plugin's code |
| `os.environ` cleared but `LANG` and `PYTHONHASHSEED` (and the supervisor passes only those) | no server secret in the environment |
| Hard rlimits: address space (Linux), open files, file size, core 0, CPU; `RLIMIT_NPROC` under the dedicated uid only; small thread stacks | memory bombs fail with `MemoryError`, no core dump, a runaway loop is killed at the CPU cap |
| `PR_SET_NO_NEW_PRIVS` (Linux) | no privilege gain through a setuid binary, even if a process were spawned |
| `PR_SET_DUMPABLE` 0 (Linux, before any plugin code) | `execve` (and the uid drop) made the child dumpable again; now no process of the same uid, another instance included, can open its `/proc/<pid>/mem` or `ptrace` it |
| Audit hook (PEP 578) | refuses the events below |
| Purge of `sys.modules`, references replaced by stubs, import guard | network, process and native modules are not reachable by name |
| Event loop self-pipe on `os.pipe()` | the child holds no socket object at all |
| RPC lines capped at 100 KiB and 64 levels of nesting, checked before parsing; only an `http.fetch` request (child to server, 384 KiB) and an answer the child reads (1.5 MiB) may be longer, per direction (`rpc.LargeLines`) | a hostile peer cannot exhaust the parser (both ends use `rpc.decode`), and a child cannot send the server a long line of any other kind |

The audit hook raises `PermissionError` on:

- `open` outside the plugin directory, the standard library, the runner package
  and the private temporary directory (cwd) for reading, and outside that
  temporary directory for writing; relative paths, `.`/`..` segments and
  site-packages are refused; writing a file with a native-module suffix (`.so`,
  `.dylib`, ...) is refused even in the temporary directory (renaming to one
  too). That only stops a native module from being imported by name: it does not
  stop native code from being loaded (open path 4 below). `os.listdir` and
  `os.scandir` follow the read rule; `os.remove`, `rmdir`, `mkdir`, `chmod`,
  `truncate`, `utime` and `rename` the write rule.
- every `socket.*` event, `http.client.*`, `urllib.Request`, the mail and FTP
  clients;
- `os.system`, `os.exec`, `os.fork`, `os.forkpty`, `os.posix_spawn`, `os.spawn`,
  `subprocess.Popen`, `pty.spawn`;
- every `ctypes.*` event (`dlopen`, calls, raw memory);
- `os.kill`, `os.killpg`, `signal.pthread_kill`, `os.putenv`, `os.unsetenv`,
  `os.chdir`, `os.chown`, `os.symlink`, `os.link`, extended attributes and flags;
- `sqlite3.connect` and extension loading;
- `gc.get_objects`, `gc.get_referrers`, `gc.get_referents`;
- `object.__setattr__` of `__code__` or `__class__`;
- `sys.addaudithook` (CPython swallows the exception and does not add the hook,
  so the refusal is silent);
- thread start (`_thread.start_new_thread`, `_thread.start_joinable_thread`;
  3.12 and later only, see below);
- `resource.setrlimit`, `resource.prlimit`;
- the `import` event for every name in `policy.DENIED_MODULES`.

The hook is compiled per child from `_HOOK_SOURCE` with its roots and lists baked
in as constants (tuples and frozenset displays), reads no global and no builtin
at call time (it uses only default arguments), and its globals and builtins
dicts are emptied once it is defined. Nothing mutable is reachable from its
frame.

## The demonstrated bypasses (critique-1 §1.2, MSHL-191)

1. **The `import` event fires only when a module is loaded.** A module already in
   `sys.modules`, or held by another module (`asyncio.base_events.socket`), is
   reachable without an import. Answer: the runner imports `asyncio` before the
   hook, then removes `socket`, `ssl`, `subprocess`, `_posixsubprocess` and the
   other denied modules from `sys.modules`, clears their dicts and replaces every
   module attribute that pointed to one with a stub raising `PermissionError`.
   `asyncio.open_connection` fails that way. Corpus: `loaded_module_reach`.
2. **`_posixsubprocess.fork_exec` emits no audit event.** Answer: the module is
   purged, its dict cleared, its import refused (hook and import guard), and
   asyncio's `subprocess` references are stubs. **Not closed**: where
   `_posixsubprocess` is built into the interpreter (python-build-standalone,
   which the deb/rpm packages ship), plugin code can rebuild the module from its
   spec through the import machinery kept in `sys.modules`
   (`importlib._bootstrap.module_from_spec` emits no `import` event) and call
   `fork_exec`; checked on 3.11. Only the OS isolation closes this path: under
   `bwrap` and `setuid` what it can exec stays in the box (corpus:
   `posixsubprocess_spec_rebuild`, check `exec_escape`); under `none` it stays
   open. Corpus: `posixsubprocess_reach` (the routes the runner does close).
3. **The hook's frame is reachable through the traceback** of the exception it
   raises. Answer: the frame holds only immutable values (the event name, the
   arguments tuple, strings, ints, types) and its globals and builtins are empty
   dicts, so there is nothing to patch to disable it. Corpus: `hook_frame`.
4. **Native code from the work dir** (found by the MSHL-191 corpus). A plugin
   writes a shared library under a harmless suffix (`libevil.txt`) into its work
   dir and loads it with `ExtensionFileLoader` from the import machinery kept in
   `sys.modules`: `dlopen` emits no audit event, so nothing in-process refuses
   it, and the library then runs with no hook at all. **Not closed**
   in-process, like bypass 2: under `bwrap` and `setuid` what it runs stays in
   the box (corpus: `native_load_from_work_dir`, check `exec_escape`); under
   `none` it stays open. A Store archive cannot carry native code (refused by
   suffix and by magic bytes, nested archives included: `nested_zip_native`,
   `native_magic`), so this needs code that writes the library itself.

## Other known limits

- `sys.meta_path`, `builtins.__import__` and `sys.modules` are mutable: plugin
  code can remove the import guard. The allowlist is an aid for authors (a clear
  error, a known surface), not a boundary; the hook keeps refusing the events.
- On Python 3.11 starting a thread emits no audit event: a plugin can start
  threads there (the `threading` import is refused to plugin code, `_thread` is
  not a boundary). Thread stacks are small, `RLIMIT_AS` bounds them at every
  level and `RLIMIT_NPROC` under the dedicated uid (the corpus checks it).
- The RPC descriptors are ordinary small integers: plugin code can write to the
  server directly. The server must treat every child message as hostile input
  (bounded lines, typed checks), which it does anyway.
- `host().call_sync` (legacy bots) blocks the child's event loop until the server
  answers or its timeout (25 s) ends. A plugin that calls it in a loop only stalls
  its own instance; the lines read meanwhile are held back within hard caps (128
  lines, 1 MiB) and the overflow is refused, so the wait cannot grow memory without
  bound. The server's own per-event 30 s deadline still applies.
- `RLIMIT_AS` is not set on macOS (not enforced there); macOS is a development
  platform only.
- A file descriptor number given to `open()` is accepted (only descriptors the
  child already holds can be named).
- Reading the standard library and the runner package is allowed (they are
  public). The server code (`app/`), the database and the data directory are
  not readable through `open`; without OS isolation, the bypasses above still
  reach them.

## Tests

What runs, and where:

- `tests/test_plugin_corpus.py` runs the hostile corpus in `tests/plugin_corpus/`
  (format and how to add a case: its `README.md` and `CONTRIBUTING.md`). Each case
  is one minimal plugin aimed at one guarantee, with its expected typed outcome.
  `runner` cases run in a real runner child under every level this machine
  offers (`none` always, `bwrap` where its self-test passes, `setuid` as root
  with the account), with a polluted environment and an inherited descriptor
  from the parent; after each event the database file and the app tree are
  checked untouched and the child must still answer. `host` cases run through
  the real supervisor (`app/plugins/host.py`) with the isolation selection
  forced to each level, and the instance must serve again afterwards. `install`
  cases build an archive and must be refused by `extract_package` with nothing
  written. A `PermissionError` expectation also requires the runner's own
  message (`plugin sandbox:`), so a refusal the OS happens to make cannot hide
  a removed guard.
- `tests/test_plugin_rpc_fuzz.py` fuzzes the framing and the parser with
  `hypothesis` (dev dependency; bounded sizes, depths and example counts,
  derandomized): `rpc.decode` returns a valid message or raises
  `ProtocolError` (malformed JSON, NaN and the infinities, huge ids, 5000-digit
  integers, nesting either side of the 64 cap, invalid UTF-8); encode then
  decode is the identity; `LineDecoder` gives the same lines however the stream
  is cut, never buffers past its bound and refuses a line without newline once
  past it, and the real caps hold at their edges (100 KiB, 384 KiB for
  `http.fetch`); `PluginProcess._handle_line` never raises on any line; a real
  child answers any line under the cap and keeps serving, and ends itself with
  exit 2 on a line over it (the host reports `protocol_violation` and restarts it).
- `tests/test_plugin_runner.py` (the runner's own behaviour, standard library
  and pytest only), `tests/test_plugin_isolation.py` (the selection table and
  the per-level checks: database, environ, code, network, group kill) and
  `tests/test_plugin_host.py` (budgets, restarts, crash loop).

CI: the backend job (Python 3.11, 3.12 and 3.14, `ubuntu-latest`, bubblewrap
installed and its self-test required) runs everything at `none` and `bwrap`.
The root container job (`plugin-isolation-root`, Python 3.11 and 3.14) creates
the account, runs the isolation and runner tests with `--noconftest`, then
installs the dependencies and runs the corpus and the fuzz as root, where all
three levels are available.

| Case | Stage | Tries | Expected, every level unless noted |
|---|---|---|---|
| `read_database` | runner | read a `0600` database in a `0711` data dir | `PermissionError` |
| `read_etc_passwd` | runner | read a system file | `PermissionError` |
| `proc_server_environ` | runner | `/proc/<server>/environ`, by pid and by parent pid | `PermissionError` |
| `proc_server_mem` | runner | `/proc/<server>/mem` (the radio key lives there) | `PermissionError` |
| `write_app_tree` | runner | write into the app root and `app/` | `PermissionError`, nothing written |
| `list_data_dir` | runner | list the data dir and `app/` | `PermissionError` |
| `path_tricks` | runner | relative path, `..` out of the work dir | `PermissionError` |
| `write_native_suffix` | runner | write `evil.so`, or rename to it | `PermissionError` |
| `links` | runner | symlink, hard link to `/etc/passwd` | `PermissionError` |
| `chdir` | runner | leave the work dir | `PermissionError` |
| `socket_import` | runner | import `socket`, `_socket`, `ssl` | `PermissionError` |
| `urllib_request` | runner | `urllib.request`, `http.client` | `PermissionError` |
| `loaded_module_reach` | runner | `socket` through `sys.modules`, `asyncio.base_events`, `asyncio.open_connection` (bypass 1) | purged, `PermissionError` |
| `subprocess_spawn` | runner | `subprocess`, `os.system`, `os.posix_spawn`, `os.execv` | `PermissionError` |
| `posixsubprocess_reach` | runner | `_posixsubprocess` by import and through asyncio (bypass 2) | `PermissionError` |
| `posixsubprocess_spec_rebuild` | runner | rebuild `_posixsubprocess` from its spec | open path: `xfail` at `none`, `exec_escape` at `bwrap`/`setuid` |
| `fork_bomb` | runner | `os.fork()` in a loop | `PermissionError` at the first |
| `kill_parent` | runner | `os.kill`, `os.killpg` (signal 0) | `PermissionError` |
| `thread_start` | runner | `_thread.start_new_thread` | `PermissionError` on 3.12+; 3.11: open path, `nproc` at `setuid`, `xfail` elsewhere |
| `ctypes_import` | runner | `ctypes`, `_ctypes` | `PermissionError` |
| `extension_import` | runner | `mmap`, `fcntl`, `sqlite3` | `PermissionError` |
| `native_load_from_work_dir` | runner | load native code written to the work dir (bypass 4) | open path: `xfail` at `none`, `exec_escape` at `bwrap`/`setuid` |
| `zipimport` | runner | import `zipimport` | `PermissionError`, not in `sys.modules` |
| `gc_objects` | runner | `gc.get_objects`, by import and by spec rebuild | `PermissionError` |
| `code_patch` | runner | set `__code__`, `__class__` | `PermissionError` |
| `add_audit_hook` | runner | `sys.addaudithook` | silently not added |
| `hook_frame` | runner | walk the traceback into the hook's frame (bypass 3) | empty globals and builtins, immutable locals |
| `rlimit_raise` | runner | `resource.setrlimit`, module rebuilt from its spec | `PermissionError` |
| `putenv` | runner | change the environment | `PermissionError` |
| `environ` | runner | read `os.environ` (parent passed a secret) | only `LANG`, `PYTHONHASHSEED` |
| `inherited_fds` | runner | look for a descriptor the parent passed | none past 0, 1, 2 |
| `memory_bomb` | runner (Linux) | allocate 1 GiB | `MemoryError` (`RLIMIT_AS`) |
| `print_flood` | runner | 20000 `print()` lines, a forged line on fd 1 | stream intact, all in stderr |
| `oversized_result` | runner | a 200 KiB result | `-32603`, the child keeps serving |
| `infinite_loop` | host | `while True: pass` in a handler | `event_cancel_ignored`: killed, replaced |
| `cpu_burn` | host (Linux) | burn CPU while yielding | `cpu_budget`: killed, replaced |
| `huge_stdout_line` | host | 10 MB without newline on the RPC descriptor | `rpc_framing`: killed, replaced |
| `garbage_to_host` | host | non-JSON lines, a forged answer, a huge id, deep nesting on the RPC descriptor | dropped, same process |
| `crash_exit` | host | `os._exit(7)` in a handler | `crash`: replaced |
| `timer_too_short` | host | `timer.set(1)` | `invalid_params` |
| `timer_quota` | host | arm timers until refused | 16 armed, then `quota_exceeded` |
| `http_without_grant` | host | `http.fetch` without the permission | `permission_denied`, no connection |
| `zip_slip` | install | an entry climbing out of the destination | `package_unsafe_path` |
| `zip_absolute_path` | install | an entry with an absolute name | `package_unsafe_path` |
| `zip_symlink` | install | a symlink entry to the database | `package_symlink` |
| `nested_zip_native` | install | a `.so` inside a nested zip | `package_native_extension` |
| `native_magic` | install | ELF bytes under a harmless name | `package_native_extension` |

Send quotas per scope (instance, destination, Meshloom-wide budget, loop
guards) are covered by `tests/test_plugin_quotas.py` and
`tests/test_plugin_capabilities.py`, not by the corpus (they need a radio
stand-in and a database, not a hostile child).

**Mutation check** (MSHL-191, Python 3.11 in a privileged Linux arm64
container, root, all three levels): each guard below was removed in turn and the
cases pinning it failed: the `os.fork`, `sys.addaudithook`, `gc.get_objects`
and `resource.setrlimit` refusals, the open read roots, the native-suffix write
rule, the environment scrub, `RLIMIT_AS`, the host's stdout drain after a
framing kill, cancel-then-kill, the timer minimum, the archive symlink check,
the nesting cap, the partial-line bound and the NaN refusal of `rpc.decode`.

**Found by the corpus** (MSHL-191): on Python 3.11 a child killed for a line over
the cap left its instance shown `running` with no process, never restarted:
the host stopped reading stdout, and 3.11's `Process.wait()` returns only once
every pipe is closed (gh-119710, fixed in 3.12). The host now drains, then
closes the pipe after such a kill (`PluginProcess._discard_stdout`, bounded by
`STDOUT_DRAIN_SECONDS`), and the code is `rpc_framing`. The corpus also found
bypass 4 above.
