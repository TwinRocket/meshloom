import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.permissions import require
from app.models import (
    ContactGroup,
    ContactGroupCreate,
    ContactGroupMembersUpdate,
    ContactGroupUpdate,
)
from app.repository import ContactGroupRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/contact-groups", tags=["contacts"])


@router.get("", response_model=list[ContactGroup], dependencies=[Depends(require("read"))])
async def list_contact_groups() -> list[ContactGroup]:
    """Return all local contact groups with member public keys (sidebar-ready)."""
    return await ContactGroupRepository.list_all()


@router.post("", response_model=ContactGroup, dependencies=[Depends(require("send"))])
async def create_contact_group(request: ContactGroupCreate) -> ContactGroup:
    try:
        group = await ContactGroupRepository.create(request.name)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    logger.info("Created contact group %s (id=%d)", group.name, group.id)
    return group


@router.patch("/{group_id}", response_model=ContactGroup, dependencies=[Depends(require("send"))])
async def update_contact_group(group_id: int, request: ContactGroupUpdate) -> ContactGroup:
    try:
        group = await ContactGroupRepository.update(
            group_id, name=request.name, sort_order=request.sort_order
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group


@router.delete("/{group_id}", dependencies=[Depends(require("send"))])
async def delete_contact_group(group_id: int) -> dict:
    deleted = await ContactGroupRepository.delete(group_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Group not found")
    return {"status": "ok"}


@router.put(
    "/{group_id}/members", response_model=ContactGroup, dependencies=[Depends(require("send"))]
)
async def replace_contact_group_members(
    group_id: int, request: ContactGroupMembersUpdate
) -> ContactGroup:
    group = await ContactGroupRepository.set_members(group_id, request.public_keys)
    if group is None:
        raise HTTPException(status_code=404, detail="Group not found")
    return group
