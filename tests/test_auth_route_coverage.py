"""Every route and every WebSocket event type is classified (app/auth/permissions.py).

A new route ships only with a permission (``Depends(require(...))``), the
``public()`` marker (and a ``PUBLIC_ROUTES`` entry) or ``own_account()``; a new
path outside ``/api`` only with a ``NON_API_ACCESS`` entry. Every WebSocket
event the app emits is a ``WsEvent`` member, which carries its permission
(``app/auth/ws_events.py``). An undeclared ``/api`` route that slips through
anyway is admin-only (default deny); an unclassified event reaches no one.
"""

from __future__ import annotations

import ast
import asyncio
import pathlib
from typing import Any, get_args
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute, APIWebSocketRoute
from starlette.routing import Mount, Route

from app.auth.middleware import NON_API_ACCESS, PUBLIC_ROUTES
from app.auth.models import User
from app.auth.permissions import PERMISSIONS, declared_access
from app.auth.principal import Principal
from app.auth.ws_events import WsEvent
from app.events import _PAYLOAD_ADAPTERS
from app.websocket import WebSocketManager

APP_DIR = pathlib.Path(__file__).resolve().parent.parent / "app"


def _walk(routes: list[Any], prefix: str = "") -> list[tuple[str, Any]]:
    """Every route with its full path, through FastAPI's lazy includes."""
    out: list[tuple[str, Any]] = []
    for route in routes:
        context = getattr(route, "include_context", None)
        if context is not None:
            out.extend(_walk(context.included_router.routes, prefix + context.prefix))
            continue
        out.append((prefix + route.path, route))
    return out


def _all_routes() -> list[tuple[str, Any]]:
    from app.main import app

    return _walk(app.routes)


def _frontend_routes(tmp_path: pathlib.Path) -> list[tuple[str, Any]]:
    """The routes ``app/main.py`` registers when a frontend build is present."""
    from app.frontend_static import register_frontend_static_routes

    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html></html>")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    assert register_frontend_static_routes(app, tmp_path)
    return _walk(app.routes)


def _is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _methods(route: Any) -> set[str]:
    return set(getattr(route, "methods", None) or {"GET"}) - {"HEAD", "OPTIONS"}


def test_the_walk_finds_every_router():
    paths = {path for path, _ in _all_routes()}
    assert "/api/ws" in paths
    assert "/api/contacts/{public_key}/repeater/status" in paths
    assert "/api/settings/backup/restore" in paths
    assert {"/docs", "/openapi.json"} <= paths
    assert len(paths) >= 150


def test_every_api_route_declares_exactly_one_access_rule():
    missing, several, unknown = [], [], []
    for path, route in _all_routes():
        if not _is_api(path):
            continue
        assert isinstance(route, (APIRoute, APIWebSocketRoute)), path
        declared = declared_access(route)
        if not declared:
            missing.append(path)
        elif len(declared) > 1:
            several.append((path, declared))
        elif declared[0] not in (*PERMISSIONS, "public", "account"):
            unknown.append((path, declared))
    assert not missing, f"Routes without a permission: {missing}"
    assert not several, f"Routes with more than one declaration: {several}"
    assert not unknown, unknown


def test_public_markers_and_public_routes_agree():
    marked: set[tuple[str, str]] = set()
    for path, route in _all_routes():
        if _is_api(path) and declared_access(route) == ["public"]:
            marked |= {(method, path) for method in _methods(route)}
    assert marked == set(PUBLIC_ROUTES)


def test_every_route_outside_api_is_classified(tmp_path):
    routes = _all_routes() + _frontend_routes(tmp_path)
    outside = {path: route for path, route in routes if not _is_api(path)}
    unclassified = sorted(set(outside) - set(NON_API_ACCESS))
    assert not unclassified, f"Add these paths to NON_API_ACCESS: {unclassified}"
    assert {"/", "/assets", "/{path:path}", "/site.webmanifest"} <= set(outside)
    for path, route in outside.items():
        assert isinstance(route, (APIRoute, Route, Mount)), path
    for path, access in NON_API_ACCESS.items():
        assert access in ("public", "authenticated"), path
        if access == "authenticated":
            # The gate matches these by exact path.
            assert "{" not in path, path
    assert NON_API_ACCESS["/docs"] == NON_API_ACCESS["/openapi.json"] == "authenticated"


def test_classification_of_representative_routes():
    expected = {
        ("GET", "/api/messages"): "read",
        ("POST", "/api/messages/channel"): "send",
        ("POST", "/api/messages/direct"): "send",
        ("PATCH", "/api/settings"): "admin",
        ("GET", "/api/settings"): "admin",
        ("GET", "/api/settings/view"): "read",
        ("GET", "/api/fanout"): "admin",
        ("POST", "/api/contacts/{public_key}/repeater/status"): "repeater_admin",
        ("POST", "/api/contacts/{public_key}/room/login"): "repeater_admin",
        ("GET", "/api/radio/private-key"): "admin",
        ("GET", "/api/debug"): "admin",
        ("POST", "/api/settings/backup/export"): "admin",
        ("PATCH", "/api/push/preferences"): "admin",
        ("POST", "/api/updates/apply"): "admin",
        ("PATCH", "/api/community"): "admin",
        ("POST", "/api/channels/{key}/mark-read"): "read_private",
        ("GET", "/api/channels/rejected"): "read",
        ("GET", "/api/packets/history"): "read",
        ("GET", "/api/auth/me"): "account",
        ("GET", "/api/health"): "public",
        ("POST", "/api/share/links"): "send",
        ("GET", "/api/share/status"): "read",
        ("GET", "/api/share/{share_id}/meta"): "read",
        ("GET", "/api/share/{share_id}"): "read",
        ("POST", "/api/share/cache/clear"): "admin",
        ("GET", "/api/share/mine"): "send",
        ("DELETE", "/api/share/{share_id}"): "send",
        ("GET", "/api/store/status"): "admin",
        ("POST", "/api/store/refresh"): "admin",
        ("GET", "/api/store/repositories/{owner}/{name}/readme"): "admin",
        ("GET", "/api/store/updates"): "admin",
        ("WS", "/api/ws"): "read",
    }
    found: dict[tuple[str, str], str] = {}
    for path, route in _all_routes():
        if not _is_api(path):
            continue
        methods = {"WS"} if isinstance(route, APIWebSocketRoute) else _methods(route)
        for method in methods:
            found[(method, path)] = declared_access(route)[0]
    for key, access in expected.items():
        assert found[key] == access, key


# The calls that emit a WebSocket event, and the position of their event type.
# ``broadcast_fn`` is ``broadcast_event`` injected into the services.
_EMITTERS = {
    "broadcast_event": 0,
    "broadcast_fn": 0,
    "broadcast": 0,
    "broadcast_nowait": 0,
    "send_personal": 1,
}
# Only the manager itself forwards an event type it was given.
_FORWARDERS = {"app/websocket.py"}
# ``send_raw`` queues a frame past every filter: only these literal frames.
_RAW_FRAMES = {("app/routers/ws.py", '{"type":"pong"}')}


def _aliases(tree: ast.AST) -> dict[str, str]:
    """Local names bound to an emitter or ``send_raw`` (``import ... as``, ``x = y``)."""
    known = {*_EMITTERS, "send_raw"}
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in known and alias.asname:
                    aliases[alias.asname] = alias.name
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
            value_name = value.id if isinstance(value, ast.Name) else getattr(value, "attr", None)
            if isinstance(target, ast.Name) and value_name in known:
                aliases[target.id] = value_name
    return aliases


def _emitted_event_types() -> tuple[set[str], list[str]]:
    """The ``WsEvent`` members every emit call in ``app/`` passes, and the call
    sites that pass anything else (a string, an expression, a non-member, a
    ``send_raw`` frame outside ``_RAW_FRAMES``)."""
    emitted: set[str] = set()
    refused: list[str] = []
    for source in APP_DIR.rglob("*.py"):
        tree = ast.parse(source.read_text(), str(source))
        aliases = _aliases(tree)
        relative = source.relative_to(APP_DIR.parent).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            name = aliases.get(name, name) if name is not None else None
            where = f"{relative}:{node.lineno}"
            if name == "send_raw":
                frame = node.args[1] if len(node.args) > 1 else None
                literal = frame.value if isinstance(frame, ast.Constant) else None
                if (relative, literal) not in _RAW_FRAMES:
                    refused.append(f"{where}: send_raw({ast.unparse(frame) if frame else ''})")
                continue
            if name not in _EMITTERS:
                continue
            index = _EMITTERS[name]
            arg = node.args[index] if len(node.args) > index else None
            if arg is None:
                arg = next((k.value for k in node.keywords if k.arg == "event_type"), None)
            if (
                isinstance(arg, ast.Attribute)
                and isinstance(arg.value, ast.Name)
                and arg.value.id == "WsEvent"
                and arg.attr in WsEvent.__members__
            ):
                emitted.add(WsEvent[arg.attr].value)
            elif isinstance(arg, ast.Name) and relative in _FORWARDERS:
                continue
            else:
                refused.append(f"{where}: {ast.unparse(arg) if arg else '(no event type)'}")
    return emitted, refused


def test_every_ws_event_type_has_a_permission_and_a_payload_model():
    for member in WsEvent:
        assert member.permission in PERMISSIONS, member
    assert set(_PAYLOAD_ADAPTERS) == set(WsEvent)


def test_every_emitted_ws_event_is_a_ws_event_member():
    emitted, refused = _emitted_event_types()
    assert refused == [], "Pass a WsEvent member (app/auth/ws_events.py), never a string"
    assert {"message", "raw_packet", "health", "community_packet", "radio_wifi"} <= emitted
    # Every member is emitted somewhere: a dead type would only widen the surface.
    assert emitted == {member.value for member in WsEvent}


def test_the_emit_scan_catches_a_string_event_type(tmp_path, monkeypatch):
    module = tmp_path / "app"
    module.mkdir()
    (module / "rogue.py").write_text(
        "broadcast_event('message', {})\n"
        "ws_manager.broadcast_nowait(WsEvent.NOT_A_MEMBER, {})\n"
        "await ws_manager.send_personal(ws, kind, {})\n"
        "broadcast_fn(WsEvent.CONTACT, {})\n"
        "from app.websocket import broadcast_event as emit\n"
        "emit('channel', {})\n"
        "shout = ws_manager.broadcast_nowait\n"
        "shout(WsEvent.HEALTH, {})\n"
        'ws_manager.send_raw(ws, \'{"type": "message"}\')\n'
    )
    monkeypatch.setattr(f"{__name__}.APP_DIR", module)
    emitted, refused = _emitted_event_types()
    assert emitted == {"contact", "health"}
    # ast.walk is breadth-first: compare without the order.
    assert sorted(line.split(": ", 1)[1] for line in refused) == sorted(
        [
            "'message'",
            "WsEvent.NOT_A_MEMBER",
            "kind",
            "'channel'",
            'send_raw(\'{"type": "message"}\')',
        ]
    )


async def test_an_unclassified_ws_event_type_reaches_no_one():
    admin = Principal(
        user=User(id=1, username="admin", role="admin", created_at=0, updated_at=0),
        via="lan",
        credential="session",
    )
    manager = WebSocketManager()
    ws = AsyncMock()
    await manager.connect(ws, admin)
    manager.broadcast_nowait("not_a_known_event", {"x": 1})  # type: ignore[arg-type]
    await manager.send_personal(ws, "not_a_known_event", {"x": 1})  # type: ignore[arg-type]
    manager.broadcast_nowait(WsEvent.SUCCESS, {"message": "ok"})
    for _ in range(20):
        await asyncio.sleep(0)
    assert [call.args[0] for call in ws.send_text.await_args_list] == [
        '{"type": "success", "data": {"message": "ok"}}'
    ]
    await manager.disconnect(ws)


@pytest.fixture
def undeclared_route():
    """A route with no declaration, added to the real app for one test."""
    from app.main import app

    async def undeclared() -> dict:
        return {"ok": True}

    app.router.add_api_route("/api/test-undeclared-route", undeclared, methods=["GET"])
    # First, ahead of the frontend's catch-all route when a build is present.
    added = app.router.routes.pop()
    app.router.routes.insert(0, added)
    try:
        yield "/api/test-undeclared-route"
    finally:
        app.router.routes.remove(added)


@pytest.mark.parametrize(
    ("role", "status"), [("admin", 200), ("member", 403), ("viewer", 403), ("kiosk", 403)]
)
async def test_an_undeclared_api_route_is_admin_only(
    undeclared_route, sign_in_as, client, role, status
):
    sign_in_as(role)
    response = await client.get(undeclared_route)
    assert response.status_code == status
    if status == 403:
        assert response.json()["detail"]["code"] == "auth_forbidden"


def test_every_role_has_a_permission_set():
    from app.auth.models import Role
    from app.auth.permissions import ROLE_PERMISSIONS

    assert set(ROLE_PERMISSIONS) == set(get_args(Role))
