"""Shared access seam over active RadioInstance objects via RadioRegistry.

This module deliberately keeps behavior thin and forwarding-only. The goal is
to reduce direct `app.radio.radio_manager` imports across routers and helpers
while supporting radio resolution by ID via `radio_registry.get(radio_id)`.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException


class RadioRuntime:
    """Thin forwarding wrapper around the active RadioInstance or RadioRegistry."""

    def __init__(self, manager_or_getter: Any = None, radio_id: str | None = None):
        self._radio_id = radio_id
        if manager_or_getter is not None:
            if callable(manager_or_getter):
                self._manager_getter: Callable[[], Any] | None = manager_or_getter
            else:
                self._manager_getter = lambda: manager_or_getter
        else:
            self._manager_getter = None

    @property
    def manager(self) -> Any:
        if self._manager_getter is not None:
            return self._manager_getter()
        return self.get(self._radio_id)

    @manager.setter
    def manager(self, value: Any) -> None:
        if value is None:
            self._manager_getter = None
        elif callable(value):
            self._manager_getter = value
        else:
            self._manager_getter = lambda: value

    def get(self, radio_id: str | None = None) -> Any:
        """Resolve a RadioInstance by ID, defaulting to current manager if unset."""
        if radio_id is None and self._manager_getter is not None:
            return self._manager_getter()
        import app.services.radio_registry as registry_module

        return registry_module.radio_registry.get(radio_id)

    def __getattr__(self, name: str) -> Any:
        """Forward unknown attributes to the current manager."""
        return getattr(self.manager, name)

    @staticmethod
    def _is_local_runtime_attr(name: str) -> bool:
        return name in ("_radio_id", "_manager_getter") or name in RadioRuntime.__dict__

    def __setattr__(self, name: str, value: Any) -> None:
        if self._is_local_runtime_attr(name):
            super().__setattr__(name, value)
            return
        setattr(self.manager, name, value)

    def __delattr__(self, name: str) -> None:
        if self._is_local_runtime_attr(name):
            super().__delattr__(name)
            return
        delattr(self.manager, name)

    def require_connected(self, radio_id: str | None = None):
        """Return MeshCore when available, mirroring existing HTTP semantics."""
        target = self.get(radio_id) if radio_id is not None else self.manager
        if target.is_setup_in_progress:
            raise HTTPException(
                status_code=423,
                detail={"code": "radio_initializing", "message": "Radio is initializing"},
            )
        if not target.is_connected:
            raise HTTPException(
                status_code=423,
                detail={"code": "radio_not_connected", "message": "Radio not connected"},
            )
        mc = target.meshcore
        if mc is None:
            raise HTTPException(
                status_code=423,
                detail={"code": "radio_not_connected", "message": "Radio not connected"},
            )
        return mc

    @asynccontextmanager
    async def radio_operation(self, name: str, **kwargs):
        radio_id = kwargs.pop("radio_id", None)
        target = self.get(radio_id) if radio_id is not None else self.manager
        async with target.radio_operation(name, **kwargs) as mc:
            yield mc

    async def start_connection_monitor(self, radio_id: str | None = None) -> None:
        target = self.get(radio_id) if radio_id is not None else self.manager
        await target.start_connection_monitor()

    async def stop_connection_monitor(self, radio_id: str | None = None) -> None:
        target = self.get(radio_id) if radio_id is not None else self.manager
        await target.stop_connection_monitor()

    async def disconnect(self, radio_id: str | None = None) -> None:
        target = self.get(radio_id) if radio_id is not None else self.manager
        await target.disconnect()

    async def prepare_connected(
        self, *, radio_id: str | None = None, broadcast_on_success: bool = True
    ) -> bool:
        from app.services.radio_lifecycle import prepare_connected_radio

        target = self.get(radio_id) if radio_id is not None else self.manager
        return await prepare_connected_radio(
            target, broadcast_on_success=broadcast_on_success
        )

    async def reconnect_and_prepare(
        self, *, radio_id: str | None = None, broadcast_on_success: bool = True
    ) -> bool:
        from app.services.radio_lifecycle import reconnect_and_prepare_radio

        target = self.get(radio_id) if radio_id is not None else self.manager
        return await reconnect_and_prepare_radio(
            target,
            broadcast_on_success=broadcast_on_success,
        )


radio_runtime = RadioRuntime()
