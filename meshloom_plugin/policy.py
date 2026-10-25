"""What a plugin child may import, which environment it keeps and its default limits.

Shared by the runner (child) and `app/plugins/launch.py` (server). The import
lists are an aid for authors (clear errors, a known surface), not the security
boundary: in-process restrictions can be bypassed by construction, and the OS
isolation is what contains a hostile plugin (docs/dev/plugins-security.md).
"""

# Standard-library modules a plugin may import, with their submodules unless
# listed in DENIED_MODULES. `meshloom_plugin` is the SDK; the plugin's own
# modules and `vendor/` are allowed separately (they are not stdlib names).
ALLOWED_IMPORTS = frozenset(
    {
        "abc",
        "array",
        "asyncio",
        "base64",
        "binascii",
        "bisect",
        "calendar",
        "cmath",
        "codecs",
        "collections",
        "contextlib",
        "contextvars",
        "copy",
        "dataclasses",
        "datetime",
        "decimal",
        "difflib",
        "enum",
        "errno",
        "fnmatch",
        "fractions",
        "functools",
        "hashlib",
        "heapq",
        "hmac",
        "html",
        "io",
        "itertools",
        "json",
        "keyword",
        "logging",
        "math",
        "meshloom_plugin",
        "numbers",
        "operator",
        "os",
        "posixpath",
        "pprint",
        "random",
        "re",
        "reprlib",
        "secrets",
        "shlex",
        "stat",
        "statistics",
        "string",
        "struct",
        "sys",
        "textwrap",
        "time",
        "types",
        "typing",
        "unicodedata",
        "urllib",
        "urllib.parse",
        "uuid",
        "warnings",
        "weakref",
        "zlib",
    }
)

# Refused to every importer once the runner has frozen its import surface, and
# purged from `sys.modules` with their references in other modules replaced.
# A name matches itself and its submodules.
DENIED_MODULES = frozenset(
    {
        "_ctypes",
        "_multiprocessing",
        "_posixshmem",
        "_posixsubprocess",
        "_socket",
        "_sqlite3",
        "_ssl",
        "ctypes",
        "ftplib",
        "http",
        "imaplib",
        "logging.handlers",
        "multiprocessing",
        "poplib",
        "pty",
        "smtplib",
        "socket",
        "socketserver",
        "sqlite3",
        "ssl",
        "subprocess",
        "telnetlib",
        "urllib.request",
        "xmlrpc",
    }
)

# The only environment variables a child keeps (cadrage §4.3).
ENV_KEYS = ("LANG", "PYTHONHASHSEED")

# Default hard limits (cadrage §4.2). Not listed: RLIMIT_NPROC, which the runner
# sets to 0 on Linux at every level (no thread, no process; root ignores it).
DEFAULT_LIMITS = {
    "address_space": 256 * 1024 * 1024,
    "open_files": 64,
    "file_size": 50 * 1024 * 1024,
    "cpu_seconds": 3600,
}

MAX_CONCURRENT_HANDLERS = 8

# Isolation levels the supervisor may announce (MSHL-180); the child refuses others.
ISOLATION_LEVELS = ("none", "setuid", "bwrap")


def _matches(name: str, names: frozenset[str]) -> bool:
    parts = name.split(".")
    return any(".".join(parts[:i]) in names for i in range(1, len(parts) + 1))


def is_denied(name: str) -> bool:
    """True when `name` or one of its parent packages is in DENIED_MODULES."""
    return _matches(name, DENIED_MODULES)


def plugin_may_import(name: str, stdlib_names: frozenset[str]) -> bool:
    """True when plugin code may import `name`.

    Allowed: the allowlist (and submodules), and any name that is not a stdlib
    module (the plugin's own modules and `vendor/`). Denied names always lose.
    """
    if is_denied(name):
        return False
    if _matches(name, ALLOWED_IMPORTS):
        return True
    return name.split(".")[0] not in stdlib_names
