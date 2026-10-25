"""Static attention scan of a local plugin source (a converted bot, a `dev check`).

Pure: `scan_source(source)` parses with `ast` (nothing is imported or run) and
returns the reasons a user should review, each with its line, plus the hosts of
the `https://` literals that prefill the `http` permission. Reused by the
bot conversion (`app/plugins/bot_conversion.py`, attention `review_permissions`)
and by `dev check` (Store T4d).

It also lists the local-network devices of the `http://` and `https://` literals
that `check_lan_host` accepts (`lan_hosts`, MSHL-342): suggestions the local plugin
editor offers one by one with their source line, never added on their own. Nothing
is resolved: the Meshloom machine's own names (its host name, `meshloom`, Docker's
`host.docker.internal`...) are dropped by name, and the fetch-time egress guard
still refuses whatever reaches the machine.

Blocking reasons name the common things the sandbox refuses (the call fails at
run time with the error in the journal; the plugin still starts); the list is a
review aid, not exhaustive, and the sandbox stays the boundary. Informative
reasons name what behaves differently (`print` goes to the journal, module
state is lost on each restart).

Hostile but valid input never raises: the source size and the number of AST
nodes are capped (`source_too_large`, `too_complex`), a `RecursionError` or
`MemoryError` from the parser is `too_complex`, and the walk is iterative. Host
extraction examines a bounded number of bytes and URLs (`truncated`).
"""

import ast
import re
import socket
from dataclasses import dataclass, field
from typing import Any

from pydantic_core import PydanticCustomError

from app.message_limits import DM_HARD_LIMIT
from app.plugins.permissions import (
    HTTP_MAX_LAN_HOSTS,
    check_host,
    check_lan_host,
    is_meshloom_domain,
)
from meshloom_plugin import policy

# The runner's own lists (MSHL-176), so the scan and the sandbox never drift. A
# local source has no `vendor/`, so any other name is flagged.
ALLOWED_IMPORTS = policy.ALLOWED_IMPORTS
DENIED_MODULES = policy.DENIED_MODULES
_THREAD_MODULES = frozenset({"threading", "_thread", "concurrent", "multiprocessing"})
_FLAGGED_CALLS = {
    "open": "open_call",
    "exec": "exec_call",
    "eval": "eval_call",
    "compile": "compile_call",
    "__import__": "import_call",
}
# `os.<name>(...)` calls the runner's audit hook refuses (processes, signals).
_OS_PROCESS_CALLS = frozenset(
    {
        "system",
        "popen",
        "fork",
        "forkpty",
        "kill",
        "killpg",
        "execl",
        "execle",
        "execlp",
        "execlpe",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
        "posix_spawn",
        "posix_spawnp",
    }
)
_MUTABLE_FACTORIES = frozenset(
    {"dict", "list", "set", "bytearray", "defaultdict", "deque", "Counter", "OrderedDict"}
)
# The longest message one send can carry (a DM; a channel allows fewer bytes).
LONG_LITERAL_BYTES = DM_HARD_LIMIT
MAX_PREFILL_HOSTS = 20
# Bounds that keep a scan fast on hostile input.
MAX_SOURCE_BYTES = 1024 * 1024
MAX_AST_NODES = 200_000
MAX_URL_SCAN_BYTES = 256 * 1024  # literal bytes searched for https:// URLs
MAX_URL_MATCHES = 1000  # URLs examined
_URL_RE = re.compile(r"(https?)://([^/\s?#'\"<>\\]+)", re.IGNORECASE)
# LAN suggestions have their own cap (the `lan_hosts` maximum), so LAN literals never
# crowd out public hosts or the reverse; they share the byte and URL budgets.
MAX_LAN_SUGGESTIONS = HTTP_MAX_LAN_HOSTS
# Ports of services that take orders without a password or hold a whole home: Home
# Assistant (8123), a Meshloom radio proxy (5051), a Meshloom web app (8000). The
# editor warns before such a device is added.
SERVICE_PORTS = frozenset({8123, 5051, 8000})
_DEFAULT_PORTS = {"http": 80, "https": 443}
# Names that always reach the machine Meshloom runs on (or its container host).
_SELF_NAMES = frozenset(
    {
        "meshloom",
        "meshloom.local",
        "ip6-localhost",
        "ip6-loopback",
        "host.docker.internal",
        "gateway.docker.internal",
        "host.containers.internal",
        "supervisor",
    }
)


@dataclass(frozen=True)
class AttentionReason:
    """One finding. `code` is stable (the UI translates it), `detail` is a module
    or host name when the code needs one, never user data."""

    code: str
    line: int
    detail: str | None = None
    informative: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "line": self.line,
            "detail": self.detail,
            "informative": self.informative,
        }


@dataclass(frozen=True)
class LanHostSuggestion:
    """A `lan_hosts` entry found in the code: `host` is the entry as the permission
    stores it (`name`, `name:port`, `ip`, `[v6]:port`; no credentials, no default
    port), `line` the first line that mentions it, `service_port` True for a port of
    `SERVICE_PORTS`."""

    host: str
    line: int
    service_port: bool = False

    def to_json(self) -> dict[str, Any]:
        return {"host": self.host, "line": self.line, "service_port": self.service_port}


@dataclass
class AttentionReport:
    reasons: list[AttentionReason] = field(default_factory=list)
    http_hosts: list[str] = field(default_factory=list)
    # True when host extraction stopped at a bound: more hosts may exist.
    truncated: bool = False
    # Suggestions only, never prefilled: not part of `to_json` (the stored shape).
    lan_hosts: list[LanHostSuggestion] = field(default_factory=list)
    lan_truncated: bool = False

    @property
    def needs_attention(self) -> bool:
        return bool(self.reasons)

    def to_json(self) -> dict[str, Any]:
        """The shape stored in `plugins.attention` (with the caller's `type`)."""
        return {
            "reasons": [reason.to_json() for reason in self.reasons],
            "http_hosts": list(self.http_hosts),
            "truncated": self.truncated,
        }


def _matches(name: str, names: frozenset[str]) -> bool:
    parts = name.split(".")
    return any(".".join(parts[:i]) in names for i in range(1, len(parts) + 1))


def import_allowed(name: str) -> bool:
    """True when a local plugin may import `name` (allowlisted, not denied)."""
    return not _matches(name, DENIED_MODULES) and _matches(name, ALLOWED_IMPORTS)


def _https_host(match: str) -> str | None:
    """The host of an `https://` literal when the `http` permission would accept it."""
    authority = match.rsplit("@", 1)[-1]
    if authority.startswith("["):
        host = authority[1:].split("]", 1)[0]
    else:
        host = authority.split(":", 1)[0]
    host = host.rstrip(".").lower()
    if not host or is_meshloom_domain(host):
        return None
    try:
        return check_host(host)
    except PydanticCustomError:
        return None


def self_names() -> frozenset[str]:
    """The names of the Meshloom machine itself, from its host name (no lookup)."""
    names = set(_SELF_NAMES)
    try:
        hostname = socket.gethostname().strip().rstrip(".").lower()
    except OSError:
        hostname = ""
    if hostname:
        short = hostname.split(".", 1)[0]
        names.update({hostname, short, f"{hostname}.local", f"{short}.local"})
    return frozenset(names)


def _is_self_name(host: str, own: frozenset[str]) -> bool:
    return host in own or host.split(".", 1)[0] == "localhost" or is_meshloom_domain(host)


def _lan_host(scheme: str, authority: str, own: frozenset[str]) -> tuple[str, bool] | None:
    """The `lan_hosts` entry of an `http(s)://` literal and whether its port is a
    service port, or None when `check_lan_host` refuses it or it names this machine.
    The credentials of `user:pass@` are dropped, a scheme's default port too (an
    entry without port allows only that port)."""
    authority = authority.rsplit("@", 1)[-1].lower()
    if authority.startswith("["):
        end = authority.find("]")
        if end < 0:
            return None
        host, rest = authority[1:end], authority[end + 1 :]
        if rest and not rest.startswith(":"):
            return None
        port = rest[1:]
    else:
        if authority.count(":") > 1:
            return None
        host, _, port = authority.partition(":")
        host = host.rstrip(".")
    if not host:
        return None
    if port.isdigit() and int(port) == _DEFAULT_PORTS[scheme]:
        port = ""
    if port:
        entry = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    else:
        entry = host
    try:
        check_lan_host(entry)
    except PydanticCustomError:
        return None
    if _is_self_name(host, own):
        return None
    return entry, bool(port) and int(port) in SERVICE_PORTS


def _open_ended_ids(nodes: list[ast.AST]) -> set[int]:
    """The literal parts of f-strings (and t-strings) followed by a placeholder: a URL
    that runs to their end is cut (`f"http://nas{n}"`), so its host is not suggested."""
    joined = tuple(t for t in (ast.JoinedStr, getattr(ast, "TemplateStr", None)) if t)
    holes = tuple(t for t in (ast.FormattedValue, getattr(ast, "Interpolation", None)) if t)
    ids: set[int] = set()
    for node in nodes:
        if isinstance(node, joined):
            values: list[ast.expr] = getattr(node, "values", [])
            for part, after in zip(values, values[1:], strict=False):
                if isinstance(part, ast.Constant) and isinstance(after, holes):
                    ids.add(id(part))
    return ids


def _docstring_ids(nodes: list[ast.AST]) -> set[int]:
    ids: set[int] = set()
    for node in nodes:
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _collect_nodes(tree: ast.Module) -> list[ast.AST] | None:
    """Every node, walked with an explicit stack; None past `MAX_AST_NODES`."""
    nodes: list[ast.AST] = []
    stack: list[ast.AST] = [tree]
    while stack:
        node = stack.pop()
        nodes.append(node)
        if len(nodes) > MAX_AST_NODES:
            return None
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _is_mutable_value(value: ast.expr) -> bool:
    if isinstance(value, ast.Dict | ast.List | ast.Set | ast.DictComp | ast.ListComp | ast.SetComp):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        return name in _MUTABLE_FACTORIES
    return False


class _Scanner:
    """Checks one node at a time; the caller supplies the nodes (no recursion)."""

    def __init__(self, docstrings: set[int], open_ended: set[int] | None = None) -> None:
        self.reasons: list[AttentionReason] = []
        self.hosts: list[str] = []
        self._seen_hosts: set[str] = set()
        self.lan_hosts: list[LanHostSuggestion] = []
        self._seen_lan: set[str] = set()
        self._url_bytes = 0
        self._url_matches = 0
        self.truncated = False
        self.lan_truncated = False
        self._docstrings = docstrings
        self._open_ended = open_ended or set()
        self._own = self_names()

    def _add(self, code: str, node: ast.AST, detail: str | None = None, info: bool = False):
        self.reasons.append(AttentionReason(code, getattr(node, "lineno", 0), detail, info))

    def _check_module(self, name: str, node: ast.AST) -> None:
        if name.split(".")[0] in _THREAD_MODULES:
            self._add("threading", node, name)
        elif not import_allowed(name):
            self._add("import_not_allowed", node, name)

    def check(self, node: ast.AST) -> None:
        if isinstance(node, ast.Import):
            for alias in node.names:
                self._check_module(alias.name, node)
        elif isinstance(node, ast.ImportFrom):
            self._import_from(node)
        elif isinstance(node, ast.Call):
            self._call(node)
        elif isinstance(node, ast.Global):
            self._add("global_statement", node, ", ".join(node.names), info=True)
        elif isinstance(node, ast.Name) and node.id == "_bot_globals":
            self._add("module_state", node, "_bot_globals", info=True)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            self._string(node)

    def _import_from(self, node: ast.ImportFrom) -> None:
        if node.level:  # the plugin's own package
            return
        module = node.module or ""
        if not import_allowed(module) or module.split(".")[0] in _THREAD_MODULES:
            self._check_module(module, node)
            return
        # `from urllib import request` imports `urllib.request`.
        for alias in node.names:
            name = f"{module}.{alias.name}"
            if _matches(name, DENIED_MODULES):
                self._check_module(name, node)

    def _call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Name):
            if func.id in _FLAGGED_CALLS:
                self._add(_FLAGGED_CALLS[func.id], node)
            elif func.id == "print":
                self._add("print_call", node, info=True)
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            owner, name = func.value.id, func.attr
            if (owner == "os" and name in _OS_PROCESS_CALLS) or owner == "subprocess":
                self._add("process_call", node, f"{owner}.{name}")

    def _string(self, node: ast.Constant) -> None:
        value = node.value
        if not isinstance(value, str):
            return
        if value == "_bot_globals":  # globals()["_bot_globals"]
            self._add("module_state", node, "_bot_globals", info=True)
        self._hosts_from(value, getattr(node, "lineno", 0), id(node) in self._open_ended)
        if id(node) in self._docstrings:
            return
        if len(value) > LONG_LITERAL_BYTES or len(value.encode("utf-8")) > LONG_LITERAL_BYTES:
            self._add("long_literal", node)

    def _exhausted(self) -> None:
        """A byte or URL bound stopped the scan: more hosts of either kind may exist."""
        self.truncated = True
        self.lan_truncated = True

    def _hosts_from(self, value: str, line: int = 0, open_ended: bool = False) -> None:
        lower = value.lower()
        if "https://" not in lower and "http://" not in lower:
            return
        budget = MAX_URL_SCAN_BYTES - self._url_bytes
        if budget <= 0:
            self._exhausted()
            return
        if len(value) > budget:
            value = value[:budget]
            open_ended = True
            self._exhausted()
        self._url_bytes += len(value)
        for match in _URL_RE.finditer(value):
            if self._url_matches >= MAX_URL_MATCHES:
                self._exhausted()
                return
            self._url_matches += 1
            scheme, authority = match.group(1).lower(), match.group(2)
            if scheme == "https":
                host = _https_host(authority)
                if host:
                    self._add_host(host)
                    continue
            if open_ended and match.end() == len(value):
                continue
            lan = _lan_host(scheme, authority, self._own)
            if lan:
                self._add_lan(lan[0], line, lan[1])

    def _add_host(self, host: str) -> None:
        if host in self._seen_hosts:
            return
        if len(self.hosts) >= MAX_PREFILL_HOSTS:
            self.truncated = True
            return
        self._seen_hosts.add(host)
        self.hosts.append(host)

    def _add_lan(self, host: str, line: int, service_port: bool) -> None:
        if host in self._seen_lan:
            return
        if len(self.lan_hosts) >= MAX_LAN_SUGGESTIONS:
            self.lan_truncated = True
            return
        self._seen_lan.add(host)
        self.lan_hosts.append(LanHostSuggestion(host, line, service_port))

    def scan_module_state(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign | ast.AnnAssign) and stmt.value is not None:
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if _is_mutable_value(stmt.value):
                    names = [t.id for t in targets if isinstance(t, ast.Name)]
                    self._add("module_state", stmt, ", ".join(names) or None, info=True)


def _single(code: str, line: int = 0) -> AttentionReport:
    return AttentionReport(reasons=[AttentionReason(code, line)])


def scan_source(source: str) -> AttentionReport:
    """Scan one Python source. Never raises on any `str`: a syntax error, an
    oversized source or one too complex to analyse is a single coded reason
    (`syntax_error`, `source_too_large`, `too_complex`)."""
    if len(source) > MAX_SOURCE_BYTES or len(source.encode("utf-8", "surrogatepass")) > (
        MAX_SOURCE_BYTES
    ):
        return _single("source_too_large")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return _single("syntax_error", getattr(exc, "lineno", None) or 0)
    except (RecursionError, MemoryError):
        return _single("too_complex")
    try:
        nodes = _collect_nodes(tree)
        if nodes is None:
            return _single("too_complex")
        # Source order, so the prefilled hosts follow the file.
        nodes.sort(key=lambda n: (getattr(n, "lineno", 0), getattr(n, "col_offset", 0)))
        scanner = _Scanner(_docstring_ids(nodes), _open_ended_ids(nodes))
        scanner.scan_module_state(tree)
        for node in nodes:
            scanner.check(node)
    except (RecursionError, MemoryError):
        return _single("too_complex")
    reasons = sorted(scanner.reasons, key=lambda r: (r.line, r.code, r.detail or ""))
    return AttentionReport(
        reasons=reasons,
        http_hosts=scanner.hosts,
        truncated=scanner.truncated,
        lan_hosts=scanner.lan_hosts,
        lan_truncated=scanner.lan_truncated,
    )
