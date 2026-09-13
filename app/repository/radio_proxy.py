"""Persisted radio-proxy listen settings on app_settings."""

from __future__ import annotations

import logging

from app.database import db
from app.radio_proxy.manager import (
    DEFAULT_BIND,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_PORT,
    ProxySettings,
)

logger = logging.getLogger(__name__)

_PROXY_COLUMNS = (
    "radio_proxy_enabled",
    "radio_proxy_bind",
    "radio_proxy_port",
    "radio_proxy_max_clients",
)


async def _app_settings_columns(conn) -> set[str]:
    async with conn.execute("PRAGMA table_info(app_settings)") as cursor:
        return {row[1] for row in await cursor.fetchall()}


class RadioProxyRepository:
    @staticmethod
    async def get() -> ProxySettings:
        async with db.readonly() as conn:
            columns = await _app_settings_columns(conn)
            if not set(_PROXY_COLUMNS) <= columns:
                logger.warning(
                    "Radio proxy settings columns are missing; using defaults until migration 074"
                )
                return ProxySettings()
            async with conn.execute(
                """
                SELECT radio_proxy_enabled, radio_proxy_bind, radio_proxy_port,
                       radio_proxy_max_clients
                FROM app_settings WHERE id = 1
                """
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return ProxySettings()
        return ProxySettings(
            enabled=bool(row["radio_proxy_enabled"]),
            bind=row["radio_proxy_bind"] or DEFAULT_BIND,
            port=int(row["radio_proxy_port"] or DEFAULT_PORT),
            max_clients=int(row["radio_proxy_max_clients"] or DEFAULT_MAX_CLIENTS),
        )

    @staticmethod
    async def update(
        *,
        enabled: bool | None = None,
        bind: str | None = None,
        port: int | None = None,
        max_clients: int | None = None,
    ) -> ProxySettings:
        current = await RadioProxyRepository.get()
        next_settings = ProxySettings(
            enabled=current.enabled if enabled is None else enabled,
            bind=current.bind if bind is None else bind.strip() or DEFAULT_BIND,
            port=current.port if port is None else port,
            max_clients=current.max_clients if max_clients is None else max_clients,
        )
        async with db.tx() as conn:
            columns = await _app_settings_columns(conn)
            if not set(_PROXY_COLUMNS) <= columns:
                raise RuntimeError(
                    "Radio proxy settings columns are missing; restart so migration 074 can apply"
                )
            await conn.execute(
                """
                UPDATE app_settings SET
                    radio_proxy_enabled = ?,
                    radio_proxy_bind = ?,
                    radio_proxy_port = ?,
                    radio_proxy_max_clients = ?
                WHERE id = 1
                """,
                (
                    1 if next_settings.enabled else 0,
                    next_settings.bind,
                    next_settings.port,
                    next_settings.max_clients,
                ),
            )
        return next_settings
