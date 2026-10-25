"""Static attention scan of a local plugin source (a converted bot, a `dev check`).

Pure: `scan_source(source)` parses with `ast` (nothing is imported or run) and
returns the reasons a user should review, each with its line, plus the hosts of
the `https://` literals that prefill the `http` permission. Reused by the
bot conversion (`app/plugins/bot_conversion.py`, attention `review_permissions`)
and by `dev check` (Store T4d).

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
from dataclasses import dataclass, field
from typing import Any

from pydantic_core import PydanticCustomError

from app.message_limits import DM_HARD_LIMIT
from app.plugins.permissions import check_host, is_meshloom_domain
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
_HTTPS_HOST_RE = re.compile(r"https://([^/\s?#'\"<>\\]+)", re.IGNORECASE)


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


@dataclass
class AttentionReport:
    reasons: list[AttentionReason] = field(default_factory=list)
    http_hosts: list[str] = field(default_factory=list)
    # True when host extraction stopped at a bound: more hosts may exist.
    truncated: bool = False

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

    def __init__(self, docstrings: set[int]) -> None:
        self.reasons: list[AttentionReason] = []
        self.hosts: list[str] = []
        self._seen_hosts: set[str] = set()
        self._url_bytes = 0
        self._url_matches = 0
        self.truncated = False
        self._docstrings = docstrings

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
        self._hosts_from(value)
        if id(node) in self._docstrings:
            return
        if len(value) > LONG_LITERAL_BYTES or len(value.encode("utf-8")) > LONG_LITERAL_BYTES:
            self._add("long_literal", node)

    def _hosts_from(self, value: str) -> None:
        if "https://" not in value.lower():
            return
        if len(self.hosts) >= MAX_PREFILL_HOSTS:
            self.truncated = True
            return
        budget = MAX_URL_SCAN_BYTES - self._url_bytes
        if budget <= 0:
            self.truncated = True
            return
        if len(value) > budget:
            value = value[:budget]
            self.truncated = True
        self._url_bytes += len(value)
        for match in _HTTPS_HOST_RE.finditer(value):
            if self._url_matches >= MAX_URL_MATCHES or len(self.hosts) >= MAX_PREFILL_HOSTS:
                self.truncated = True
                return
            self._url_matches += 1
            host = _https_host(match.group(1))
            if host and host not in self._seen_hosts:
                self._seen_hosts.add(host)
                self.hosts.append(host)

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
        scanner = _Scanner(_docstring_ids(nodes))
        scanner.scan_module_state(tree)
        for node in nodes:
            scanner.check(node)
    except (RecursionError, MemoryError):
        return _single("too_complex")
    reasons = sorted(scanner.reasons, key=lambda r: (r.line, r.code, r.detail or ""))
    return AttentionReport(reasons=reasons, http_hosts=scanner.hosts, truncated=scanner.truncated)
