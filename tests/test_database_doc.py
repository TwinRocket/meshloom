"""docs/dev/database.md must list exactly the tables and indexes of a fresh database."""

import re
import sqlite3
from pathlib import Path

from app.database import Database

DOC = Path(__file__).resolve().parent.parent / "docs" / "dev" / "database.md"


def _doc_names() -> tuple[set[str], set[str]]:
    text = DOC.read_text(encoding="utf-8")
    tables: set[str] = set()
    for heading in re.findall(r"^### (.+)$", text, flags=re.MULTILINE):
        tables.update(re.findall(r"`([a-z_]+)`", heading))
    indexes = set(re.findall(r"`(idx_[a-z0-9_]+)`", text))
    return tables, indexes


async def test_database_doc_lists_every_table_and_index(tmp_path):
    path = tmp_path / "doc-check.db"
    database = Database(str(path))
    await database.connect()
    await database.disconnect()

    conn = sqlite3.connect(path)
    try:
        rows = conn.execute(
            "SELECT type, name FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' AND type IN ('table', 'index')"
        ).fetchall()
    finally:
        conn.close()
    real_tables = {name for kind, name in rows if kind == "table"}
    real_indexes = {name for kind, name in rows if kind == "index"}

    doc_tables, doc_indexes = _doc_names()
    assert real_tables == doc_tables, (
        f"docs/dev/database.md tables drifted: missing {sorted(real_tables - doc_tables)}, "
        f"unknown {sorted(doc_tables - real_tables)}"
    )
    assert real_indexes == doc_indexes, (
        f"docs/dev/database.md indexes drifted: missing {sorted(real_indexes - doc_indexes)}, "
        f"unknown {sorted(doc_indexes - real_indexes)}"
    )
