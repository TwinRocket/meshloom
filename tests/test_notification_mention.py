"""``message.mention``: the matcher, the rules and the producer (MSHL-121)."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest

from app.notifications import events as ev
from app.notifications import render
from app.notifications import rules as rules_mod
from app.notifications.dispatcher import notification_dispatcher
from app.notifications.mention import _matcher, is_mention
from app.repository.channels import ChannelRepository
from app.repository.notification_channels import NotificationChannelRepository

PUBLIC_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
HASHTAG_KEY = "A1B2C3D4E5F60718293A4B5C6D7E8F90"
PRIVATE_KEY = "00112233445566778899AABBCCDDEEFF"


# --- the matcher ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("hello @[Alpha] how are you", True),
        ("@[alpha]", True),
        ("@[ALPHA] ping", True),
        ("Alpha, are you there?", False),  # the name alone is not the token
        ("@Alpha", False),
        ("@[Alph]", False),
        ("@[Alpha", False),
        ("", False),
    ],
)
def test_token_match_is_case_insensitive(text, expected):
    assert is_mention(text, "Alpha") is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Al, are you there?", True),
        ("hey al!", True),
        ("(AL)", True),
        ("Alpha is here", False),  # "Al" is not "Alpha"
        ("final", False),
        ("Hal", False),
        ("@[Al]", True),
    ],
)
def test_whole_word_option(text, expected):
    assert is_mention(text, "Al", whole_word=True) is expected
    # Off: only the token counts.
    assert is_mention(text, "Al") is (text == "@[Al]")


def test_no_radio_name_never_matches():
    for name in (None, "", "   "):
        assert not is_mention("@[] and everything", name)
        assert not is_mention("@[] and everything", name, whole_word=True)


@pytest.mark.parametrize(
    "name",
    [
        "a.c",
        "a+b",
        "(x)",
        "[bob]",
        "a|b",
        "^x$",
        "x*",
        "a{2}",
        "back\\slash",
        "(?i)evil",
        "(a+)+$",
        "?",
        ".*",
    ],
)
def test_regex_metacharacters_in_a_name_are_literal(name):
    assert is_mention(f"hi @[{name}] there", name)
    assert is_mention(f"hi @[{name.upper()}] there", name)
    assert is_mention(f"hi {name} there", name, whole_word=True)
    # A pattern-looking name does not match what the pattern would.
    other = "abc" if name != "abc" else "xyz"
    assert not is_mention(f"@[{other}] {other}", name, whole_word=True)


def test_dot_is_not_a_wildcard():
    assert not is_mention("@[abc]", "a.c")
    assert not is_mention("abc", "a.c", whole_word=True)
    assert not is_mention("aaa", ".*", whole_word=True)


def test_catastrophic_pattern_names_stay_fast():
    text = "a" * 5000 + "!"
    assert not is_mention(text, "(a+)+$", whole_word=True)
    assert not is_mention(text, "(a+)+$")


@pytest.mark.parametrize(
    ("name", "text"),
    [
        ("Léa", "salut @[LÉA] !"),
        ("Zoë", "@[zoë]"),
        ("Ünal", "ünal, ça va ?"),
        ("🙂Bob", "yo 🙂Bob!"),
        ("Bob🙂", "yo bob🙂 yo"),
        ("日本語", "こんにちは 日本語 さん"),
        ("Łukasz", "łukasz?"),
        ("Ana María", "hola ana maría."),
    ],
)
def test_unicode_names_match_whole_word(name, text):
    assert is_mention(text, name, whole_word=True)


def test_unicode_word_boundaries():
    assert not is_mention("Léandre", "Léa", whole_word=True)
    assert not is_mention("éLéa", "Léa", whole_word=True)
    assert not is_mention("日本語です", "日本語", whole_word=True)  # CJK letters are word chars
    assert not is_mention("a🙂Bob", "🙂Bob", whole_word=True)  # glued to a letter
    assert is_mention("a 🙂Bob", "🙂Bob", whole_word=True)


def test_the_pattern_is_compiled_once_per_name_and_mode():
    _matcher.cache_clear()
    for _ in range(50):
        is_mention("@[Bob] hi", "Bob")
        is_mention("Bob hi", "Bob", whole_word=True)
    info = _matcher.cache_info()
    assert info.misses == 2 and info.hits == 98
    assert isinstance(_matcher("Bob", False), re.Pattern)


# --- the rules ------------------------------------------------------------------

KEY = "channel-00112233445566778899aabbccddeeff"


def _rules(channels=("tg",), conv=None, **kw):
    return {
        "events": {
            "message.channel": {"channels": ["builtin:push"]},
            "message.mention": {"channels": list(channels), **kw},
        },
        "conversations": conv or {},
    }


def test_a_mention_reaches_the_mention_rule_channels_and_stored_exceptions():
    ids = rules_mod.message_channel_ids(
        _rules(("a", "b"), conv={KEY: {"c": True, "d": False}}),
        state_key=KEY,
        is_dm=False,
        is_public_or_hashtag=True,
        mention=True,
    )
    assert ids == ["a", "b", "c"]  # "c" has an exception but is not on the Mentions row


def test_private_key_mention_skips_cloud_channels_without_exception():
    kw = {"state_key": KEY, "is_dm": False, "is_public_or_hashtag": False, "mention": True}
    rules = _rules(("local", "tg"))
    assert rules_mod.message_channel_ids(rules, cloud_ids={"tg"}, **kw) == ["local"]
    excepted = _rules(("local", "tg"), conv={KEY: {"tg": True}})
    assert rules_mod.message_channel_ids(excepted, cloud_ids={"tg"}, **kw) == ["local", "tg"]
    off = _rules(("local", "tg"), conv={KEY: {"local": False}})
    assert rules_mod.message_channel_ids(off, cloud_ids={"tg"}, **kw) == []


def test_mention_event_ids_follow_the_mention_rule():
    event = ev.event_from_payload(
        {"type": "CHAN", "conversation_key": PUBLIC_KEY, "text": "x", "mention": True}
    )
    assert event is not None and event.event == "message.mention" and event.urgent is False
    assert rules_mod.event_channel_ids(_rules(), event, is_public_or_hashtag=True) == ["tg"]
    assert rules_mod.event_channel_ids(_rules(), event, is_public_or_hashtag=False) == ["tg"]
    assert (
        rules_mod.event_channel_ids(_rules(), event, is_public_or_hashtag=False, cloud_ids={"tg"})
        == []
    )


def test_channel_is_cloud_follows_the_registry():
    def channel(type_, config=None):
        return SimpleNamespace(type=type_, config=config or {})

    assert not rules_mod.channel_is_cloud(channel("web_push"))
    # A webhook URL is a secret that may point anywhere: a third party until excepted.
    assert rules_mod.channel_is_cloud(channel("webhook", {"url_set": True}))
    assert rules_mod.channel_is_cloud(channel("discord"))
    assert rules_mod.channel_is_cloud(channel("apprise"))  # URLs can point at any service
    assert rules_mod.channel_is_cloud(channel("ntfy", {"server": "https://ntfy.sh"}))
    assert not rules_mod.channel_is_cloud(channel("ntfy", {"server": "http://192.168.1.5"}))
    assert rules_mod.channel_is_cloud(channel("email", {"host": "smtp.gmail.com"}))
    assert not rules_mod.channel_is_cloud(channel("email", {"host": "192.168.1.2"}))
    assert rules_mod.channel_is_cloud(channel("somethingnew"))


@pytest.mark.asyncio
async def test_cloud_channel_ids_fails_closed(test_db, monkeypatch):
    assert await rules_mod.cloud_channel_ids(["builtin:push", "ghost"]) == {"ghost"}

    async def broken():
        raise RuntimeError

    monkeypatch.setattr(NotificationChannelRepository, "list_all", broken)
    assert await rules_mod.cloud_channel_ids(["builtin:push"]) == {"builtin:push"}


# --- rendering ------------------------------------------------------------------


def _push(**data) -> dict:
    base = {"type": "CHAN", "conversation_key": PUBLIC_KEY, "channel_name": "#paris"}
    return json.loads(render.build_push_payload(base | data, "en"))


def test_mention_title_and_tag():
    plain = _push(text="hi", sender_name="Léa")
    mention = _push(text="hi @[Bob]", sender_name="Léa", mention=True)
    assert mention["title"] == "Mention in #paris by Léa"
    assert mention["body"] == "hi @[Bob]"
    assert mention["tag"] == plain["tag"]  # replaces the plain message notification
    fr = json.loads(
        render.build_push_payload(
            {
                "type": "CHAN",
                "conversation_key": PUBLIC_KEY,
                "channel_name": "#paris",
                "text": "x",
                "sender_name": "Léa",
                "mention": True,
            },
            "fr",
        )
    )
    assert fr["title"] == "Mention dans #paris par Léa"


def test_mention_title_without_names():
    data = {"type": "CHAN", "conversation_key": PUBLIC_KEY, "text": "x", "mention": True}
    assert json.loads(render.build_push_payload(data, "en"))["title"] == "Mention in this channel"
    assert json.loads(render.build_push_payload(data, "fr"))["title"] == "Mention dans ce canal"


def test_mention_text_policy_still_applies():
    data = {
        "type": "CHAN",
        "conversation_key": PRIVATE_KEY,
        "channel_name": "Crew",
        "sender_name": "Léa",
        "text": "secret @[Bob]",
        "mention": True,
    }
    _, body = render.event_notification_text(data, "en", include_message_text="public_only")
    assert "secret" not in body
    _, body = render.event_notification_text(data, "en", include_message_text="always")
    assert "secret" in body


# --- the producer ---------------------------------------------------------------


@pytest.fixture
def emitted(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def fake_emit(event, *, payload=None, targets=None, exclude=(), rules=None, **kw):
        """What the dispatcher would queue: the instance channels the rules give, and Web Push
        decided per account (``user_rules``): a mention goes out as ``message.mention``."""
        audience = None
        if targets is not None:
            ids = list(targets)
        else:
            ids = [
                c
                for c in rules_mod.event_channel_ids(
                    rules,
                    event,
                    is_public_or_hashtag=bool(kw.get("is_open")),
                    muted=kw.get("muted", False),
                    blocked=kw.get("blocked", False),
                )
                if c not in exclude
            ]
            ids, audience = await notification_dispatcher()._push_per_account(
                ids,
                rules,
                event,
                payload,
                kw.get("push_mention"),
                is_open=bool(kw.get("is_open")),
                muted=kw.get("muted", False),
                blocked=kw.get("blocked", False),
            )
        # With a mention version for the accounts, the dispatcher carries an audience whose
        # default payload is the mention one.
        pushed_mention = bool(
            audience is not None and audience.default and audience.default.get("mention")
        )
        if pushed_mention:
            ids = [c for c in ids if c != "builtin:push"]
            previous = next((e for e in seen if e["event"] == "message.mention"), None)
            if previous is not None:
                previous["channels"].insert(0, "builtin:push")
            else:
                seen.append(
                    {"event": "message.mention", "urgent": False, "channels": ["builtin:push"]}
                )
            if not ids:
                return 1
        seen.append({"event": event.event, "urgent": event.urgent, "channels": ids})
        return len(ids)

    monkeypatch.setattr("app.notifications.producers.emit", fake_emit)
    return seen


@pytest.fixture
def radio(monkeypatch):
    monkeypatch.setattr("app.notifications.producers._radio_name", lambda: "Bob")


async def _set_rules(*, mention=("builtin:push",), message_channel=("builtin:push",), **top):
    rules = await NotificationChannelRepository.get_rules()
    rules["events"]["message.mention"] = {"channels": list(mention), "always_deliver": False}
    rules["events"]["message.channel"]["channels"] = list(message_channel)
    rules.update(top)
    await NotificationChannelRepository.set_rules(rules)
    return rules


def _chan(key=PUBLIC_KEY, text="hi @[Bob]", **extra) -> dict:
    return {"type": "CHAN", "conversation_key": key, "text": text, "sender_name": "Léa", **extra}


async def _send(message: dict) -> None:
    from app.notifications.producers import emit_message

    await emit_message(message)


@pytest.mark.asyncio
async def test_a_mention_replaces_the_plain_message_on_the_same_channel(test_db, radio, emitted):
    await _set_rules()
    await _send(_chan())
    assert emitted == [{"event": "message.mention", "urgent": False, "channels": ["builtin:push"]}]


@pytest.mark.asyncio
async def test_a_mention_is_delivered_once_per_channel_across_both_events(test_db, radio, emitted):
    await _set_rules(mention=("a",), message_channel=("a", "b"))
    await _send(_chan())
    assert emitted == [
        {"event": "message.mention", "urgent": False, "channels": ["a"]},
        {"event": "message.channel", "urgent": False, "channels": ["b"]},
    ]


@pytest.mark.asyncio
async def test_no_mention_rule_keeps_the_plain_notification(test_db, radio, emitted):
    await _set_rules(mention=())
    await _send(_chan())
    assert emitted == [{"event": "message.channel", "urgent": False, "channels": ["builtin:push"]}]


@pytest.mark.asyncio
async def test_a_message_without_the_token_is_a_plain_message(test_db, radio, emitted):
    await _set_rules()
    await _send(_chan(text="Bob, are you there?"))
    assert [e["event"] for e in emitted] == ["message.channel"]


@pytest.mark.asyncio
async def test_whole_word_option_in_the_rules(test_db, radio, emitted):
    await _set_rules(mention_whole_word=True)
    await _send(_chan(text="Bob, are you there?"))
    await _send(_chan(text="Bobby, are you there?"))
    assert [e["event"] for e in emitted] == ["message.mention", "message.channel"]


@pytest.mark.asyncio
async def test_a_forged_mention_flag_in_the_input_is_ignored(test_db, radio, emitted):
    await _set_rules()
    await _send(_chan(text="nothing", mention=True))
    assert [e["event"] for e in emitted] == ["message.channel"]


@pytest.mark.asyncio
async def test_no_radio_name_means_no_mention(test_db, monkeypatch, emitted):
    monkeypatch.setattr("app.notifications.producers._radio_name", lambda: None)
    await _set_rules()
    await _send(_chan(text="@[] @[None]"))
    assert [e["event"] for e in emitted] == ["message.channel"]


@pytest.mark.asyncio
async def test_a_direct_message_is_never_a_mention(test_db, radio, emitted):
    await _set_rules()
    await _send({"type": "PRIV", "conversation_key": "ab" * 32, "text": "@[Bob]"})
    assert [e["event"] for e in emitted] == ["message.dm"]


@pytest.mark.asyncio
async def test_outgoing_never_mentions(test_db, radio, emitted):
    await _set_rules()
    await _send(_chan(outgoing=True))
    assert emitted == []


@pytest.mark.asyncio
async def test_muted_channel_and_blocked_sender_cut_a_mention(test_db, radio, emitted):
    await _set_rules()
    await ChannelRepository.upsert(HASHTAG_KEY, "#ops", is_hashtag=True)
    await ChannelRepository.set_muted(HASHTAG_KEY, True)
    await _send(_chan(key=HASHTAG_KEY))
    assert emitted == []

    await ChannelRepository.set_muted(HASHTAG_KEY, False)
    from app.repository.settings import AppSettingsRepository

    await AppSettingsRepository.update(blocked_names=["Léa"])
    await _send(_chan(key=HASHTAG_KEY))
    assert emitted == []


@pytest.mark.asyncio
async def test_private_key_mention_towards_a_cloud_channel_needs_an_exception(
    test_db, radio, emitted, monkeypatch
):
    async def cloud(ids):
        return {c for c in ids if c == "tg"}

    monkeypatch.setattr(rules_mod, "cloud_channel_ids", cloud)
    await ChannelRepository.upsert(PRIVATE_KEY, "Crew")
    await _set_rules(mention=("builtin:push", "tg"), message_channel=())
    await _send(_chan(key=PRIVATE_KEY))
    assert emitted == [{"event": "message.mention", "urgent": False, "channels": ["builtin:push"]}]

    emitted.clear()
    await _set_rules(
        mention=("builtin:push", "tg"),
        message_channel=(),
        conversations={f"channel-{PRIVATE_KEY}": {"tg": True}},
    )
    await _send(_chan(key=PRIVATE_KEY))
    assert emitted[0]["channels"] == ["builtin:push", "tg"]


@pytest.mark.asyncio
async def test_private_key_mention_reaches_non_cloud_even_when_the_channel_rule_says_no(
    test_db, radio, emitted
):
    await ChannelRepository.upsert(PRIVATE_KEY, "Crew")
    await _set_rules(message_channel=())
    await _send(_chan(key=PRIVATE_KEY))
    assert [e["event"] for e in emitted] == ["message.mention"]


@pytest.mark.asyncio
async def test_the_mention_path_does_no_database_read_for_a_plain_message(
    test_db, radio, emitted, monkeypatch
):
    """Hot path: the matcher uses the radio name in memory; channels are read for a mention
    of a private-key channel only."""
    reads: list[int] = []
    real = NotificationChannelRepository.list_all

    async def counting():
        reads.append(1)
        return await real()

    monkeypatch.setattr(NotificationChannelRepository, "list_all", counting)
    await _set_rules()
    await _send(_chan(text="no mention here"))
    await _send(_chan(text="@[Bob]"))  # Public channel: nothing to look up either
    assert reads == []
    await ChannelRepository.upsert(PRIVATE_KEY, "Crew")
    await _send(_chan(key=PRIVATE_KEY))
    assert reads == [1]


@pytest.mark.asyncio
async def test_a_mention_is_never_urgent(test_db, radio, emitted):
    await _set_rules()
    await _send(_chan())
    assert all(e["urgent"] is False for e in emitted)


@pytest.mark.asyncio
async def test_a_public_webhook_is_cloud_and_needs_an_exception_on_a_private_channel(
    test_db, radio, emitted, monkeypatch
):
    hook = SimpleNamespace(id="hook-1", type="webhook", config={})

    async def listing():
        return [hook]

    monkeypatch.setattr(NotificationChannelRepository, "list_all", listing)
    await ChannelRepository.upsert(PRIVATE_KEY, "Crew")
    await _set_rules(mention=("hook-1",), message_channel=())
    await _send(_chan(key=PRIVATE_KEY))
    assert emitted == []

    await _set_rules(
        mention=(), message_channel=(), conversations={f"channel-{PRIVATE_KEY}": {"hook-1": True}}
    )
    await _send(_chan(key=PRIVATE_KEY))
    assert emitted == [{"event": "message.mention", "urgent": False, "channels": ["hook-1"]}]
