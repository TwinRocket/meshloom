"""``remote_state``, ``remote_devices`` and ``remote_enroll_tokens`` (migration 138).

The Meshloom Remote state of this instance. Rules in ``app/services/remote.py``;
the Remote key itself is ``app_settings.remote_instance_seed``, read only through
``app/services/instance_identity.py``. None of these tables is in a backup, and a
database copy empties them (``SNAPSHOT_EMPTIED_TABLES``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.database import db


class RemoteState(BaseModel):
    enabled: bool = False
    slug: str = ""
    hostname: str = ""
    relay_url: str = ""
    registered_at: int | None = None
    gate_gen: int = 0
    last_error: str | None = None


class RemoteDevice(BaseModel):
    id: str
    user_id: int
    kind: str  # "browser" or "client"
    label: str | None = None
    origin: str  # "qr", "approval" or "client"
    gen: int
    created_at: int
    pass_expires_at: int
    last_seen_at: int | None = None
    revoked_at: int | None = None


class RemoteEnrollToken(BaseModel):
    jti: str
    user_id: int
    gen: int
    created_at: int
    expires_at: int
    used_at: int | None = None


_STATE_FIELDS = (
    "enabled",
    "slug",
    "hostname",
    "relay_url",
    "registered_at",
    "gate_gen",
    "last_error",
)
_DEVICE_COLUMNS = (
    "id, user_id, kind, label, origin, gen, created_at, pass_expires_at, last_seen_at, revoked_at"
)


def _device(row: Any) -> RemoteDevice:
    return RemoteDevice(**dict(row))


class RemoteRepository:
    @staticmethod
    async def get_state() -> RemoteState:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {', '.join(_STATE_FIELDS)} FROM remote_state WHERE id = 1"
            ) as cursor:
                row = await cursor.fetchone()
        if row is None:
            return RemoteState()
        values = {key: row[key] for key in _STATE_FIELDS}
        values["enabled"] = bool(values["enabled"])
        return RemoteState(**values)

    @staticmethod
    async def update_state(**fields: Any) -> None:
        unknown = set(fields) - set(_STATE_FIELDS)
        if unknown:
            raise ValueError(f"unknown remote_state fields: {sorted(unknown)}")
        if not fields:
            return
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        assignments = ", ".join(f"{name} = ?" for name in fields)
        async with db.tx() as conn:
            await conn.execute("INSERT OR IGNORE INTO remote_state (id) VALUES (1)")
            await conn.execute(
                f"UPDATE remote_state SET {assignments} WHERE id = 1", tuple(fields.values())
            )

    @staticmethod
    async def reset_all(*, keep_enabled: bool = False) -> None:
        """Forget the registration, every device and every enrolment token."""
        async with db.tx() as conn:
            await conn.execute("DELETE FROM remote_devices")
            await conn.execute("DELETE FROM remote_enroll_tokens")
            if keep_enabled:
                await conn.execute("INSERT OR IGNORE INTO remote_state (id) VALUES (1)")
                await conn.execute(
                    "UPDATE remote_state SET slug = '', hostname = '', relay_url = '',"
                    " registered_at = NULL, gate_gen = 0, last_error = NULL WHERE id = 1"
                )
            else:
                await conn.execute("DELETE FROM remote_state")
                await conn.execute("INSERT INTO remote_state (id) VALUES (1)")

    # ---------------------------------------------------------------- devices

    @staticmethod
    async def add_device(device: RemoteDevice) -> None:
        async with db.tx() as conn:
            await conn.execute(
                f"INSERT INTO remote_devices ({_DEVICE_COLUMNS})"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device.id,
                    device.user_id,
                    device.kind,
                    device.label,
                    device.origin,
                    device.gen,
                    device.created_at,
                    device.pass_expires_at,
                    device.last_seen_at,
                    device.revoked_at,
                ),
            )

    @staticmethod
    async def delete_device(device_id: str) -> None:
        async with db.tx() as conn:
            await conn.execute("DELETE FROM remote_devices WHERE id = ?", (device_id,))

    @staticmethod
    async def get_device(device_id: str) -> RemoteDevice | None:
        async with db.readonly() as conn:
            async with conn.execute(
                f"SELECT {_DEVICE_COLUMNS} FROM remote_devices WHERE id = ?", (device_id,)
            ) as cursor:
                row = await cursor.fetchone()
        return _device(row) if row is not None else None

    @staticmethod
    async def list_devices(user_id: int | None = None) -> list[RemoteDevice]:
        query = f"SELECT {_DEVICE_COLUMNS} FROM remote_devices"
        params: tuple[Any, ...] = ()
        if user_id is not None:
            query += " WHERE user_id = ?"
            params = (user_id,)
        query += " ORDER BY created_at, id"
        async with db.readonly() as conn:
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [_device(row) for row in rows]

    @staticmethod
    async def revoke_device(device_id: str, now: int) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE remote_devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                (now, device_id),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def revoked_ids(gen: int, now: int, *, grace: int) -> list[str]:
        """Devices revoked within ``gen`` whose pass may still be presented."""
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT id FROM remote_devices WHERE gen = ? AND revoked_at IS NOT NULL"
                " AND pass_expires_at >= ? ORDER BY revoked_at, id",
                (gen, now - grace),
            ) as cursor:
                return [row[0] for row in await cursor.fetchall()]

    @staticmethod
    async def bump_generation(now: int) -> int:
        """Revoke every device: the next generation, every device marked revoked and
        every pending enrolment token dropped. Returns the new generation."""
        async with db.tx() as conn:
            await conn.execute("INSERT OR IGNORE INTO remote_state (id) VALUES (1)")
            await conn.execute("UPDATE remote_state SET gate_gen = gate_gen + 1 WHERE id = 1")
            await conn.execute(
                "UPDATE remote_devices SET revoked_at = ? WHERE revoked_at IS NULL", (now,)
            )
            await conn.execute("DELETE FROM remote_enroll_tokens")
            async with conn.execute("SELECT gate_gen FROM remote_state WHERE id = 1") as cursor:
                row = await cursor.fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    async def extend_device_pass(device_id: str, expires_at: int) -> bool:
        """A renewed pass (never earlier than the stored expiry). False when the device
        is gone or revoked: that pass must not be handed out."""
        async with db.tx() as conn:
            async with conn.execute(
                "UPDATE remote_devices SET pass_expires_at = MAX(pass_expires_at, ?)"
                " WHERE id = ? AND revoked_at IS NULL",
                (expires_at, device_id),
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def touch_device(device_id: str, now: int) -> None:
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE remote_devices SET last_seen_at = ? WHERE id = ?", (now, device_id)
            )

    # ---------------------------------------------------------------- enrolment

    @staticmethod
    async def add_enroll_token(token: RemoteEnrollToken, now: int) -> None:
        async with db.tx() as conn:
            # Expired tokens are dead weight: drop them on every issue.
            await conn.execute("DELETE FROM remote_enroll_tokens WHERE expires_at < ?", (now - 60,))
            await conn.execute(
                "INSERT INTO remote_enroll_tokens"
                " (jti, user_id, gen, created_at, expires_at, used_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    token.jti,
                    token.user_id,
                    token.gen,
                    token.created_at,
                    token.expires_at,
                    token.used_at,
                ),
            )

    @staticmethod
    async def consume_enroll_token(jti: str, now: int) -> RemoteEnrollToken | None:
        """Mark the token used and return it; None when unknown, used or expired."""
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT jti, user_id, gen, created_at, expires_at, used_at"
                " FROM remote_enroll_tokens WHERE jti = ?",
                (jti,),
            ) as cursor:
                row = await cursor.fetchone()
            if row is None or row["used_at"] is not None or row["expires_at"] < now - 60:
                return None
            async with conn.execute(
                "UPDATE remote_enroll_tokens SET used_at = ? WHERE jti = ? AND used_at IS NULL",
                (now, jti),
            ) as cursor:
                if cursor.rowcount == 0:
                    return None
        return RemoteEnrollToken(**dict(row))

    @staticmethod
    async def enroll_token_known(jti: str) -> bool:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT 1 FROM remote_enroll_tokens WHERE jti = ?", (jti,)
            ) as cursor:
                return await cursor.fetchone() is not None
