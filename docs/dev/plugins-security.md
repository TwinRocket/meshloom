# Plugin sandbox: what the child runtime blocks, and what it cannot

The plugin child (`meshloom_plugin/runner.py`, launch and protocol in
`docs/dev/plugins.md`, "Runner") restricts itself before it loads plugin code.
**Those restrictions are defence in depth, not the security boundary.** Python
code running in a process can, by construction, undo or walk around anything
installed in that same process. What contains a hostile plugin is the OS
isolation around the child (MSHL-180: bubblewrap on deb/rpm, a dedicated uid in
Docker and the Home Assistant add-on) plus the capability API (a plugin acts on
the server only through RPC methods the server checks). On a target where
neither is available ("hooks only"), a hostile plugin can read the database and
the code; the UI must say so (MSHL-180). Like every doc here, this file may be
wrong: the code wins.

## What the runner does

| Measure | What it gives |
|---|---|
| Descriptors from 3 up closed at start; the RPC descriptors duplicated, fd 0 = `/dev/null`, fd 1 = stderr | nothing inherited from the server; `print()` cannot corrupt the RPC stream |
| The cwd must be the `work_dir` the supervisor named (`launch.prepare_work_dir()`, 0700), not `/`, not nested with the plugin directory; otherwise no start | the only writable place is a private directory, never the server's cwd or the plugin's code |
| `os.environ` cleared but `LANG` and `PYTHONHASHSEED` (and the supervisor passes only those) | no server secret in the environment |
| Hard rlimits: address space (Linux), open files, file size, core 0, CPU; `RLIMIT_NPROC` under the dedicated uid only; small thread stacks | memory bombs fail with `MemoryError`, no core dump, a runaway loop is killed at the CPU cap |
| `PR_SET_NO_NEW_PRIVS` (Linux) | no privilege gain through a setuid binary, even if a process were spawned |
| Audit hook (PEP 578) | refuses the events below |
| Purge of `sys.modules`, references replaced by stubs, import guard | network, process and native modules are not reachable by name |
| Event loop self-pipe on `os.pipe()` | the child holds no socket object at all |
| RPC lines capped at 100 KiB and 64 levels of nesting, checked before parsing | a hostile peer cannot exhaust the parser (both ends use `rpc.decode`) |

The audit hook raises `PermissionError` on:

- `open` outside the plugin directory, the standard library, the runner package
  and the private temporary directory (cwd) for reading, and outside that
  temporary directory for writing; relative paths, `.`/`..` segments and
  site-packages are refused; writing a file with a native-module suffix (`.so`,
  `.dylib`, ...) is refused even in the temporary directory. `os.listdir` and
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

## The three demonstrated bypasses (critique-1 §1.2)

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
   `fork_exec`; checked on 3.11. Only the OS isolation closes this path (no
   binary to run, no namespace to escape to). Corpus: `posixsubprocess_reach`
   (the routes the runner does close).
3. **The hook's frame is reachable through the traceback** of the exception it
   raises. Answer: the frame holds only immutable values (the event name, the
   arguments tuple, strings, ints, types) and its globals and builtins are empty
   dicts, so there is nothing to patch to disable it. Corpus: `hook_frame`.

## Other known limits

- `sys.meta_path`, `builtins.__import__` and `sys.modules` are mutable: plugin
  code can remove the import guard. The allowlist is an aid for authors (a clear
  error, a known surface), not a boundary; the hook keeps refusing the events.
- On Python 3.11 starting a thread emits no audit event: a plugin can start
  threads there (the `threading` import is refused to plugin code, `_thread` is
  not a boundary). Thread stacks are small and `RLIMIT_NPROC` applies under the
  dedicated uid.
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

`tests/test_plugin_runner.py` runs a hostile plugin through a real child and
checks each refusal leaves the child serving; `tests/plugin_corpus/` holds the
seed cases above plus the `print()` flood, `os.environ`, inherited descriptors
and thread start. The open paths are pinned as corpus cases with an
`open_path` entry (`posixsubprocess_spec_rebuild`, `thread_start` below 3.12):
they report `xfail` while the path stays open and pass once a later ticket
(MSHL-180, MSHL-191) closes it. MSHL-191 runs the corpus under every isolation
level.
