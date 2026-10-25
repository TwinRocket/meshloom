"""Web Push subscription management endpoints.

A subscription belongs to the account that subscribed it (MSHL-290, migration 118). Every
account holding ``send`` lists, renames, tests and deletes its own devices only; another
account's device answers 404, as if it did not exist. An administrator also lists every
device (``GET /push/subscriptions?scope=all``, with the owner's username and the endpoint
reduced to its origin) and may delete any of them, but neither renames nor tests another
account's device. A token acts for its account; ``kiosk`` holds no ``send`` and owns none.
"""

import asyncio
import json
import logging
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from pywebpush import WebPushException

from app.auth.permissions import forbidden, has_permission, require
from app.auth.principal import Principal, get_principal
from app.net.egress_guard import EgressBlocked, egress_detail, validate_destination
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


class NotificationMediaModel(BaseModel):
    push: bool = True
    email: bool = False
    webhook: bool = False
    apprise: bool = False


class NotificationMediaPatch(BaseModel):
    push: bool | None = None
    email: bool | None = None
    webhook: bool | None = None
    apprise: bool | None = None


class PushDefaultsModel(BaseModel):
    new_contact: NotificationMediaModel
    new_dm: NotificationMediaModel
    advert_repeater: NotificationMediaModel
    advert_companion: NotificationMediaModel
    advert_sensor: NotificationMediaModel
    channel_found: NotificationMediaModel
    telemetry_alert: NotificationMediaModel
    oss_update: NotificationMediaModel
    radio_offline: NotificationMediaModel
    radio_online: NotificationMediaModel
    app_started: NotificationMediaModel


class PushDefaultsPatch(BaseModel):
    new_contact: NotificationMediaPatch | bool | None = None
    new_dm: NotificationMediaPatch | bool | None = None
    advert_repeater: NotificationMediaPatch | bool | None = None
    advert_companion: NotificationMediaPatch | bool | None = None
    advert_sensor: NotificationMediaPatch | bool | None = None
    channel_found: NotificationMediaPatch | bool | None = None
    telemetry_alert: NotificationMediaPatch | bool | None = None
    oss_update: NotificationMediaPatch | bool | None = None
    radio_offline: NotificationMediaPatch | bool | None = None
    radio_online: NotificationMediaPatch | bool | None = None
    app_started: NotificationMediaPatch | bool | None = None


class PushPreferencesResponse(BaseModel):
    defaults: PushDefaultsModel
    overrides: dict[str, dict[str, bool]]
    vapid_subject: str


class PushPreferencesPatch(BaseModel):
    defaults: PushDefaultsPatch | None = None
    vapid_subject: str | None = None


class PushConversationOverrideBody(BaseModel):
    override: bool | NotificationMediaPatch | None = None


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


def _caller(request: Request) -> Principal:
    principal = get_principal(request)
    if principal is None:  # the ``send`` declaration already refused it
        raise forbidden("send")
    return principal


def _public_view(sub: dict, caller_id: int) -> dict:
    """A subscription as the API returns it: ``mine`` tells the caller's devices apart.

    Another account's device (the administrator's listing) keeps only the endpoint's
    origin: its path is the push service's secret for that browser, and the keys too.
    """
    out = {
        key: sub[key]
        for key in (
            "id",
            "endpoint",
            "p256dh",
            "auth",
            "label",
            "language",
            "created_at",
            "last_success_at",
            "failure_count",
        )
    }
    mine = sub.get("user_id") == caller_id
    out["mine"] = mine
    out["owner_username"] = sub.get("owner_username")
    if not mine:
        parts = urlsplit(sub["endpoint"])
        out["endpoint"] = f"{parts.scheme}://{parts.netloc}" if parts.netloc else ""
        out["p256dh"] = ""
        out["auth"] = ""
    return out


async def _owned(subscription_id: str, principal: Principal, *, admin_ok: bool) -> dict:
    """The caller's subscription, or any one for an administrator when ``admin_ok``; 404
    otherwise (another account's device is not revealed)."""
    sub = await PushSubscriptionRepository.get(subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub.get("user_id") == principal.user_id:
        return sub
    if admin_ok and has_permission(principal, "admin"):
        return sub
    raise HTTPException(status_code=404, detail="Subscription not found")


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get(
    "/vapid-public-key",
    response_model=VapidPublicKeyResponse,
    dependencies=[Depends(require("read"))],
)
async def vapid_public_key() -> VapidPublicKeyResponse:
    """Return the VAPID public key for browser PushManager.subscribe()."""
    key = get_vapid_public_key()
    if not key:
        raise HTTPException(status_code=423, detail="VAPID keys not initialized")
    return VapidPublicKeyResponse(public_key=key)


@router.post("/subscribe", dependencies=[Depends(require("send"))])
async def subscribe(body: PushSubscribeRequest, request: Request) -> dict:
    """Register or update this browser's push subscription. Upserts by endpoint.

    The caller's account owns it, even when the endpoint was another account's (a shared
    browser follows whoever subscribes it last). The endpoint must pass the egress
    guard: a private-network endpoint (a self-hosted UnifiedPush distributor) needs
    ``push_allow_private_endpoints``.
    """
    principal = _caller(request)
    allow_private = (await AppSettingsRepository.get()).push_allow_private_endpoints
    try:
        await validate_destination(body.endpoint, allow_private_network=allow_private)
    except EgressBlocked as exc:
        logger.info("Push subscribe refused by the egress guard: %s", exc.code)
        raise HTTPException(status_code=400, detail=egress_detail(exc.code)) from None
    sub = await PushSubscriptionRepository.create(
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        label=body.label,
        language=body.language,
        user_id=principal.user_id,
    )
    return _public_view(sub, principal.user_id)


@router.get("/subscriptions", dependencies=[Depends(require("send"))])
async def list_subscriptions(
    request: Request, scope: Literal["mine", "all"] = "mine"
) -> list[dict]:
    """The caller's devices; ``scope=all`` (administrator only) lists every account's."""
    principal = _caller(request)
    if scope == "all":
        if not has_permission(principal, "admin"):
            raise forbidden("admin")
        subs = await PushSubscriptionRepository.list_with_owner()
    else:
        subs = await PushSubscriptionRepository.list_for_user(principal.user_id)
    return [_public_view(sub, principal.user_id) for sub in subs]


@router.patch("/subscriptions/{subscription_id}", dependencies=[Depends(require("send"))])
async def update_subscription(
    subscription_id: str, body: PushSubscriptionUpdate, request: Request
) -> dict:
    """Update the label or language of one of the caller's devices."""
    principal = _caller(request)
    existing = await _owned(subscription_id, principal, admin_ok=False)

    updates = {}
    if body.label is not None:
        updates["label"] = body.label
    if body.language is not None:
        updates["language"] = body.language

    result = await PushSubscriptionRepository.update(subscription_id, **updates)
    return _public_view(result or existing, principal.user_id)


@router.delete("/subscriptions/{subscription_id}", dependencies=[Depends(require("send"))])
async def unsubscribe(subscription_id: str, request: Request) -> dict:
    """Delete one of the caller's devices; an administrator may delete any device."""
    await _owned(subscription_id, _caller(request), admin_ok=True)
    deleted = await PushSubscriptionRepository.delete(subscription_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"deleted": True}


# The test route answers a fixed code, never the exception text: the endpoint
# is client-supplied, so a network error would reveal what lives on the LAN.
# The egress guard refuses a private endpoint before any connection (unless
# push_allow_private_endpoints), so neither the status code nor the answer time
# says anything about the LAN: every delivery failure, timeout included, is a
# 422, and push_stale (410) only follows an answer from a guarded endpoint.
# The cause goes to the server log only. Translated by the UI under errors.<code>.
_TEST_PUSH_MESSAGES = {
    "push_timeout": "Push delivery timed out",
    "push_stale": "Subscription is stale (VAPID key mismatch or expired). "
    "Re-enable push from a conversation header.",
    "push_delivery_failed": "Push delivery failed",
}


def _coded(code: str) -> dict:
    return {"code": code, "message": _TEST_PUSH_MESSAGES[code]}


@router.post("/subscriptions/{subscription_id}/test", dependencies=[Depends(require("send"))])
async def test_push(subscription_id: str, request: Request) -> dict:
    """Send a test notification to one of the caller's devices."""
    sub = await _owned(subscription_id, _caller(request), admin_ok=False)

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
    allow_private = (await AppSettingsRepository.get()).push_allow_private_endpoints

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
                allow_private_network=allow_private,
            )
        return {"status": "sent"}
    except EgressBlocked as e:
        logger.info("Test push refused by the egress guard: %s", e.code)
        raise HTTPException(status_code=422, detail=egress_detail(e.code)) from None
    except TimeoutError:
        raise HTTPException(status_code=422, detail=_coded("push_timeout")) from None
    except WebPushException as e:
        status_code = getattr(getattr(e, "response", None), "status_code", 0)
        if status_code in (403, 404, 410):
            logger.info(
                "Test push: subscription stale (HTTP %d), removing %s",
                status_code,
                subscription_id,
            )
            await PushSubscriptionRepository.delete(subscription_id)
            raise HTTPException(status_code=410, detail=_coded("push_stale")) from None
        # The type only: a WebPushException carries the push service's body.
        logger.warning("Test push failed: %s", type(e).__name__)
        raise HTTPException(status_code=422, detail=_coded("push_delivery_failed")) from None
    except Exception as e:
        logger.warning("Test push failed: %s", type(e).__name__)
        raise HTTPException(status_code=422, detail=_coded("push_delivery_failed")) from None


# ── Preferences (deprecated shims) ───────────────────────────────────────
#
# Removed in 5.2.0 (MSHL-111): the data is read and written through
# /api/notifications/rules now. Until the producers read the rules (MSHL-112) these
# routes keep writing the 4.18.x columns that still drive delivery, and the repository
# mirrors every write into the rules and the ``legacy:*`` channels
# (``app/notifications/legacy_sync.py``). Channels other than ``builtin:push`` and the
# ``legacy:*`` ones are invisible here.


DEPRECATED_SINCE = "@1792368000"


def deprecated_route(response: Response) -> None:
    """Mark a response of a 4.18.x route that is kept for one release."""
    # RFC 9745: the date the route was deprecated (2026-10-19, 5.0.0); removal is in 5.2.0.
    response.headers["Deprecation"] = DEPRECATED_SINCE
    response.headers["Link"] = '</api/notifications/rules>; rel="successor-version"'


async def _require_legacy_channels(raw: bool | dict[str, bool] | None) -> None:
    """400 ``channel_missing`` when an override turns on a medium whose legacy channel
    does not exist (no SMTP host, no URL: it could not deliver)."""
    from app.migrations._113_notification_channels import MEDIUM_CHANNEL
    from app.repository.notification_channels import NotificationChannelRepository

    if not isinstance(raw, dict):
        return
    for medium, on in raw.items():
        channel_id = MEDIUM_CHANNEL.get(medium)
        if (
            on is True
            and channel_id
            and await NotificationChannelRepository.get(channel_id) is None
        ):
            raise HTTPException(
                status_code=400,
                detail={"code": "channel_missing", "params": {"medium": medium}},
            )


@router.get(
    "/preferences",
    response_model=PushPreferencesResponse,
    dependencies=[Depends(require("admin")), Depends(deprecated_route)],
    deprecated=True,
)
async def get_push_preferences() -> PushPreferencesResponse:
    """Return global push defaults, per-conversation overrides, and VAPID subject."""
    defaults = await AppSettingsRepository.get_push_defaults()
    overrides = await AppSettingsRepository.get_push_conversation_overrides()
    vapid_subject = await AppSettingsRepository.get_vapid_subject()
    return PushPreferencesResponse(
        defaults=PushDefaultsModel.model_validate(defaults),
        overrides=overrides,
        vapid_subject=vapid_subject,
    )


@router.patch(
    "/preferences",
    response_model=PushPreferencesResponse,
    dependencies=[Depends(require("admin")), Depends(deprecated_route)],
    deprecated=True,
)
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
    dependencies=[Depends(require("admin")), Depends(deprecated_route)],
    deprecated=True,
)
async def put_push_conversation_override(
    key: str, body: PushConversationOverrideBody
) -> PushPreferencesResponse:
    """Set, merge, or clear (null) one conversation media override."""
    if not key:
        raise HTTPException(status_code=400, detail="Conversation key is required")
    override = body.override
    raw: bool | dict[str, bool] | None
    if isinstance(override, NotificationMediaPatch):
        raw = override.model_dump(exclude_none=True)
    else:
        raw = override
    await _require_legacy_channels(raw if isinstance(raw, dict) else None)
    await AppSettingsRepository.set_push_conversation_override(key, raw)
    return await get_push_preferences()


@router.api_route(
    "/conversations",
    methods=["GET", "POST"],
    include_in_schema=False,
    dependencies=[Depends(require("admin"))],
)
async def removed_push_conversations() -> None:
    """Removed in favor of GET/PATCH /push/preferences."""
    raise HTTPException(status_code=404, detail="Use /api/push/preferences")


@router.api_route(
    "/conversations/toggle",
    methods=["GET", "POST"],
    include_in_schema=False,
    dependencies=[Depends(require("admin"))],
)
async def removed_push_conversation_toggle() -> None:
    """Removed in favor of PUT /push/preferences/conversations/{key}."""
    raise HTTPException(status_code=404, detail="Use /api/push/preferences")
