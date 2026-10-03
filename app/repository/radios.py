import time
import uuid
from typing import Any

from app.database import db
from app.models import RadioCreate, RadioRecord, RadioUpdate


def _row_to_radio(row: Any) -> RadioRecord:
    keys = row.keys() if hasattr(row, "keys") else []
    return RadioRecord(
        id=row["id"],
        name=row["name"],
        transport=row["transport"],
        serial_port=row["serial_port"] or "" if "serial_port" in keys else "",
        serial_baudrate=row["serial_baudrate"] or 115200 if "serial_baudrate" in keys else 115200,
        tcp_host=row["tcp_host"] or "" if "tcp_host" in keys else "",
        tcp_port=row["tcp_port"] if "tcp_port" in keys else None,
        ble_address=row["ble_address"] or "" if "ble_address" in keys else "",
        ble_pin=row["ble_pin"] or "" if "ble_pin" in keys else "",
        enabled=bool(row["enabled"]) if "enabled" in keys else True,
        auto_connect=bool(row["auto_connect"]) if "auto_connect" in keys else True,
        bound_public_key=row["bound_public_key"] if "bound_public_key" in keys else None,
        identity_state=row["identity_state"] if "identity_state" in keys else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_connected_at=row["last_connected_at"] if "last_connected_at" in keys else None,
        sort_order=row["sort_order"] or 0 if "sort_order" in keys else 0,
    )


class RadioRepository:
    """Repository for managing radios in SQLite."""

    @staticmethod
    async def create(radio: RadioCreate) -> RadioRecord:
        radio_id = radio.id or f"radio_{uuid.uuid4().hex[:8]}"
        now = int(time.time())
        record = RadioRecord(
            id=radio_id,
            name=radio.name,
            transport=radio.transport,
            serial_port=radio.serial_port,
            serial_baudrate=radio.serial_baudrate,
            tcp_host=radio.tcp_host,
            tcp_port=radio.tcp_port,
            ble_address=radio.ble_address,
            ble_pin=radio.ble_pin,
            enabled=radio.enabled,
            auto_connect=radio.auto_connect,
            bound_public_key=radio.bound_public_key,
            identity_state=radio.identity_state,
            created_at=now,
            updated_at=now,
            last_connected_at=None,
            sort_order=radio.sort_order,
        )
        async with db.tx() as conn:
            await conn.execute(
                """
                INSERT INTO radios (
                    id, name, transport, serial_port, serial_baudrate,
                    tcp_host, tcp_port, ble_address, ble_pin,
                    enabled, auto_connect, bound_public_key, identity_state,
                    created_at, updated_at, last_connected_at, sort_order
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.name,
                    record.transport,
                    record.serial_port,
                    record.serial_baudrate,
                    record.tcp_host,
                    record.tcp_port,
                    record.ble_address,
                    record.ble_pin,
                    1 if record.enabled else 0,
                    1 if record.auto_connect else 0,
                    record.bound_public_key,
                    record.identity_state,
                    record.created_at,
                    record.updated_at,
                    record.last_connected_at,
                    record.sort_order,
                ),
            )
        return record

    @staticmethod
    async def get(radio_id: str) -> RadioRecord | None:
        async with db.readonly() as conn:
            async with conn.execute("SELECT * FROM radios WHERE id = ?", (radio_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return _row_to_radio(row)
        return None

    @staticmethod
    async def get_default() -> RadioRecord | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM radios WHERE id = 'default'",
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return _row_to_radio(row)
            async with conn.execute(
                "SELECT * FROM radios ORDER BY sort_order ASC, created_at ASC LIMIT 1",
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return _row_to_radio(row)
        return None

    @staticmethod
    async def list_all() -> list[RadioRecord]:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT * FROM radios ORDER BY sort_order ASC, created_at ASC",
            ) as cursor:
                rows = await cursor.fetchall()
                return [_row_to_radio(r) for r in rows]

    @staticmethod
    async def update(radio_id: str, patch: RadioUpdate) -> RadioRecord | None:
        patch_dict = patch.model_dump(exclude_unset=True)
        if not patch_dict:
            return await RadioRepository.get(radio_id)

        now = int(time.time())
        patch_dict["updated_at"] = now

        if "enabled" in patch_dict and patch_dict["enabled"] is not None:
            patch_dict["enabled"] = 1 if patch_dict["enabled"] else 0
        if "auto_connect" in patch_dict and patch_dict["auto_connect"] is not None:
            patch_dict["auto_connect"] = 1 if patch_dict["auto_connect"] else 0

        set_clause = ", ".join(f"{col} = ?" for col in patch_dict)
        params = list(patch_dict.values()) + [radio_id]

        async with db.tx() as conn:
            async with conn.execute(
                f"UPDATE radios SET {set_clause} WHERE id = ?",
                params,
            ) as cursor:
                if cursor.rowcount == 0:
                    return None
        return await RadioRepository.get(radio_id)

    @staticmethod
    async def delete(radio_id: str, purge_data: bool = False) -> bool:
        if radio_id == "default":
            raise ValueError("Cannot delete the default radio")

        async with db.tx() as conn:
            async with conn.execute("SELECT 1 FROM radios WHERE id = ?", (radio_id,)) as cursor:
                if await cursor.fetchone() is None:
                    return False

            if purge_data:
                await conn.execute("DELETE FROM messages WHERE radio_id = ?", (radio_id,))
                await conn.execute("DELETE FROM raw_packets WHERE radio_id = ?", (radio_id,))
                await conn.execute(
                    "DELETE FROM contact_advert_paths WHERE radio_id = ?", (radio_id,)
                )
                await conn.execute(
                    "DELETE FROM contact_name_history WHERE radio_id = ?", (radio_id,)
                )
                await conn.execute(
                    "DELETE FROM repeater_telemetry_history WHERE radio_id = ?", (radio_id,)
                )
                await conn.execute(
                    "DELETE FROM contact_telemetry_history WHERE radio_id = ?", (radio_id,)
                )
                await conn.execute(
                    "DELETE FROM repeater_pane_cache WHERE radio_id = ?", (radio_id,)
                )
                await conn.execute(
                    "DELETE FROM contact_group_members WHERE radio_id = ?", (radio_id,)
                )
                await conn.execute("DELETE FROM contacts WHERE radio_id = ?", (radio_id,))
                await conn.execute("DELETE FROM channels WHERE radio_id = ?", (radio_id,))

            await conn.execute("DELETE FROM radios WHERE id = ?", (radio_id,))
            return True
