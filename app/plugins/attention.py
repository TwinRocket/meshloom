"""Static attention scan of a local plugin source (a converted bot, a `dev check`).

Pure: `scan_source(source)` parses with `ast` (nothing is imported or run) and
returns the reasons a user should review, each with its line, plus the hosts of
the `https://` literals that prefill the `http` permission. Reused by the
switch (Store T9, `attention: migrated`) and by `dev check` (Store T4d).

Blocking reasons name what the sandbox will refuse (the call fails at run time
with the error in the journal; the plugin still starts). Informative reasons
name what behaves differently (`print` goes to the journal, module state is
lost on each restart).
"""

import ast
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_core import PydanticCustomError

from app.message_limits import DM_HARD_LIMIT
from app.plugins.permissions import check_host, is_meshloom_domain

# Mirror of `ALLOWED_IMPORTS` / `DENIED_MODULES` in `meshloom_plugin/policy.py`
# (MSHL-176): `tests/test_plugin_attention.py` compares them when that module is
# importable. A local source has no `vendor/`, so any other name is flagged.
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
_THREAD_MODULES = frozenset({"threading", "_thread", "concurrent", "multiprocessing"})
_FLAGGED_CALLS = {"open": "open_call", "exec": "exec_call", "eval": "eval_call"}
_MUTABLE_FACTORIES = frozenset(
    {"dict", "list", "set", "bytearray", "defaultdict", "deque", "Counter", "OrderedDict"}
)
# The longest message one send can carry (a DM; a channel allows fewer bytes).
LONG_LITERAL_BYTES = DM_HARD_LIMIT
MAX_PREFILL_HOSTS = 20
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

    @property
    def needs_attention(self) -> bool:
        return bool(self.reasons)

    def to_json(self) -> dict[str, Any]:
        """The shape stored in `plugins.attention` (with the caller's `type`)."""
        return {
            "reasons": [reason.to_json() for reason in self.reasons],
            "http_hosts": list(self.http_hosts),
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


def _docstring_nodes(tree: ast.Module) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
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


def _is_mutable_value(value: ast.expr) -> bool:
    if isinstance(value, ast.Dict | ast.List | ast.Set | ast.DictComp | ast.ListComp | ast.SetComp):
        return True
    if isinstance(value, ast.Call):
        func = value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        return name in _MUTABLE_FACTORIES
    return False


class _Scanner(ast.NodeVisitor):
    def __init__(self, docstrings: set[int]) -> None:
        self.reasons: list[AttentionReason] = []
        self.hosts: list[str] = []
        self._docstrings = docstrings

    def _add(self, code: str, node: ast.AST, detail: str | None = None, info: bool = False):
        self.reasons.append(AttentionReason(code, getattr(node, "lineno", 0), detail, info))

    def _check_module(self, name: str, node: ast.AST) -> None:
        if name.split(".")[0] in _THREAD_MODULES:
            self._add("threading", node, name)
        elif not import_allowed(name):
            self._add("import_not_allowed", node, name)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name, node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
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

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name):
            if node.func.id in _FLAGGED_CALLS:
                self._add(_FLAGGED_CALLS[node.func.id], node)
            elif node.func.id == "__import__":
                self._add("import_call", node)
            elif node.func.id == "print":
                self._add("print_call", node, info=True)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self._add("global_statement", node, ", ".join(node.names), info=True)

    def visit_Name(self, node: ast.Name) -> None:
        if node.id == "_bot_globals":
            self._add("module_state", node, "_bot_globals", info=True)

    def visit_Constant(self, node: ast.Constant) -> None:
        if not isinstance(node.value, str):
            return
        if node.value == "_bot_globals":  # globals()["_bot_globals"]
            self._add("module_state", node, "_bot_globals", info=True)
        for match in _HTTPS_HOST_RE.findall(node.value):
            host = _https_host(match)
            if host and host not in self.hosts:
                self.hosts.append(host)
        if id(node) in self._docstrings:
            return
        if len(node.value.encode("utf-8")) > LONG_LITERAL_BYTES:
            self._add("long_literal", node)

    def scan_module_state(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign | ast.AnnAssign) and stmt.value is not None:
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                if _is_mutable_value(stmt.value):
                    names = [t.id for t in targets if isinstance(t, ast.Name)]
                    self._add("module_state", stmt, ", ".join(names) or None, info=True)


def scan_source(source: str) -> AttentionReport:
    """Scan one Python source. A syntax error is a single `syntax_error` reason."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        line = getattr(exc, "lineno", None) or 0
        return AttentionReport(reasons=[AttentionReason("syntax_error", line)])
    scanner = _Scanner(_docstring_nodes(tree))
    scanner.scan_module_state(tree)
    scanner.visit(tree)
    reasons = sorted(scanner.reasons, key=lambda r: (r.line, r.code, r.detail or ""))
    return AttentionReport(reasons=reasons, http_hosts=scanner.hosts[:MAX_PREFILL_HOSTS])
