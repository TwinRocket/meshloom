"""Push subscriptions belong to an account (MSHL-290, migration 118)."""

import pytest

from app.repository.auth import UserRepository
from app.repository.push_subscriptions import PushSubscriptionRepository

_KEYS = {"p256dh": "p256", "auth": "auth"}
ADMIN_ENDPOINT = "https://93.184.215.14/push/admin-secret"
MEMBER_ENDPOINT = "https://93.184.215.15/push/member-secret"


@pytest.fixture
async def member(push_owner):
    """Account id 2, the one ``sign_in_as`` signs in."""
    user = await UserRepository.create("test-member", "member")
    assert user.id == 2
    return user


async def _subscribe(client, endpoint: str, label: str = "") -> dict:
    response = await client.post(
        "/api/push/subscribe", json={"endpoint": endpoint, "label": label, **_KEYS}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_subscribe_belongs_to_the_caller_and_lists_only_its_own(member, client, sign_in_as):
    admin_sub = await _subscribe(client, ADMIN_ENDPOINT, "admin phone")
    assert admin_sub["mine"] is True
    assert (await PushSubscriptionRepository.get(admin_sub["id"]))["user_id"] == 1

    sign_in_as("member")
    member_sub = await _subscribe(client, MEMBER_ENDPOINT, "member phone")
    assert (await PushSubscriptionRepository.get(member_sub["id"]))["user_id"] == 2
    listed = (await client.get("/api/push/subscriptions")).json()
    assert [s["id"] for s in listed] == [member_sub["id"]]
    assert listed[0]["endpoint"] == MEMBER_ENDPOINT


@pytest.mark.asyncio
async def test_a_member_cannot_reach_another_accounts_device(member, client, sign_in_as):
    admin_sub = await _subscribe(client, ADMIN_ENDPOINT)
    sign_in_as("member")
    sub_id = admin_sub["id"]
    assert (
        await client.patch(f"/api/push/subscriptions/{sub_id}", json={"label": "x"})
    ).status_code == 404
    assert (await client.delete(f"/api/push/subscriptions/{sub_id}")).status_code == 404
    assert (await client.post(f"/api/push/subscriptions/{sub_id}/test")).status_code == 404
    refused = await client.get("/api/push/subscriptions?scope=all")
    assert refused.status_code == 403
    assert refused.json()["detail"]["params"]["permission"] == "admin"
    assert await PushSubscriptionRepository.get(sub_id) is not None


@pytest.mark.asyncio
async def test_an_admin_lists_and_deletes_every_device_but_never_renames_or_tests_them(
    member, client, sign_in_as, monkeypatch
):
    sign_in_as("member")
    member_sub = await _subscribe(client, MEMBER_ENDPOINT, "member phone")
    monkeypatch.undo()  # back to the default admin (id 1)
    from app.auth import middleware
    from app.auth.models import User
    from app.auth.principal import Principal

    admin = User(id=1, username="test-admin", role="admin", created_at=0, updated_at=0)

    async def _resolve(scope, headers, via, secure):
        result = middleware._Resolution()
        result.principal = Principal(user=admin, via=via, credential="session")
        return result

    monkeypatch.setattr(middleware, "resolve", _resolve)
    own = await _subscribe(client, ADMIN_ENDPOINT, "admin phone")

    assert [s["id"] for s in (await client.get("/api/push/subscriptions")).json()] == [own["id"]]
    every = {s["id"]: s for s in (await client.get("/api/push/subscriptions?scope=all")).json()}
    assert set(every) == {own["id"], member_sub["id"]}
    other = every[member_sub["id"]]
    assert other["mine"] is False
    assert other["owner_username"] == "test-member"
    # The path is the push service's secret for that browser: the origin only, no keys.
    assert other["endpoint"] == "https://93.184.215.15"
    assert other["p256dh"] == other["auth"] == ""
    assert every[own["id"]]["endpoint"] == ADMIN_ENDPOINT

    sub_id = member_sub["id"]
    assert (
        await client.patch(f"/api/push/subscriptions/{sub_id}", json={"label": "x"})
    ).status_code == 404
    assert (await client.post(f"/api/push/subscriptions/{sub_id}/test")).status_code == 404
    assert (await client.delete(f"/api/push/subscriptions/{sub_id}")).status_code == 200
    assert await PushSubscriptionRepository.get(sub_id) is None


@pytest.mark.asyncio
async def test_a_shared_browser_follows_the_account_that_subscribes_it_last(member):
    sub = await PushSubscriptionRepository.create(ADMIN_ENDPOINT, "p", "a", user_id=1)
    moved = await PushSubscriptionRepository.create(ADMIN_ENDPOINT, "p", "a", user_id=2)
    assert moved["id"] == sub["id"]
    assert moved["user_id"] == 2
    # Without an owner (a backup restore) the current one stays.
    kept = await PushSubscriptionRepository.create(ADMIN_ENDPOINT, "p", "a")
    assert kept["user_id"] == 2
    # A new row without an owner goes to the first administrator.
    fresh = await PushSubscriptionRepository.create(MEMBER_ENDPOINT, "p", "a")
    assert fresh["user_id"] == 1


@pytest.mark.asyncio
async def test_only_enabled_owners_holding_send_are_delivered(member):
    admin_sub = await PushSubscriptionRepository.create(ADMIN_ENDPOINT, "p", "a", user_id=1)
    member_sub = await PushSubscriptionRepository.create(MEMBER_ENDPOINT, "p", "a", user_id=2)

    async def ids() -> set[str]:
        return {s["id"] for s in await PushSubscriptionRepository.deliverable()}

    assert await ids() == {admin_sub["id"], member_sub["id"]}
    await UserRepository.update(2, role="viewer")
    assert await ids() == {admin_sub["id"]}
    await UserRepository.update(2, role="member", disabled=True)
    assert await ids() == {admin_sub["id"]}
    # Kept while disabled: it comes back with the account.
    await UserRepository.update(2, disabled=False)
    assert await ids() == {admin_sub["id"], member_sub["id"]}

    assert await UserRepository.delete(2)
    assert await PushSubscriptionRepository.get(member_sub["id"]) is None


async def _stored_owner(sub_id: str):
    from app.repository import push_subscriptions

    async with push_subscriptions.db.readonly() as conn:
        async with conn.execute(
            "SELECT user_id FROM push_subscriptions WHERE id = ?", (sub_id,)
        ) as cursor:
            return (await cursor.fetchone())[0]


@pytest.mark.asyncio
async def test_unowned_devices_count_as_the_first_administrators_without_any_write(test_db):
    sub = await PushSubscriptionRepository.create(ADMIN_ENDPOINT, "p", "a")
    assert sub["user_id"] is None
    assert await PushSubscriptionRepository.deliverable() == []

    admin = await UserRepository.create("first-admin", "admin")
    delivered = await PushSubscriptionRepository.deliverable()
    assert [(s["id"], s["user_id"]) for s in delivered] == [(sub["id"], admin.id)]
    assert [s["id"] for s in await PushSubscriptionRepository.list_for_user(admin.id)] == [
        sub["id"]
    ]
    assert (await PushSubscriptionRepository.get(sub["id"]))["user_id"] == admin.id
    # Reading resolved the owner; nothing was written (a GET never opens a write).
    assert await _stored_owner(sub["id"]) is None


@pytest.mark.asyncio
async def test_listing_never_opens_a_write_transaction(member, monkeypatch):
    from app.repository import push_subscriptions

    await PushSubscriptionRepository.create(ADMIN_ENDPOINT, "p", "a")

    def _no_write():
        raise AssertionError("a listing opened a write transaction")

    monkeypatch.setattr(push_subscriptions.db, "tx", _no_write)
    assert len(await PushSubscriptionRepository.list_for_user(1)) == 1
    assert len(await PushSubscriptionRepository.list_with_owner()) == 1
    assert len(await PushSubscriptionRepository.deliverable()) == 1


@pytest.mark.asyncio
async def test_an_empty_label_keeps_the_owners_label_but_is_not_handed_to_a_new_owner(
    member, client, sign_in_as
):
    first = await _subscribe(client, ADMIN_ENDPOINT, "kitchen tablet")
    again = await _subscribe(client, ADMIN_ENDPOINT, "")
    assert again["id"] == first["id"]
    assert again["label"] == "kitchen tablet"
    assert (await PushSubscriptionRepository.get(first["id"]))["label"] == "kitchen tablet"

    sign_in_as("member")
    moved = await _subscribe(client, ADMIN_ENDPOINT, "")
    assert moved["id"] == first["id"]
    assert moved["mine"] is True
    assert moved["label"] == ""
    stored = await PushSubscriptionRepository.get(first["id"])
    assert (stored["label"], stored["user_id"]) == ("", 2)

    renamed = await _subscribe(client, ADMIN_ENDPOINT, "my phone")
    assert renamed["label"] == "my phone"
