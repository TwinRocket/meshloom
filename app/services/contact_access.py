"""Shared contact lookup and radio staging used by contact/repeater/room routers."""

from fastapi import HTTPException
from meshcore import EventType

from app.models import Contact
from app.repository import AmbiguousPublicKeyPrefixError, ContactRepository


def ambiguous_contact_detail(err: AmbiguousPublicKeyPrefixError) -> str:
    sample = ", ".join(key[:12] for key in err.matches[:2])
    return (
        f"Ambiguous contact key prefix '{err.prefix}'. "
        f"Use a full 64-character public key. Matching contacts: {sample}"
    )


async def resolve_contact_or_404(
    public_key: str,
    not_found_detail: str = "Contact not found",
    radio_id: str = "default",
) -> Contact:
    try:
        contact = await ContactRepository.get_by_key_or_prefix(public_key, radio_id=radio_id)
    except AmbiguousPublicKeyPrefixError as err:
        raise HTTPException(status_code=409, detail=ambiguous_contact_detail(err)) from err
    if not contact:
        raise HTTPException(status_code=404, detail=not_found_detail)
    return contact


async def ensure_on_radio(mc, contact: Contact) -> None:
    """Add a contact to the radio for routing, raising 422 on failure."""
    add_result = await mc.commands.add_contact(contact.to_radio_dict())
    if add_result is not None and add_result.type == EventType.ERROR:
        raise HTTPException(
            status_code=422, detail=f"Failed to add contact to radio: {add_result.payload}"
        )
