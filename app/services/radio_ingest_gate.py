"""Process-wide ingest gate so handlers cannot write during identity transitions."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_ingest_allowed = False
_session_generation = 0


def begin_connection_session() -> int:
    """Close ingest and bump the session id. Return the new generation."""
    global _ingest_allowed, _session_generation
    _ingest_allowed = False
    _session_generation += 1
    logger.debug("Radio ingest closed (session %s)", _session_generation)
    return _session_generation


def allow_ingest(session: int | None = None) -> None:
    global _ingest_allowed
    if session is not None and session != _session_generation:
        return
    _ingest_allowed = True
    logger.debug("Radio ingest allowed (session %s)", _session_generation)


def deny_ingest() -> None:
    global _ingest_allowed
    _ingest_allowed = False


def ingest_allowed() -> bool:
    return _ingest_allowed
