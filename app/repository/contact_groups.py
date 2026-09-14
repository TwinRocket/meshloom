import time

from app.database import db
from app.models import ContactGroup


def _normalize_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("Group name cannot be empty")
    return cleaned[:64]


class ContactGroupRepository:
    """Local-only contact groups. Membership cascades when a contact or group is deleted."""

    @staticmethod
    async def list_all() -> list[ContactGroup]:
        async with db.readonly() as conn:
            async with conn.execute(
                """
                SELECT id, name, sort_order, created_at
                FROM contact_groups
                ORDER BY sort_order, name
                """
            ) as cursor:
                group_rows = await cursor.fetchall()
            async with conn.execute(
                "SELECT group_id, public_key FROM contact_group_members"
            ) as cursor:
                member_rows = await cursor.fetchall()

        members_by_group: dict[int, list[str]] = {}
        for row in member_rows:
            members_by_group.setdefault(row["group_id"], []).append(row["public_key"])

        return [
            ContactGroup(
                id=row["id"],
                name=row["name"],
                sort_order=row["sort_order"],
                created_at=row["created_at"],
                public_keys=members_by_group.get(row["id"], []),
            )
            for row in group_rows
        ]

    @staticmethod
    async def get_by_id(group_id: int) -> ContactGroup | None:
        async with db.readonly() as conn:
            async with conn.execute(
                "SELECT id, name, sort_order, created_at FROM contact_groups WHERE id = ?",
                (group_id,),
            ) as cursor:
                row = await cursor.fetchone()
            if not row:
                return None
            async with conn.execute(
                "SELECT public_key FROM contact_group_members WHERE group_id = ?",
                (group_id,),
            ) as cursor:
                member_rows = await cursor.fetchall()
        return ContactGroup(
            id=row["id"],
            name=row["name"],
            sort_order=row["sort_order"],
            created_at=row["created_at"],
            public_keys=[m["public_key"] for m in member_rows],
        )

    @staticmethod
    async def find_id_by_name(name: str) -> int | None:
        needle = _normalize_name(name).lower()
        async with db.readonly() as conn:
            async with conn.execute("SELECT id, name FROM contact_groups") as cursor:
                rows = await cursor.fetchall()
        for row in rows:
            if row["name"].lower() == needle:
                return row["id"]
        return None

    @staticmethod
    async def create(name: str) -> ContactGroup:
        cleaned = _normalize_name(name)
        existing = await ContactGroupRepository.find_id_by_name(cleaned)
        if existing is not None:
            raise ValueError("A group with that name already exists")

        now = int(time.time())
        async with db.tx() as conn:
            async with conn.execute(
                "SELECT COALESCE(MAX(sort_order), -1) + 1 FROM contact_groups"
            ) as cursor:
                row = await cursor.fetchone()
            sort_order = row[0] if row else 0
            async with conn.execute(
                """
                INSERT INTO contact_groups (name, sort_order, created_at)
                VALUES (?, ?, ?)
                """,
                (cleaned, sort_order, now),
            ) as cursor:
                group_id = cursor.lastrowid
        if group_id is None:
            raise RuntimeError("Failed to create contact group")
        created = await ContactGroupRepository.get_by_id(group_id)
        assert created is not None
        return created

    @staticmethod
    async def update(
        group_id: int, *, name: str | None = None, sort_order: int | None = None
    ) -> ContactGroup | None:
        current = await ContactGroupRepository.get_by_id(group_id)
        if current is None:
            return None

        new_name = current.name
        if name is not None:
            new_name = _normalize_name(name)
            other = await ContactGroupRepository.find_id_by_name(new_name)
            if other is not None and other != group_id:
                raise ValueError("A group with that name already exists")

        new_sort = current.sort_order if sort_order is None else sort_order
        async with db.tx() as conn:
            await conn.execute(
                "UPDATE contact_groups SET name = ?, sort_order = ? WHERE id = ?",
                (new_name, new_sort, group_id),
            )
        return await ContactGroupRepository.get_by_id(group_id)

    @staticmethod
    async def delete(group_id: int) -> bool:
        async with db.tx() as conn:
            async with conn.execute(
                "DELETE FROM contact_groups WHERE id = ?", (group_id,)
            ) as cursor:
                return cursor.rowcount > 0

    @staticmethod
    async def set_members(group_id: int, public_keys: list[str]) -> ContactGroup | None:
        current = await ContactGroupRepository.get_by_id(group_id)
        if current is None:
            return None

        normalized: list[str] = []
        seen: set[str] = set()
        for raw in public_keys:
            key = raw.strip().lower()
            if len(key) != 64 or key in seen:
                continue
            seen.add(key)
            normalized.append(key)

        async with db.tx() as conn:
            await conn.execute("DELETE FROM contact_group_members WHERE group_id = ?", (group_id,))
            if normalized:
                placeholders = ",".join("?" * len(normalized))
                async with conn.execute(
                    f"SELECT public_key FROM contacts WHERE public_key IN ({placeholders})",
                    normalized,
                ) as cursor:
                    existing = {row["public_key"] for row in await cursor.fetchall()}
                for key in normalized:
                    if key not in existing:
                        continue
                    await conn.execute(
                        "INSERT INTO contact_group_members (group_id, public_key) VALUES (?, ?)",
                        (group_id, key),
                    )
        return await ContactGroupRepository.get_by_id(group_id)
