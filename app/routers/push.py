"""Web Push subscription management endpoints."""

import asyncio
import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from pywebpush import WebPushException

from app.push.send import send_push
from app.push.vapid import (
    get_vapid_claims,
    get_vapid_private_key,
    get_vapid_public_key,
    normalize_vapid_subject,
    set_cached_vapid_subject,
    vapid_subject_is_usable,
)
from app.repository.push_subscriptions import PushSubscriptionRepository
from app.repository.settings import AppSettingsRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/push", tags=["push"])


# ── Request/response models ─────────────────────────────────────────────


class VapidPublicKeyResponse(BaseModel):
    public_key: str


class PushSubscribeRequest(BaseModel):
    endpoint: str = Field(min_length=1)
    p256dh: str = Field(min_length=1)
    auth: str = Field(min_length=1)
    label: str = ""
    language: Literal["fr", "en"] = "fr"


class PushSubscriptionUpdate(BaseModel):
    label: str | None = None
    language: Literal["fr", "en"] | None = None


class PushDefaultsModel(BaseModel):
    new_contact: bool
    new_dm: bool
    advert_repeater: bool
    advert_companion: bool
    advert_sensor: bool
    channel_found: bool
    telemetry_alert: bool


class PushDefaultsPatch(BaseModel):
    new_contact: bool | None = None
    new_dm: bool | None = None
    advert_repeater: bool | None = None
    advert_companion: bool | None = None
    advert_sensor: bool | None = None
    channel_found: bool | None = None
    telemetry_alert: bool | None = None


class PushPreferencesResponse(BaseModel):
    defaults: PushDefaultsModel
    overrides: dict[str, bool]
    vapid_subject: str


class PushPreferencesPatch(BaseModel):
    defaults: PushDefaultsPatch | None = None
    vapid_subject: str | None = None


class PushConversationOverrideBody(BaseModel):
    override: bool | None = None


def _validate_vapid_subject(subject: str) -> str:
    """Empty stores as '' (env fallback). Non-empty must be a usable mailto: or https origin."""
    stored = normalize_vapid_subject(subject)
    if not stored:
        return ""
    if not vapid_subject_is_usable(stored):
        raise HTTPException(
            status_code=400,
            detail="vapid_subject must be a mailto: address or an https:// origin "
            "(no path or trailing slash)",
        )
    return stored


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("/vapid-public-key", response_model=VapidPublicKeyResponse)
async def vapid_public_key() -> VapidPublicKeyResponse:
    """Return the VAPID public key for browser PushManager.subscribe()."""
    key = get_vapid_public_key()
    if not key:
        raise HTTPException(status_code=423, detail="VAPID keys not initialized")
    return VapidPublicKeyResponse(public_key=key)


@router.post("/subscribe")
async def subscribe(body: PushSubscribeRequest) -> dict:
    """Register or update a push subscription (device). Upserts by endpoint."""
    sub = await PushSubscriptionRepository.create(
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        label=body.label,
        language=body.language,
    )
    return sub


@router.get("/subscriptions")
async def list_subscriptions() -> list[dict]:
    """List all push subscriptions (devices)."""
    return await PushSubscriptionRepository.get_all()


@router.patch("/subscriptions/{subscription_id}")
async def update_subscription(subscription_id: str, body: PushSubscriptionUpdate) -> dict:
    """Update a subscription's label."""
    existing = await PushSubscriptionRepository.get(subscription_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Subscription not found")

    updates = {}
    if body.label is not None:
        updates["label"] = body.label
    if body.language is not None:
        updates["language"] = body.language

    result = await PushSubscriptionRepository.update(subscription_id, **updates)
    return result or existing


@router.delete("/subscriptions/{subscription_id}")
async def unsubscribe(subscription_id: str) -> dict:
    """Delete a push subscription (device)."""
    deleted = await PushSubscriptionRepository.delete(subscription_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"deleted": True}


@router.post("/subscriptions/{subscription_id}/test")
async def test_push(subscription_id: str) -> dict:
    """Send a test notification to a subscription."""
    sub = await PushSubscriptionRepository.get(subscription_id)
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")

    vapid_key = get_vapid_private_key()
    if not vapid_key:
        raise HTTPException(status_code=423, detail="VAPID keys not initialized")

    language = sub.get("language") or "fr"
    if language == "en":
        title, body = "Meshloom Test", "Push notifications are working!"
    else:
        title, body = "Test Meshloom", "Les notifications push fonctionnent !"

    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "tag": "meshcore-test",
            "url_hash": "",
        }
    )

    try:
        async with asyncio.timeout(15):
            await send_push(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
                },
                payload=payload,
                vapid_private_key=vapid_key,
                vapid_claims=get_vapid_claims(),
            )
        return {"status": "sent"}
    except TimeoutError:
        raise HTTPException(status_code=408, detail="Push delivery timed out") from None
    except WebPushException as e:
        status_code = getattr(getattr(e, "response", None), "status_code", 0)
        if status_code in (403, 404, 410):
            logger.info(
                "Test push: subscription stale (HTTP %d), removing %s",
                status_code,
                subscription_id,
            )
            await PushSubscriptionRepository.delete(subscription_id)
            raise HTTPException(
                status_code=410,
                detail="Subscription is stale (VAPID key mismatch or expired). "
                "Re-enable push from a conversation header.",
            ) from None
        logger.warning("Test push failed: %s", e)
        raise HTTPException(status_code=422, detail=f"Push delivery failed: {e}") from None
    except Exception as e:
        logger.warning("Test push failed: %s", e)
        raise HTTPException(status_code=422, detail=f"Push delivery failed: {e}") from None


# ── Preferences ──────────────────────────────────────────────────────────


@router.get("/preferences", response_model=PushPreferencesResponse)
async def get_push_preferences() -> PushPreferencesResponse:
    """Return global push defaults, per-conversation overrides, and VAPID subject."""
    defaults = await AppSettingsRepository.get_push_defaults()
    overrides = await AppSettingsRepository.get_push_conversation_overrides()
    vapid_subject = await AppSettingsRepository.get_vapid_subject()
    return PushPreferencesResponse(
        defaults=PushDefaultsModel(**defaults),
        overrides=overrides,
        vapid_subject=vapid_subject,
    )


@router.patch("/preferences", response_model=PushPreferencesResponse)
async def patch_push_preferences(body: PushPreferencesPatch) -> PushPreferencesResponse:
    """Partially update push defaults and/or the stored VAPID subject."""
    if body.defaults is not None:
        patch = body.defaults.model_dump(exclude_none=True)
        if patch:
            await AppSettingsRepository.set_push_defaults(patch)
    if body.vapid_subject is not None:
        stored = _validate_vapid_subject(body.vapid_subject)
        await AppSettingsRepository.set_vapid_subject(stored)
        set_cached_vapid_subject(stored)
    return await get_push_preferences()


@router.put(
    "/preferences/conversations/{key}",
    response_model=PushPreferencesResponse,
)
async def put_push_conversation_override(
    key: str, body: PushConversationOverrideBody
) -> PushPreferencesResponse:
    """Set (true/false) or clear (null) one conversation override."""
    if not key:
        raise HTTPException(status_code=400, detail="Conversation key is required")
    await AppSettingsRepository.set_push_conversation_override(key, body.override)
    return await get_push_preferences()


@router.api_route("/conversations", methods=["GET", "POST"], include_in_schema=False)
async def removed_push_conversations() -> None:
    """Removed in favor of GET/PATCH /push/preferences."""
    raise HTTPException(status_code=404, detail="Use /api/push/preferences")


@router.api_route("/conversations/toggle", methods=["GET", "POST"], include_in_schema=False)
async def removed_push_conversation_toggle() -> None:
    """Removed in favor of PUT /push/preferences/conversations/{key}."""
    raise HTTPException(status_code=404, detail="Use /api/push/preferences")
