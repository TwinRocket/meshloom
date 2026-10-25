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
| Linux, not root, holds `CAP_SETUID` and `CAP_SETGID` and the account exists (Docker with `MESHLOOM_RUN_AS_USER`) | `setuid` | — |
| Linux, not root (deb/rpm, RPi image, source) | `bwrap` if its self-test passes | `none` |
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
| `/proc/<server>/environ`, `mem` | not there (own pid namespace and `/proc`) | refused (root's process, or another uid's undumpable one) | refused by `PR_SET_DUMPABLE` (non-root servers), readable when the server is root |
| Write outside the work dir | refused (read-only binds, `--remount-ro /`) | refused (root-owned tree) | only the hooks |
| Network | none (own network namespace, loopback down) | open (the hooks refuse sockets in-process) | the hooks only |
| A process it execs (`fork_exec` rebuilt) | none: the seccomp filter and `RLIMIT_NPROC` 0 fail the fork | same | same; as root only the filter holds, and a root child without one does not start |
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

`subprocess.Popen(user=, group=, extra_groups=[], umask=0o077)` with a uid of
its own per plugin instance (MSHL-339). The `meshloom-plugin` account (uid and
gid pinned to 10002, `PLUGIN_UID` in the `Dockerfile`, next to
`MESHLOOM_RUN_AS_USER`'s 10001: Docker does not remap users, so it is that uid
on the host too, and the first system uid, 999, is often a real host account;
no shell, no home; an account that already exists is kept; the add-on is `FROM`
that image) only opts the server into this level; no child runs under it any
more. Each instance gets the lowest free uid in 40000-49999 (gid = uid, no
passwd entry needed), stored in the `plugin_uids` table (migration 121) so it
keeps it across restarts: a uid with a passwd or group entry, the server's own
uid, 10002, or one already held is skipped (the passwd and group lookups run in a
thread, outside the write transaction); none left is `uid_exhausted`. Docker does
not remap uids by default: 40000-49999 are the same numbers on the host, where a
directory service (LDAP) may use them; the user docs (`install-paths.md`) say so.
Deleting the instance purges what its uid left in the shared temporary
directories (`/tmp`, `/var/tmp`, `/dev/shm`, `/dev/mqueue`, `tempfile.gettempdir()`:
every entry it owns at any depth, also under a directory another uid owns such as
`/tmp/.X11-unix`, without leaving each directory's file system; past 100 000 entries
the purge fails) and its System V IPC objects (owner or creator, `IPC_RMID`), then
frees the uid. Before removing a directory tree the uid owns (in the temp dirs, or its
work dir without root), the helper gives the owner `rwx` back on each of its
directories, so a plugin leaving them `0300` or `0000` cannot make the removal fail and
keep its uid forever (MSHL-348); it opens each one `O_PATH | O_NOFOLLOW` relative to its
parent and changes the mode through that descriptor, so no symlink is followed and only
a directory the uid owns is touched. The walk fails closed (MSHL-347): a directory the
uid may enter but not list (`0733` owned by another uid, where it could have hidden
files) fails the purge; only a work dir of another instance (named as `mkdtemp` makes
it, `meshloom-plugin-` and 8 characters of `[a-z0-9_]`, `0711`, owned by another plugin
uid: 40000-49999 or 10002, directly under a scanned dir; MSHL-348) and a directory the
uid cannot enter are skipped, since it could not have written there unless another
plugin uid opened a subdirectory to it (collusion between two plugins, out of scope).
A `0711` directory that is none of these, even a harmless one another program left in
`/tmp`, blocks every purge that walks past it: the uids then stay taken (fail closed)
and the server logs the path ("not listable") at each attempt, until an administrator
removes the directory or opens it (`chmod o+r`); startup retries the purge. After the helper, the server re-walks the same dirs
(`owns_something`, which a root server can list fully) and fails if anything is left.
The walk does not cross file systems: what the uid left on another mount under `/tmp`
(a bind mount, a tmpfs mounted inside `/tmp`) is not purged; the uid is still freed only
after a clean walk of each listed dir's own file system. A failed purge keeps the row, and the
startup (`PluginManager.reap_uids`) retries orphans, purges the uids of stopped
instances, and once purges what the shared 10002 uid of earlier releases left.
A root server creates the work dir and hands it to the uid (`chown`, `0700`).
A non-root server holding only `CAP_SETUID` and `CAP_SETGID`
(docker-entrypoint.sh under `MESHLOOM_RUN_AS_USER` keeps exactly those two,
ambient) cannot `chown` nor signal another uid: a short helper started as the
instance uid, capabilities dropped, creates the work dir (`0711`, the server
must still `chdir` into it before the uid switch), removes it at the end
(`remove_work_dir`), sends the group signals (`killpg` of a group the uid owns,
no `CAP_KILL`) and runs the purge. A root server signals directly. The runner
clears the inherited capabilities before any plugin code. The runner refuses to start when told `setuid` while still root.
At startup (`isolation.startup()`) the
file model is applied to the data directory: `0711` on the directory, `0600` on
each file in it, `0700` on each subdirectory but `plugins/`, whose tree is
`0755`/`0644`; the server's umask becomes `077`, so later files are born
private, and each plugin directory is opened up again (`0755`/`0644`) right
before its child starts. `/data/options.json` (the add-on's options) becomes
`0600`. Symbolic links are never followed.

### Known limits of the OS layer

- Under `setuid`, each instance has its own uid (MSHL-339): one plugin can
  neither read another one's work dir nor signal its processes
  (`test_two_instance_uids_cannot_read_or_signal_each_other`). The runner still
  sets `PR_SET_DUMPABLE` 0 (every level), which keeps `/proc/<pid>/mem` and
  `ptrace` refused to the server's other processes and, under `none`, to a
  sibling (`test_a_sibling_under_the_same_uid_cannot_open_the_child_memory`).
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
- Docker with `MESHLOOM_RUN_AS_USER`: the server keeps `CAP_SETUID` and
  `CAP_SETGID` (ambient), so `setuid`; the startup file model applies the same
  way, the data directory and its `0600` files belonging to uid 10001.
- The seccomp filter (`meshloom_plugin/seccomp.py`, no `pyseccomp`) knows
  x86_64, i386, aarch64, arm and riscv64, which covers every architecture
  Meshloom ships (amd64, arm64 and armhf packages; amd64, arm64 and armv7
  images and add-on; `test_every_shipped_architecture_has_a_seccomp_table`
  fails when one is added without a table). On another architecture (a source
  install on s390x, say) it is best effort, by decision (MSHL-339): a non-root
  child runs without it, still held by `RLIMIT_NPROC` 0 (no thread, no process)
  and with every capability dropped, while a root child, which `RLIMIT_NPROC`
  does not bind, refuses to start.

## What the runner does

| Measure | What it gives |
|---|---|
| Descriptors from 3 up closed at start; the RPC descriptors duplicated, fd 0 = `/dev/null`, fd 1 = stderr | nothing inherited from the server; `print()` cannot corrupt the RPC stream |
| The cwd must be the `work_dir` the supervisor named (`launch.prepare_work_dir()`, 0700), not `/`, not nested with the plugin directory; otherwise no start | the only writable place is a private directory, never the server's cwd or the plugin's code |
| `os.environ` cleared but `LANG` and `PYTHONHASHSEED` (and the supervisor passes only those) | no server secret in the environment |
| Hard rlimits: address space (Linux), open files, file size, core 0, CPU; `RLIMIT_NPROC` 0 (Linux, every level); small thread stacks | memory bombs fail with `MemoryError`, no core dump, a runaway loop is killed at the CPU cap; no thread and no process can be created, whatever path the hook does not see (the kernel counts the uid's tasks against the limit; root, `CAP_SYS_RESOURCE` and `CAP_SYS_ADMIN` ignore it) |
| `PR_SET_NO_NEW_PRIVS` (Linux) | no privilege gain through a setuid binary, even if a process were spawned |
| Inherited capabilities dropped (Linux, non-root child): ambient cleared, effective, permitted and inheritable emptied, checked in `/proc/self/status` | `CAP_SETUID`/`CAP_SETGID` kept by a `MESHLOOM_RUN_AS_USER` server do not reach plugin code |
| seccomp filter (Linux, `meshloom_plugin/seccomp.py`): `clone`, `clone3`, `fork`, `vfork`, `execve`, `execveat` and any syscall of a foreign ABI answer `EPERM` | no thread, no process, no exec, **root included** (MSHL-323); required for a root child, best effort otherwise |
| `PR_SET_DUMPABLE` 0 (Linux, before any plugin code) | `execve` (and the uid drop) made the child dumpable again; now no other process of the same uid can open its `/proc/<pid>/mem` or `ptrace` it |
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
  stop native code from being loaded; the `import` rule below does (bypass 4). `os.listdir` and
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
  so after the install the runner also replaces `sys.addaudithook` with a stub,
  compiled like the hook without globals or builtins, that raises
  `PermissionError`: the refusal is an error the plugin sees);
- thread start (`_thread.start_new_thread`, `_thread.start_joinable_thread`;
  3.12 and later only; on 3.11 the seccomp filter and `RLIMIT_NPROC` 0 fail
  the clone, see below);
- `resource.setrlimit`, `resource.prlimit`;
- the `import` event for every name in `policy.DENIED_MODULES`.
- the `import` event of an extension module (it carries the file) whose file is
  not under the standard library's directories as the bootstrap set them
  (`native_roots`), or is in site-packages or the work dir.

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
   asyncio's `subprocess` references are stubs. Where `_posixsubprocess` is an
   extension module (Debian's and the official Docker images' Python), loading it
   again emits an `import` event the hook refuses. Where it is built into the
   interpreter (python-build-standalone, which the deb/rpm packages ship), plugin
   code can rebuild the module from its spec through the import machinery kept
   in `sys.modules` (`importlib._bootstrap.module_from_spec` emits no `import`
   event) and call `fork_exec`. **Closed by the seccomp filter** (MSHL-323,
   `EPERM`) and by `RLIMIT_NPROC` 0 (MSHL-289, `EAGAIN`) at every level, 3.11 to
   3.14. Root ignores the limit, so for a child running as root (a root server
   with `none` or `bwrap`) the filter is the guard, and such a child does not
   start without it. Corpus: `posixsubprocess_spec_rebuild` (runs a program through the
   rebuilt `fork_exec`), `posixsubprocess_reach` (the routes the hook closes).
3. **The hook's frame is reachable through the traceback** of the exception it
   raises. Answer: the frame holds only immutable values (the event name, the
   arguments tuple, strings, ints, types) and its globals and builtins are empty
   dicts, so there is nothing to patch to disable it. Corpus: `hook_frame`.
4. **Native code from the work dir** (found by the MSHL-191 corpus): a plugin
   writing a shared library under a harmless suffix into its work dir and
   loading it through the import machinery kept in `sys.modules`. **Closed at
   every level**: the `import` audit event carries the file of every extension
   module (3.11 to 3.14), and the hook refuses one that is not under the
   interpreter's standard-library directories frozen at bootstrap (never
   site-packages, never the work dir). Corpus: `native_load_from_work_dir`. A
   Store archive cannot carry native code either (refused by suffix and by
   magic bytes, nested archives included: `nested_zip_native`, `native_magic`).

## Other known limits

- `sys.meta_path`, `builtins.__import__` and `sys.modules` are mutable: plugin
  code can remove the import guard. The allowlist is an aid for authors (a clear
  error, a known surface), not a boundary; the hook keeps refusing the events.
- On Python 3.11 starting a thread emits no audit event (the `threading` import
  is refused to plugin code, `_thread` is not a boundary). The seccomp filter
  and `RLIMIT_NPROC` 0 make the clone fail (`RuntimeError: can't start new
  thread`) at every level, root included (corpus: `thread_start`).
- The RPC descriptors are ordinary small integers: plugin code can write to the
  server directly. The server must treat every child message as hostile input
  (bounded lines, typed checks), which it does anyway.
- `host().call_sync` (converted bots) blocks the child's event loop until the server
  answers or its timeout (25 s) ends. A plugin that calls it in a loop only stalls
  its own instance; the lines read meanwhile are held back within hard caps (128
  lines, 1 MiB) and the overflow is refused, so the wait cannot grow memory without
  bound. The server's own per-event 30 s deadline still applies.
- `RLIMIT_AS` and `RLIMIT_NPROC` are not set on macOS (`RLIMIT_AS` is not
  enforced there, `RLIMIT_NPROC` counts the whole login session); macOS is a
  development platform only.
- A file descriptor number given to `open()` is accepted (only descriptors the
  child already holds can be named).
- Reading the standard library and the runner package is allowed (they are
  public). The server code (`app/`), the database and the data directory are
  not readable through `open`; without OS isolation, the bypasses above still
  reach them.

## LAN hosts (`http.lan_hosts`, MSHL-282, MSHL-330, MSHL-331)

The `http` capability reaches a device on the user's network only through
`lan_hosts`, each entry named in the consent. What holds, all in
`app/net/egress_guard.py` (`validate_destination(..., lan_hosts=...)`) and
covered by `tests/test_egress_guard_lan_hosts.py` and `tests/test_plugin_http_lan.py`:

- Exact entries only: no wildcard, no range, no `localhost`/`*.localhost`, no
  metadata name, no IP literal outside `10/8`, `172.16/12`, `192.168/16`,
  `100.64/10` (CGNAT, Tailscale), `fc00::/7` (manifest validation and
  `HostAllowlist`, port included).
- Local names only (`lan_host_public_name`): a single label or a name ending in
  `.local`, `.lan`, `.home.arpa` or `.internal`. A public DNS name is refused
  because its owner, not the user, would choose which LAN address it reaches.
- DNS rebinding: every address a `lan_hosts` name resolves to must be in those
  ranges, tested as written (`is_lan_address`): loopback, link-local, metadata
  (Alibaba's `100.100.100.200` included), documentation, IPv4-mapped/NAT64/6to4
  forms and public addresses are refused (`egress_lan_address`, metadata and
  documentation as `egress_private_address`). The connection is pinned to the
  checked address.
- The Meshloom host itself is refused on every port (`egress_self_address`,
  `is_self_address`), whatever the name or literal (an IPv4-mapped spelling is
  unwrapped first): an address bound to one of its interfaces (bind probe: a
  failing probe counts as local), and inside a container (`/.dockerenv`,
  `/run/.containerenv`) the default gateways from `/proc/net/route` and
  `/proc/net/ipv6_route`, which are the Docker or Home Assistant host in
  bridge networking. With `network_mode: host` (`_host_networking`, both
  signals required: the container lists the engine's `docker0`, `hassio`,
  `br-*` or `veth*` interfaces in `/proc/net/dev`, and every default route
  leaves through an interface that is its own link, `iflink == ifindex` in
  `/sys/class/net`, or a VLAN or macvlan stacked on one in the same namespace,
  which the kernel shows as a `lower_<parent>` entry) the gateway is the LAN router and is allowed; only the Home
  Assistant supervisor gateway (`172.30.32.0/23`) stays refused there, and the
  host's own addresses are caught by the bind probe. The second signal rules out
  a bridged container that runs its own engine (Docker-in-Docker lists
  `docker0` but routes through its veth `eth0`, whose peer is on the real host;
  MSHL-340). Comparing network namespace inodes with PID 1 does not work: a
  container's PID 1 shares its network namespace in both modes. Host networking
  that shows none of those interfaces, routes through a veth uplink or through
  a macvlan whose parent is in another namespace (Docker's macvlan driver: no
  `lower_` entry), or whose `/sys` cannot be read is not recognised and fails closed (the
  router stays refused). This keeps
  plugins away from the radio proxy (5051, `0.0.0.0`, no authentication) and the
  app's own port.
- Redirects: every hop is checked again against `hosts` and `lan_hosts`; a hop
  to an undeclared LAN device, or to plain HTTP outside `lan_hosts`, is
  refused before any lookup.
- Self-signed devices: `http.fetch` takes `verify_tls: false` only when the URL's
  host is a `lan_hosts` entry (`invalid_params` otherwise), and the check is
  skipped only on the hops that reach a `lan_hosts` device; a redirect to a
  public host is verified. The consent says certificates may go unchecked.
- `hosts` stays public-only; an entry cannot be in both lists.

Residual risks, accepted:

- Plain HTTP, or HTTPS with `verify_tls: false`, to a declared device: anyone on
  the LAN can read or alter it.
- In a bridged container (Docker, Home Assistant add-on), the host's LAN address
  is not visible from inside, so a plugin that declares it can still reach the
  host (radio proxy included). The consent names the device; the user decides.
  Bare-metal installs (deb, rpm, Raspberry Pi image) and host-network containers
  see every address of the host, so the bind probe covers them.
- In the Home Assistant add-on, `homeassistant` resolves to the supervisor
  gateway (`172.30.32.1`), which is the host (HA core API, radio proxy), and is
  refused on purpose, also with host networking.
- With Linux's `ip_nonlocal_bind` sysctl on, every bind probe succeeds and every
  LAN host is refused (fails closed).
- Link-local addresses are not accepted.

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
- `tests/test_plugin_seccomp.py` (standard library and pytest only): the ABI
  table, the BPF program run through a small interpreter for every ABI, and a
  probe process that installs the filter and checks `fork`, `execv`,
  `posix_spawn`, `subprocess` and a thread start are refused, and that a
  non-root process ends with no capability. `tests/test_plugin_runner.py` also
  checks `Seccomp: 2` in a live child's `/proc/<pid>/status`.

CI: the backend job (Python 3.11, 3.12, 3.13 and 3.14, `ubuntu-latest`, bubblewrap
installed and its self-test required) runs everything at `none` and `bwrap`.
The root container job (`plugin-isolation-root`, Python 3.11 and 3.14) creates
the account, runs the isolation, runner and seccomp tests with `--noconftest`,
then installs the dependencies and runs the corpus, the fuzz and the host tests
as root, where all three levels are available, then all of them again as uid
10001 holding only `CAP_SETUID`/`CAP_SETGID` ambient (`setpriv` as
docker-entrypoint.sh calls it), where `none` and `setuid` are.

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
| `posixsubprocess_spec_rebuild` | runner | rebuild `_posixsubprocess` from its spec and run a program with `fork_exec` | refused: the import, or the fork (`EPERM` from the filter, `EAGAIN` from `RLIMIT_NPROC`), root included |
| `fork_bomb` | runner | `os.fork()` in a loop | `PermissionError` at the first |
| `kill_parent` | runner | `os.kill`, `os.killpg` (signal 0) | `PermissionError` |
| `thread_start` | runner | `_thread.start_new_thread` | `PermissionError` on 3.12+; 3.11: `can't start new thread` (seccomp filter, `RLIMIT_NPROC` 0), root included |
| `ctypes_import` | runner | `ctypes`, `_ctypes` | `PermissionError` |
| `extension_import` | runner | `mmap`, `fcntl`, `sqlite3` | `PermissionError` |
| `native_load_from_work_dir` | runner | load native code written to the work dir (bypass 4) | `PermissionError` |
| `zipimport` | runner | import `zipimport` | `PermissionError`, not in `sys.modules` |
| `gc_objects` | runner | `gc.get_objects`, by import and by spec rebuild | `PermissionError` |
| `code_patch` | runner | set `__code__`, `__class__` | `PermissionError` |
| `add_audit_hook` | runner | `sys.addaudithook` | `PermissionError` (the stub) |
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

**Mutation check** (`scripts/test/plugin_mutations.py`, MSHL-191; Python 3.11
in a privileged Linux arm64 container, root, all three levels): each guard below
was removed in turn and the cases pinning it failed: the `os.fork`,
`sys.addaudithook`, `gc.get_objects` and `resource.setrlimit` refusals, the
`sys.addaudithook` stub, `RLIMIT_NPROC` 0 (MSHL-289), the seccomp filter
(MSHL-323), the open
read roots, the native-suffix write rule, the native load roots, the environment
scrub, `RLIMIT_AS`, the host's two 3.11 pipe guards together (the stdout drain
after a framing kill and the bounded wait for the pipes: either one alone
keeps the instance restarting), cancel then kill, the timer minimum, the archive symlink check, the nesting cap, the
partial-line bound and the NaN refusal of `rpc.decode`. The pipe mutant is
caught only on 3.11 with bwrap (the hang needs both): in CI that is the 3.11
backend job.

**Found by the corpus** (MSHL-191): on Python 3.11 a child killed for a line over
the cap left its instance shown `running` with no process, never restarted:
the host stopped reading stdout, and 3.11's `Process.wait()` returns only once
every pipe is closed (gh-119710, fixed in 3.12). The host now drains, then
closes the pipe after such a kill (`PluginProcess._discard_stdout`, bounded by
`STDOUT_DRAIN_SECONDS`, the close in a `finally`), and the code is `rpc_framing`.
The same 3.11 behaviour could still hang on a pipe a surviving grandchild
holds (only bypass 2 can start one): once the child is reaped, `_wait_exit`
waits `PIPE_CLOSE_SECONDS`, then kills the group and closes the host's ends
of every pipe. The corpus also found bypass 4 above, now closed.

## Measurements

`tests/plugin_corpus/measure_footprint.py` starts children the way the
supervisor does, under each level available, and reads `/proc` (RSS and PSS of
each child's process tree, bwrap's processes included), then keeps them busy
(one event every 5 s) and reads again. Run it on the target, from the
repository root:

```bash
uv run python tests/plugin_corpus/measure_footprint.py --instances 8 --duration 3600
```

Run as root with the `meshloom-plugin` account to include `setuid`, and with
bubblewrap installed (and user namespaces allowed) to include `bwrap`.

**These are not Raspberry Pi figures.** No Pi was available for MSHL-191; the
real-Pi run is tracked separately. The figures below come from a
Linux arm64 container (Debian bookworm, `python:3.11-slim`, Python 3.11.17, the
same architecture and distribution as Raspberry Pi OS 64-bit) on an Apple M
host: memory is comparable, start times are not (a Pi 3's Cortex-A53 is several
times slower). Eight instances, one hour, one event every 5 s:

| Level | Processes per child | RSS per child, start / 1 h | PSS per child, start / 1 h | PSS of the 8 children, start / 1 h | Start to first answer |
|---|---|---|---|---|---|
| `none` | 1 | 18.7 / 19.1 MiB | 11.0 / 12.1 MiB | 88 / 95 MiB | 30-31 ms |
| `bwrap` | 3 (two bwrap, the runner) | 21.8 / 21.8 MiB | 11.4 / 11.4 MiB | 91 / 92 MiB | 33-37 ms |
| `setuid` | 1 | 18.7 / 18.7 MiB | 11.1 / 11.2 MiB | 89 / 91 MiB | 32-43 ms |

PSS divides each shared page among the processes sharing it, so it is the figure that adds up across
children; RSS counts the interpreter's shared pages in every child. bwrap adds
about 3 MiB of RSS per child and almost no PSS. Over the hour (719 rounds of one
event to each child) a child grew by at most 1 MiB of PSS. The three levels ran
at the same time in separate containers, so the host's `MemAvailable` drop is
not reported.

The instance cap (`host.instance_cap`: one instance per 128 MB of `MemTotal`,
between 3 and 20; a Pi 3 with 1 GB gets 7) stays: a child costs about a tenth
of its share, which leaves the rest to the server, the OS and a plugin's own
data within its 256 MiB `RLIMIT_AS`. Eight children take about 95 MiB of PSS, a
tenth of a Pi 3's memory. To rerun on a Pi: install
bubblewrap, check out the repository, `uv sync`, create the `meshloom-plugin`
account (uid and gid 10002) to include `setuid`, then run the command above as
root, and once more as the service user for `bwrap` alone; also note the start
times, and `MemAvailable` with the server running. The deb/rpm packages ship
python-build-standalone (and the armhf images a 32-bit build), whose figures may
differ from this Debian interpreter's.
