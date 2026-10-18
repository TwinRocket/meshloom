# Updated automatically when a new migration is added.  Migration tests that
# run ``run_migrations`` to completion assert ``get_version == LATEST`` and
# ``applied == LATEST - starting_version`` so only this constant needs to
# change, not every individual assertion.
# 112 is reserved for another lane (MSHL-71): the numbers are not contiguous, so tests count
# pending migrations with ``migrations_after`` instead of subtracting.
LATEST_SCHEMA_VERSION = 113


def migrations_after(version: int) -> int:
    """How many migration files come after ``version`` (a reserved number may be missing)."""
    import re
    from pathlib import Path

    folder = Path(__file__).resolve().parents[2] / "app" / "migrations"
    numbers = (re.match(r"_(\d+)_", p.name) for p in folder.glob("_*.py"))
    return sum(1 for m in numbers if m and int(m.group(1)) > version)
