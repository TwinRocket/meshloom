"""Multi-radio management REST router.

Exposes CRUD and connection lifecycle operations for multiple MeshCore radios.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from app.models import (
    RadioCreate,
    RadioRecord,
    RadioStatusResponse,
    RadioTestRequest,
    RadioTestResponse,
    RadioUpdate,
)
from app.repository.radios import RadioRepository
from app.services.radio_instance import RadioInstance
from app.services.radio_lifecycle import reconnect_and_prepare_radio
from app.services.radio_registry import radio_registry
from app.websocket import broadcast_event, broadcast_health

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/radios", tags=["radios"])


def _validate_transport_and_ssrf(
    transport: str | None,
    *,
    tcp_host: str | None = None,
    tcp_port: int | None = None,
    ble_address: str | None = None,
    ble_pin: str | None = None,
) -> None:
    """Validate candidate transport parameters and prevent proxy self-loops (SSRF)."""
    if transport == "tcp":
        host = (tcp_host or "").strip()
        if not host:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tcp_host is required when transport is tcp",
            )
        port = tcp_port if tcp_port is not None else 5000
        if port < 1 or port > 65535:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="tcp_port must be between 1 and 65535",
            )
        from app.radio_proxy.manager import radio_proxy_manager

        if radio_proxy_manager.would_loop_transport(host, port):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TCP radio target points at this Meshloom radio proxy (loop detected)",
            )
    elif transport == "ble":
        if not (ble_address or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ble_address is required when transport is ble",
            )


def _build_radio_response(
    record: RadioRecord, instance: RadioInstance | None
) -> RadioStatusResponse:
    """Combine persisted record with real-time runtime state."""
    is_connected = False
    is_reconnecting = False
    connection_info = None
    last_error = None
    device_model = None
    firmware_version = None

    if instance is not None:
        is_connected = bool(instance.is_connected)
        is_reconnecting = bool(instance.is_reconnecting)
        connection_info = instance.connection_info
        last_error = instance.last_error
        device_model = instance.device_model
        firmware_version = instance.firmware_version

    return RadioStatusResponse(
        id=record.id,
        name=record.name,
        transport=record.transport,
        serial_port=record.serial_port,
        serial_baudrate=record.serial_baudrate,
        tcp_host=record.tcp_host,
        tcp_port=record.tcp_port,
        ble_address=record.ble_address,
        ble_pin_configured=bool(record.ble_pin),
        enabled=record.enabled,
        auto_connect=record.auto_connect,
        bound_public_key=record.bound_public_key,
        identity_state=record.identity_state,
        created_at=record.created_at,
        updated_at=record.updated_at,
        last_connected_at=record.last_connected_at,
        sort_order=record.sort_order,
        is_connected=is_connected,
        is_reconnecting=is_reconnecting,
        connection_info=connection_info,
        last_error=last_error,
        device_model=device_model,
        firmware_version=firmware_version,
    )


@router.get("", response_model=list[RadioStatusResponse])
async def list_radios() -> list[RadioStatusResponse]:
    """List all configured radios with real-time status."""
    records = await RadioRepository.list_all()
    results: list[RadioStatusResponse] = []
    for record in records:
        instance = None
        if radio_registry.has(record.id):
            try:
                instance = radio_registry.get(record.id)
            except Exception:
                pass
        results.append(_build_radio_response(record, instance))
    return results


@router.post("", response_model=RadioStatusResponse, status_code=status.HTTP_201_CREATED)
async def create_radio(radio_in: RadioCreate) -> RadioStatusResponse:
    """Create a new radio configuration and register its runtime instance."""
    if radio_in.id:
        existing = await RadioRepository.get(radio_in.id)
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Radio '{radio_in.id}' already exists",
            )

    _validate_transport_and_ssrf(
        radio_in.transport,
        tcp_host=radio_in.tcp_host,
        tcp_port=radio_in.tcp_port,
        ble_address=radio_in.ble_address,
        ble_pin=radio_in.ble_pin,
    )

    record = await RadioRepository.create(radio_in)
    instance = RadioInstance(
        radio_id=record.id,
        name=record.name,
        transport_snapshot=record.to_transport_snapshot(),
    )
    instance.connection_desired = bool(record.enabled)
    instance.auto_connect = bool(record.auto_connect)
    radio_registry.register(instance)

    if record.auto_connect and record.enabled:
        await instance.start_connection_monitor()
        asyncio.create_task(reconnect_and_prepare_radio(instance, broadcast_on_success=True))

    resp = _build_radio_response(record, instance)
    broadcast_event("radio_created", resp.model_dump(), radio_id=record.id)
    return resp


@router.post("/test", response_model=RadioTestResponse)
async def test_radio_candidate(candidate: RadioTestRequest) -> RadioTestResponse:
    """Dry-run connectivity test for a candidate transport configuration."""
    _validate_transport_and_ssrf(
        candidate.transport,
        tcp_host=candidate.tcp_host,
        tcp_port=candidate.tcp_port,
        ble_address=candidate.ble_address,
        ble_pin=candidate.ble_pin,
    )

    if candidate.transport == "tcp":
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(candidate.tcp_host, candidate.tcp_port),
                timeout=3.0,
            )
            writer.close()
            await writer.wait_closed()
            return RadioTestResponse(
                success=True,
                message=f"Successfully reached {candidate.tcp_host}:{candidate.tcp_port}",
            )
        except Exception as exc:
            return RadioTestResponse(
                success=False,
                message=f"TCP connection failed: {exc}",
            )
    elif candidate.transport == "serial":
        from app.services.radio_instance import test_serial_device

        try:
            success = await test_serial_device(
                candidate.serial_port, candidate.serial_baudrate, timeout=3.0
            )
            if success:
                return RadioTestResponse(
                    success=True,
                    message=f"MeshCore device responded on {candidate.serial_port}",
                )
            return RadioTestResponse(
                success=False,
                message=f"No MeshCore response on {candidate.serial_port}",
            )
        except Exception as exc:
            return RadioTestResponse(
                success=False,
                message=f"Serial test failed: {exc}",
            )
    elif candidate.transport == "ble":
        return RadioTestResponse(
            success=True,
            message=f"BLE candidate validated for address {candidate.ble_address}",
        )

    return RadioTestResponse(success=True, message="Transport configuration accepted")


@router.get("/{radio_id}", response_model=RadioStatusResponse)
async def get_radio(radio_id: str) -> RadioStatusResponse:
    """Return radio detail and live status."""
    record = await RadioRepository.get(radio_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Radio '{radio_id}' not found",
        )
    instance = None
    if radio_registry.has(radio_id):
        try:
            instance = radio_registry.get(radio_id)
        except Exception:
            pass
    return _build_radio_response(record, instance)


@router.patch("/{radio_id}", response_model=RadioStatusResponse)
async def patch_radio(radio_id: str, patch: RadioUpdate) -> RadioStatusResponse:
    """Update radio configuration and sync active instance."""
    existing = await RadioRepository.get(radio_id)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Radio '{radio_id}' not found",
        )

    target_transport = patch.transport if patch.transport is not None else existing.transport
    target_host = patch.tcp_host if patch.tcp_host is not None else existing.tcp_host
    target_port = patch.tcp_port if patch.tcp_port is not None else existing.tcp_port
    target_ble_addr = patch.ble_address if patch.ble_address is not None else existing.ble_address
    target_ble_pin = patch.ble_pin if patch.ble_pin is not None else existing.ble_pin

    _validate_transport_and_ssrf(
        target_transport,
        tcp_host=target_host,
        tcp_port=target_port,
        ble_address=target_ble_addr,
        ble_pin=target_ble_pin,
    )

    transport_changed = (
        (patch.transport is not None and patch.transport != existing.transport)
        or (patch.serial_port is not None and patch.serial_port != existing.serial_port)
        or (patch.serial_baudrate is not None and patch.serial_baudrate != existing.serial_baudrate)
        or (patch.tcp_host is not None and patch.tcp_host != existing.tcp_host)
        or (patch.tcp_port is not None and patch.tcp_port != existing.tcp_port)
        or (patch.ble_address is not None and patch.ble_address != existing.ble_address)
        or (patch.ble_pin is not None and patch.ble_pin != existing.ble_pin)
    )
    enabled_changed = patch.enabled is not None and patch.enabled != existing.enabled

    updated_record = await RadioRepository.update(radio_id, patch)
    if updated_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Radio '{radio_id}' not found",
        )

    if radio_registry.has(radio_id):
        instance = radio_registry.get(radio_id)
    else:
        instance = RadioInstance(
            radio_id=updated_record.id,
            name=updated_record.name,
            transport_snapshot=updated_record.to_transport_snapshot(),
        )
        radio_registry.register(instance)

    instance.name = updated_record.name
    instance._transport_snapshot = updated_record.to_transport_snapshot()
    instance.connection_desired = bool(updated_record.enabled)
    instance.auto_connect = bool(updated_record.auto_connect)

    if transport_changed or enabled_changed:
        if not updated_record.enabled:
            await instance.pause_connection()
            await instance.stop_connection_monitor()
        else:
            if instance.is_connected:
                await instance.disconnect()
            instance.resume_connection()
            if updated_record.auto_connect:
                await instance.start_connection_monitor()
                asyncio.create_task(
                    reconnect_and_prepare_radio(instance, broadcast_on_success=True)
                )

    resp = _build_radio_response(updated_record, instance)
    broadcast_event("radio_updated", resp.model_dump(), radio_id=radio_id)
    return resp


@router.delete("/{radio_id}")
async def delete_radio(radio_id: str, purge_data: bool = Query(default=False)) -> dict[str, Any]:
    """Delete a radio configuration. Rejects deletion of the default radio."""
    if radio_id == "default":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete the default radio",
        )

    record = await RadioRepository.get(radio_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Radio '{radio_id}' not found",
        )

    instance = radio_registry.unregister(radio_id)
    if instance is not None:
        with suppress(Exception):
            await instance.stop_connection_monitor()
        with suppress(Exception):
            await instance.disconnect()

    try:
        await RadioRepository.delete(radio_id, purge_data=purge_data)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    broadcast_event("radio_deleted", {"radio_id": radio_id}, radio_id=radio_id)
    return {
        "status": "ok",
        "message": f"Radio '{radio_id}' deleted",
        "purged": purge_data,
    }


@router.post("/{radio_id}/connect")
async def connect_radio_endpoint(radio_id: str) -> dict[str, Any]:
    """Trigger connect action on target radio instance."""
    record = await RadioRepository.get(radio_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Radio '{radio_id}' not found",
        )

    if radio_registry.has(radio_id):
        instance = radio_registry.get(radio_id)
    else:
        instance = RadioInstance(
            radio_id=record.id,
            name=record.name,
            transport_snapshot=record.to_transport_snapshot(),
        )
        radio_registry.register(instance)

    if instance.is_connected:
        return {"status": "ok", "message": "Already connected", "connected": True}

    instance.resume_connection()
    success = await reconnect_and_prepare_radio(instance, broadcast_on_success=True)
    if not success:
        raise HTTPException(
            status_code=423,
            detail={"code": "radio_not_connected", "message": "Failed to connect to radio"},
        )
    return {"status": "ok", "message": "Connected successfully", "connected": True}


@router.post("/{radio_id}/disconnect")
async def disconnect_radio_endpoint(radio_id: str) -> dict[str, Any]:
    """Trigger disconnect action on target radio instance."""
    record = await RadioRepository.get(radio_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Radio '{radio_id}' not found",
        )

    if radio_registry.has(radio_id):
        instance = radio_registry.get(radio_id)
        await instance.pause_connection()
        broadcast_health(False, instance.connection_info, radio_id=radio_id)

    return {
        "status": "ok",
        "message": f"Radio '{radio_id}' disconnected",
        "connected": False,
    }


@router.post("/{radio_id}/reconnect")
async def reconnect_radio_endpoint(radio_id: str) -> dict[str, Any]:
    """Trigger reconnect action on target radio instance."""
    record = await RadioRepository.get(radio_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Radio '{radio_id}' not found",
        )

    if radio_registry.has(radio_id):
        instance = radio_registry.get(radio_id)
    else:
        instance = RadioInstance(
            radio_id=record.id,
            name=record.name,
            transport_snapshot=record.to_transport_snapshot(),
        )
        radio_registry.register(instance)

    if instance.is_connected:
        await instance.disconnect()

    instance.resume_connection()
    success = await reconnect_and_prepare_radio(instance, broadcast_on_success=True)
    if not success:
        raise HTTPException(
            status_code=423,
            detail={"code": "radio_not_connected", "message": "Failed to reconnect to radio"},
        )
    return {"status": "ok", "message": "Reconnected successfully", "connected": True}
