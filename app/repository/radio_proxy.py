"""Persisted radio-proxy listen settings on app_settings."""

from __future__ import annotations

from app.database import db
from app.radio_proxy.manager import (
    DEFAULT_BIND,
    DEFAULT_MAX_CLIENTS,
    DEFAULT_PORT,
    ProxySettings,
)


class RadioProxyRepository:
    @staticmethod
    async def get() -> ProxySettings:
        async with db.readonly() as conn:
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
