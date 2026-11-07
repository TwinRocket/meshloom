"""The 5.0.0 switch (MSHL-192): bots are local plugins, the bot runtime is gone.

The retired fanout routes, the conversion notice of ``/api/health`` and its
acknowledgement, the compat layout a converted bot runs from, the review gate before
its first start, and a gate that no ``exec``/``eval`` of user code remains in ``app/``.
The migration itself is ``tests/test_migrations/test_122_bots_to_plugins.py``.
"""

import ast
import json
import sys
from pathlib import Path

import pytest

import app.database
from app.plugins import bot_conversion
from app.plugins.host import PluginHostError
from app.repository.plugins import PluginRepository
from tests.test_plugins_api import api, code_of, manager, statuses, wait_until  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ECHO_BOT = "def bot(**kwargs):\n    return kwargs['message_text']\n"


@pytest.fixture(autouse=True)
def _global_db(request, monkeypatch):
    """``bot_conversion`` reads ``app.database.db`` at call time, like the backup
    service; point it at the test database when the test has one."""
    if "test_db" in request.fixturenames or "manager" in request.fixturenames:
        monkeypatch.setattr(app.database, "db", request.getfixturevalue("test_db"))


async def _archive_bot(config_id: str, instance_id: str) -> None:
    async with app.database.db.tx() as conn:
        await conn.execute(
            "INSERT INTO fanout_configs_bots_archive (id, name, enabled, config, scope,"
            " sort_order, created_at, plugin_instance_id, archived_at)"
            " VALUES (?, 'Echo', 1, '{}', '{}', 0, 1, ?, 1)",
            (config_id, instance_id),
        )


async def _converted(enabled: bool = True, source: str = ECHO_BOT) -> dict:
    row = bot_conversion.converted_row(
        label="Echo", source=source, enabled=enabled, fanout_scope={"messages": "all"}
    )
    async with app.database.db.tx() as conn:
        await bot_conversion.insert_converted(conn, row)
    return row


# --- the retired fanout routes ----------------------------------------------------


@pytest.mark.usefixtures("test_db")
async def test_creating_a_bot_answers_410(client):
    response = await client.post(
        "/api/fanout", json={"type": "bot", "name": "Echo", "config": {"code": ECHO_BOT}}
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "bot_type_retired"


@pytest.mark.usefixtures("test_db")
async def test_patching_a_converted_bot_answers_410_and_an_unknown_id_404(client):
    await _archive_bot("bot-1", "instance-1")
    converted = await client.patch("/api/fanout/bot-1", json={"name": "Echo 2"})
    assert converted.status_code == 410
    assert converted.json()["detail"]["code"] == "bot_type_retired"
    assert (await client.patch("/api/fanout/nope", json={"name": "x"})).status_code == 404


def test_the_bot_lock_routes_are_gone():
    from app.main import app

    paths = [getattr(route, "path", "") for route in app.routes]
    assert not [path for path in paths if "until-restart" in path or "/fanout/bots" in path]


def test_bot_is_not_a_fanout_type():
    from app.routers.fanout import _VALID_TYPES

    assert "bot" not in _VALID_TYPES


# --- the notice -------------------------------------------------------------------


@pytest.mark.usefixtures("test_db")
async def test_health_carries_the_notice_until_acknowledged(client):
    from app.routers.health import HealthResponse, build_health_data

    assert HealthResponse(**await build_health_data(False, None)).converted_bots is None
    row = await _converted()
    await _archive_bot("bot-1", row["instance_id"])
    health = HealthResponse(**await build_health_data(False, None))
    assert health.converted_bots == {"converted": 1, "attention": 1}

    response = await client.post("/api/plugins/converted-bots/acknowledge")
    assert response.status_code == 200 and response.json() == {"acknowledged": True}
    assert HealthResponse(**await build_health_data(False, None)).converted_bots is None
    # Idempotent; the instance keeps its own review.
    assert (await client.post("/api/plugins/converted-bots/acknowledge")).status_code == 200
    stored = await PluginRepository.get(row["instance_id"])
    assert stored is not None and stored["status"] == "needs_attention"


@pytest.mark.usefixtures("test_db")
async def test_acknowledging_is_admin_only(client, sign_in_as):
    sign_in_as("member")
    assert (await client.post("/api/plugins/converted-bots/acknowledge")).status_code == 403


# --- the compat layout and the review gate ----------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")
async def test_a_converted_bot_is_written_as_bot_py_beside_the_wrapper(manager):  # noqa: F811
    row = bot_conversion.converted_row(
        label="Echo", source=ECHO_BOT, enabled=False, fanout_scope={}
    )
    directory = manager.code_dir({**row, "manifest": row["manifest"]})
    assert (directory / "bot.py").read_text() == ECHO_BOT
    assert (directory / "compat.py").read_text() == bot_conversion.COMPAT_WRAPPER
    assert json.loads((directory / "manifest.json").read_text())["entry"] == "compat.py:Bot"


@pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")
async def test_the_host_refuses_to_start_a_converted_bot_before_review(manager):  # noqa: F811
    row = await _converted()
    with pytest.raises(PluginHostError) as exc:
        await manager.start(row["instance_id"])
    assert exc.value.code == "attention_required"
    assert manager.get(row["instance_id"]) is None


@pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")
async def test_accepting_the_grant_starts_the_converted_bot(api, manager):  # noqa: F811
    row = await _converted(enabled=False)
    iid = row["instance_id"]
    refused = await api.patch(f"/api/plugins/{iid}", json={"enabled": True})
    assert refused.status_code == 409 and code_of(refused) == "attention_required"
    assert refused.json()["detail"]["params"]["type"] == "review_permissions"
    assert manager.get(iid) is None

    ok = await api.patch(
        f"/api/plugins/{iid}",
        json={"enabled": True, "granted_permissions": row["granted_permissions"]},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["attention"] is None
    instance = manager.get(iid)
    assert instance is not None
    await wait_until(lambda: instance.status == "running", what="running")


# --- no exec/eval of user code ----------------------------------------------------


_DYNAMIC_CODE_BUILTINS = {"exec", "eval", "compile"}
_DYNAMIC_CODE_MODULES = {"runpy"}
_IMPORTLIB_CALLS = {"import_module", "__import__", "exec_module", "load_module"}


def _dynamic_code_uses(tree: ast.AST) -> list[tuple[int, str]]:
    """Every way ``app/`` could run or load code built at run time: a call (or a bare
    reference, which could be called later) to ``exec``/``eval``/``compile`` or through
    ``builtins``, ``runpy``, ``importlib`` loaders, ``__import__``, or an import of
    ``runpy``; an alias (``from builtins import exec as run``) is followed."""
    hits: list[tuple[int, str]] = []
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                root = item.name.split(".")[0]
                if root in _DYNAMIC_CODE_MODULES:
                    hits.append((node.lineno, f"import {item.name}"))
                if root in {"builtins", "importlib"}:
                    aliases[item.asname or root] = root
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in _DYNAMIC_CODE_MODULES:
                hits.append((node.lineno, f"from {node.module} import ..."))
            for item in node.names:
                name = item.asname or item.name
                if root == "builtins" and item.name in _DYNAMIC_CODE_BUILTINS:
                    aliases[name] = f"builtins.{item.name}"
                    hits.append((node.lineno, f"from builtins import {item.name}"))
                if root == "importlib" and item.name in _IMPORTLIB_CALLS | {"util", "machinery"}:
                    aliases[name] = f"importlib.{item.name}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in _DYNAMIC_CODE_BUILTINS | {"__import__"}:
            hits.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            base = node.value
            if isinstance(base, ast.Name):
                module = aliases.get(base.id, base.id)
                if module == "builtins" and node.attr in _DYNAMIC_CODE_BUILTINS | {"__import__"}:
                    hits.append((node.lineno, f"builtins.{node.attr}"))
                elif module in {"importlib", "importlib.util", "importlib.machinery"} and (
                    node.attr in _IMPORTLIB_CALLS
                    or node.attr in {"spec_from_file_location", "SourceFileLoader"}
                ):
                    hits.append((node.lineno, f"{module}.{node.attr}"))
            elif isinstance(base, ast.Attribute) and (
                isinstance(base.value, ast.Name)
                and aliases.get(base.value.id, base.value.id) == "importlib"
                and base.attr in {"util", "machinery"}
                and node.attr in _IMPORTLIB_CALLS | {"spec_from_file_location", "SourceFileLoader"}
            ):
                hits.append((node.lineno, f"importlib.{base.attr}.{node.attr}"))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and aliases.get(node.func.id, "").split(".")[-1]
            in _DYNAMIC_CODE_BUILTINS | {"import_module"}
        ):
            hits.append((node.lineno, node.func.id))
    return sorted(set(hits))


@pytest.mark.parametrize(
    "snippet",
    [
        "exec(code)",
        "eval(code)",
        "compile(code, 'x', 'exec')",
        "import builtins\nbuiltins.exec(code)",
        "import builtins as b\nb.eval(code)",
        "from builtins import exec as run\nrun(code)",
        "import runpy",
        "from runpy import run_path",
        "import importlib\nimportlib.import_module(name)",
        "import importlib.util\nimportlib.util.spec_from_file_location('m', p)",
        "from importlib import import_module as load\nload(name)",
        "__import__(name)",
        "run = exec",
    ],
)
def test_the_dynamic_code_gate_catches_each_spelling(snippet):
    assert _dynamic_code_uses(ast.parse(snippet))


@pytest.mark.parametrize(
    "snippet",
    [
        "import ast\nast.parse(source)",
        "import re\nre.compile('x')",
        "import importlib.metadata\nimportlib.metadata.version('x')",
        "def evaluate(x):\n    return x",
    ],
)
def test_the_dynamic_code_gate_leaves_ordinary_code_alone(snippet):
    # ``re.compile`` is an attribute of ``re``, not the builtin.
    assert _dynamic_code_uses(ast.parse(snippet)) == []


# Reviewed uses, each with a constant module name that no user input reaches: the
# migration runner loads its own ``_NNN_*`` modules, and a stale-.pyc hint names meshcore.
_REVIEWED = {
    "app/migrations/__init__.py": {"importlib.import_module"},
    "app/services/radio_lifecycle.py": {"__import__"},
}


def test_no_dynamic_code_loading_remains_in_app():
    """The in-process bot runtime (``exec`` of user code) is gone: user code only runs
    in a plugin child. A new ``exec``/``eval``/``compile``/``runpy``/``importlib`` loader
    in ``app/`` must be a deliberate decision that updates this test."""
    found: list[str] = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative = path.relative_to(ROOT).as_posix()
        found += [
            f"{relative}:{line} {what}"
            for line, what in _dynamic_code_uses(tree)
            if what not in _REVIEWED.get(relative, set())
        ]
    assert found == []
