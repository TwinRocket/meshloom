"""Store status (``/api/store``): what the Store activation card says. No egress.

The consent itself is ``store_online`` in ``PATCH /api/settings``; every Store
request goes through ``app.plugins.store_egress`` (``store_fetch``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth.permissions import require
from app.models import StoreStatus
from app.plugins.store_egress import store_status

router = APIRouter(prefix="/store", tags=["store"])


@router.get("/status", response_model=StoreStatus, dependencies=[Depends(require("admin"))])
async def get_store_status() -> StoreStatus:
    """The Store consent, the hosts it may reach, what leaves and what is missed when off."""
    return await store_status()
