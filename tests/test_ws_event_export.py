"""frontend/src/wsEventTypes.ts is generated from WsEvent and kept in sync.

``frontend/src/wsEvents.ts`` proves at compile time (``tsc``) that its
``KnownWsEvent`` union covers exactly the generated types, so a server-side
change to the enumeration fails CI until the frontend handles it.
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _exporter():
    spec = importlib.util.spec_from_file_location(
        "export_ws_events", ROOT / "scripts/build/export_ws_events.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ws_event_types_ts_is_up_to_date():
    exporter = _exporter()
    assert exporter.main(["--check"]) == 0, (
        "frontend/src/wsEventTypes.ts is stale: run scripts/build/export_ws_events.py"
    )


def test_the_generated_file_lists_every_member_with_its_permission():
    from app.auth.ws_events import WsEvent

    content = _exporter().render()
    for member in WsEvent:
        assert f"  {member.value}: '{member.permission}',\n" in content
    assert "export type WsEventType = keyof typeof WS_EVENT_PERMISSIONS;" in content
    # And wsEvents.ts consumes it, with the compile-time check.
    ws_events_ts = (ROOT / "frontend/src/wsEvents.ts").read_text()
    assert "from './wsEventTypes'" in ws_events_ts
    assert "export type KnownWsEventsMatchServer = Assert<" in ws_events_ts
