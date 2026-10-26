"""``message.keyword``: the matcher, the validation, the producer and the API (MSHL-128)."""

from __future__ import annotations

import json
import time
import unicodedata

import pytest

from app.notifications import events as ev
from app.notifications import keywords as kw
from app.notifications import render
from app.notifications.dispatcher import notification_dispatcher
from app.repository.channels import ChannelRepository
from app.repository.notification_channels import NotificationChannelRepository

PUBLIC_KEY = "8B3387E9C5CDEA6AC9E5EDBAA115CD72"
HASHTAG_KEY = "A1B2C3D4E5F60718293A4B5C6D7E8F90"
PRIVATE_KEY = "00112233445566778899AABBCCDDEEFF"


def _match(text: str, pattern: str, mode: str = "contains") -> bool:
    return kw.text_matches(kw.normalise(text), kw.normalise(pattern), mode)


# --- the matcher ----------------------------------------------------------------


def test_contains_versus_word():
    assert _match("au secours!", "secours")
    assert _match("secourssss", "secours")
    assert _match("à bientôt", "tôt")
    assert _match("au secours!", "secours", "word")
    assert _match("secours", "secours", "word")
    assert not _match("secourssss", "secours", "word")
    assert not _match("le_secours", "secours", "word")
    assert not _match("insecours", "secours", "word")
    # A later occurrence can still be a whole word.
    assert _match("secoursx secours", "secours", "word")


def test_word_mode_on_a_pattern_with_symbols():
    # A symbol or emoji edge has no word boundary: only a word-character edge is checked.
    assert _match("help🆘now", "🆘", "word")
    assert _match("help🆘now", "help", "word")
    assert not _match("helpful🆘", "help", "word")
    assert _match("go to #paris now", "#paris", "word")
    assert _match("go to x#paris now", "#paris", "word")
    assert not _match("go to #parisx", "#paris", "word")
    assert _match("sos! please", "sos!", "word")
    assert _match("sos!please", "sos!", "word")
    assert not _match("xsos!", "sos!", "word")


def test_case_and_unicode_normalisation():
    assert _match("AU SECOURS", "secours")
    assert _match("Straße", "STRASSE")  # casefold, not lower
    assert _match("Éléphant", "éLÉPHANT")
    decomposed = unicodedata.normalize("NFD", "café")
    assert decomposed != "café"
    assert _match(f"un {decomposed} noir", "café")
    assert _match("un café noir", decomposed)
    # Accents stay significant.
    assert not _match("cafe", "café")


def test_a_pathological_text_stays_linear():
    text = "a" * 200_000 + "!"
    started = time.perf_counter()
    assert not _match(text, "a" * 63 + "b", "word")
    assert _match(text, "a" * 63)
    assert time.perf_counter() - started < 2


def test_the_sender_name_prefix_is_not_matched():
    payload = {"type": "CHAN", "sender_name": "Secours Alpes", "text": "Secours Alpes: bonjour"}
    rules = {"keywords": [{"id": "a", "pattern": "alpes", "scope": "all", "match": "contains"}]}
    assert kw.matching_rules(rules, payload) == []
    assert kw.matching_rules(rules, payload | {"text": "Secours Alpes: alpes!"})


def test_scope():
    chan = {"type": "CHAN", "conversation_key": PUBLIC_KEY, "text": "sos"}
    dm = {"type": "PRIV", "conversation_key": "ab" * 32, "text": "sos"}

    def hits(scope, payload):
        rule = {"id": "a", "pattern": "sos", "match": "contains", "scope": scope}
        return bool(kw.matching_rules({"keywords": [rule]}, payload))

    assert hits("all", chan) and hits("all", dm)
    assert hits("dms", dm) and not hits("dms", chan)
    assert hits([f"channel-{PUBLIC_KEY.lower()}"], chan)
    assert not hits([f"channel-{PUBLIC_KEY}"], dm)
    assert not hits([f"channel-{HASHTAG_KEY}"], chan)
    assert hits([f"contact-{'ab' * 32}"], dm)
    assert not hits("bogus", chan)


# --- validation -----------------------------------------------------------------


def _rule(**over):
    return {"pattern": "secours", "match": "contains", "scope": "all", "channels": ["a"]} | over


def test_limits():
    assert kw.clean_rule(_rule(pattern="x" * 64))["pattern"] == "x" * 64
    with pytest.raises(kw.KeywordError, match="pattern"):
        kw.clean_rule(_rule(pattern="x" * 65))
    for bad in ("", "   ", "a\x00b", "a\nb", 5):
        with pytest.raises(kw.KeywordError, match="pattern"):
            kw.clean_rule(_rule(pattern=bad))
    assert (
        len(kw.clean_rules([_rule() for _ in range(20)] and [_rule(id=f"r{i}") for i in range(20)]))
        == 20
    )
    with pytest.raises(kw.KeywordError, match="keywords"):
        kw.clean_rules([_rule(id=f"r{i}") for i in range(21)])
    with pytest.raises(kw.KeywordError, match="id"):
        kw.clean_rules([_rule(id="x"), _rule(id="x")])


def test_the_pattern_is_stored_nfc_and_trimmed():
    stored = kw.clean_rule(_rule(pattern="  " + unicodedata.normalize("NFD", "café") + " "))
    assert stored["pattern"] == "café"


@pytest.mark.parametrize(
    ("over", "field"),
    [
        ({"match": "regex"}, "match"),
        ({"scope": []}, "scope"),
        ({"scope": ["nope"]}, "scope"),
        ({"scope": "channels"}, "scope"),
        ({"channels": "a"}, "channels"),
        ({"channels": ["builtin:push"]}, "channels"),
        ({"cooldown_s": -1}, "cooldown_s"),
        ({"cooldown_s": 86_401}, "cooldown_s"),
        ({"cooldown_s": True}, "cooldown_s"),
        ({"id": "bad id!"}, "id"),
    ],
)
def test_invalid_rules(over, field):
    with pytest.raises(kw.KeywordError, match=field):
        kw.clean_rule(_rule(**over))


def test_defaults_and_unknown_channels():
    stored = kw.clean_rule({"pattern": "sos"})
    assert stored["match"] == "contains" and stored["scope"] == "all"
    assert stored["cooldown_s"] == kw.DEFAULT_COOLDOWN_S and stored["id"].startswith("kw_")
    with pytest.raises(kw.KeywordError, match="channels"):
        kw.clean_rule(_rule(), known_channels={"b"})


# --- the event ------------------------------------------------------------------


def _payload(**extra):
    return {
        "type": "CHAN",
        "conversation_key": PUBLIC_KEY,
        "channel_name": "#paris",
        "sender_name": "Léa",
        "text": "Léa: secours!",
        "id": 7,
        "sender_timestamp": 1,
        "keyword": "secours",
        "keyword_id": "r1",
    } | extra


def test_the_event_is_never_urgent_and_deduped_per_rule_and_message():
    event = ev.event_from_payload(_payload())
    assert isinstance(event, ev.KeywordEvent)
    assert event.event == "message.keyword" and event.urgent is False
    other_rule = ev.event_from_payload(_payload(keyword_id="r2"))
    other_message = ev.event_from_payload(_payload(id=8))
    assert event.dedup_key == ev.event_from_payload(_payload()).dedup_key
    assert event.dedup_key != other_rule.dedup_key
    assert event.dedup_key != other_message.dedup_key
    assert event.to_payload()["keyword"] == "secours"


def test_the_title():
    en = json.loads(render.build_push_payload(_payload(), "en"))
    fr = json.loads(render.build_push_payload(_payload(), "fr"))
    assert en["title"] == "Keyword \u201csecours\u201d in #paris"
    assert fr["title"] == "Mot-clé « secours » dans #paris"
    plain = json.loads(render.build_push_payload(_payload(keyword="", keyword_id=""), "en"))
    assert en["tag"] != plain["tag"]  # a keyword notification does not replace the plain one


# --- the producer ---------------------------------------------------------------


@pytest.fixture
def emitted(monkeypatch) -> list[dict]:
    seen: list[dict] = []

    async def fake_emit(event, *, payload=None, targets=None, exclude=(), rules=None, **kw_):
        if targets is not None:
            ids = list(targets)
        else:
            from app.notifications import rules as rules_mod

            ids = [
                c
                for c in rules_mod.event_channel_ids(
                    rules,
                    event,
                    is_public_or_hashtag=bool(kw_.get("is_open")),
                    muted=kw_.get("muted", False),
                    blocked=kw_.get("blocked", False),
                )
                if c not in exclude and c != "builtin:push"
            ]
        seen.append({"event": event.event, "urgent": event.urgent, "channels": ids})
        return len(ids)

    monkeypatch.setattr("app.notifications.producers.emit", fake_emit)
    monkeypatch.setattr("app.notifications.producers._radio_name", lambda: "Bob")
    return seen


async def _set_rules(keywords, *, message_channel=(), mention=(), **top):
    rules = await NotificationChannelRepository.get_rules()
    rules["events"]["message.channel"]["channels"] = list(message_channel)
    rules["events"]["message.mention"] = {"channels": list(mention), "always_deliver": False}
    rules["keywords"] = keywords
    rules.update(top)
    await NotificationChannelRepository.set_rules(rules)


def _kw(rid="r1", pattern="secours", **over):
    return {
        "id": rid,
        "pattern": pattern,
        "match": "contains",
        "scope": "all",
        "channels": ["k"],
        "cooldown_s": 0,
    } | over


def _chan(key=PUBLIC_KEY, text="Léa: au secours", **extra) -> dict:
    return {"type": "CHAN", "conversation_key": key, "text": text, "sender_name": "Léa"} | extra


async def _send(message: dict) -> None:
    from app.notifications.producers import emit_message

    await emit_message(message)


def _keyword_events(emitted):
    return [e for e in emitted if e["event"] == "message.keyword"]


@pytest.mark.asyncio
async def test_a_match_fires_in_addition_to_the_plain_message(test_db, emitted):
    await _set_rules([_kw()], message_channel=("m",))
    await _send(_chan())
    assert emitted == [
        {"event": "message.channel", "urgent": False, "channels": ["m"]},
        {"event": "message.keyword", "urgent": False, "channels": ["k"]},
    ] or emitted == [
        {"event": "message.keyword", "urgent": False, "channels": ["k"]},
        {"event": "message.channel", "urgent": False, "channels": ["m"]},
    ]


@pytest.mark.asyncio
async def test_no_duplicate_on_a_channel_that_already_gets_the_message(test_db, emitted):
    await _set_rules([_kw(channels=["m", "k"])], message_channel=("m",), mention=("n",))
    await _send(_chan(text="Léa: secours @[Bob]"))
    assert _keyword_events(emitted) == [
        {"event": "message.keyword", "urgent": False, "channels": ["k"]}
    ]
    emitted.clear()
    await _set_rules([_kw(channels=["m", "n"])], message_channel=("m",), mention=("n",))
    await _send(_chan(text="Léa: secours @[Bob]", id=2))
    assert _keyword_events(emitted) == []  # both channels already receive it


@pytest.mark.asyncio
async def test_no_match_no_event(test_db, emitted):
    await _set_rules([_kw()], message_channel=("m",))
    await _send(_chan(text="Léa: bonjour"))
    assert _keyword_events(emitted) == []


@pytest.mark.asyncio
async def test_a_forged_keyword_in_the_input_is_ignored(test_db, emitted):
    await _set_rules([], message_channel=("m",))
    await _send(_chan(text="Léa: bonjour", keyword="x", keyword_id="y"))
    assert [e["event"] for e in emitted] == ["message.channel"]


@pytest.mark.asyncio
async def test_never_urgent(test_db, emitted):
    await _set_rules([_kw()])
    await _send(_chan(urgent=True))
    assert [e["urgent"] for e in _keyword_events(emitted)] == [False]


@pytest.mark.asyncio
async def test_scope_filters_in_the_producer(test_db, emitted):
    await _set_rules([_kw(scope="dms")])
    await _send(_chan())
    assert _keyword_events(emitted) == []
    await _send({"type": "PRIV", "conversation_key": "ab" * 32, "text": "secours", "id": 3})
    assert len(_keyword_events(emitted)) == 1


@pytest.mark.asyncio
async def test_muted_blocked_and_pending_win(test_db, emitted):
    await _set_rules([_kw()])
    await ChannelRepository.upsert(HASHTAG_KEY, "#ops", is_hashtag=True)
    await ChannelRepository.set_muted(HASHTAG_KEY, True)
    await _send(_chan(key=HASHTAG_KEY))
    assert emitted == []
    await ChannelRepository.set_muted(HASHTAG_KEY, False)

    from app.repository.settings import AppSettingsRepository

    await AppSettingsRepository.update(blocked_names=["Léa"])
    from app.services import message_filters

    message_filters.invalidate_block_lists() if hasattr(
        message_filters, "invalidate_block_lists"
    ) else None
    await _send(_chan(key=HASHTAG_KEY))
    assert _keyword_events(emitted) == []


@pytest.mark.asyncio
async def test_cooldown_per_rule_and_per_sender(test_db, emitted):
    now = [1_000_000.0]
    notification_dispatcher()._dedup._wall = lambda: now[0]
    await _set_rules([_kw(cooldown_s=60)])
    await _send(_chan(id=1, sender_name="Léa"))
    assert len(_keyword_events(emitted)) == 1
    # Same rule, another sender, inside the rule's cooldown.
    await _send(_chan(id=2, sender_name="Max", text="Max: secours"))
    assert len(_keyword_events(emitted)) == 1
    # The rule's cooldown is over, the same sender's is not (15 minutes at least).
    now[0] += 61
    await _send(_chan(id=3, sender_name="Léa"))
    assert len(_keyword_events(emitted)) == 1
    # Another sender goes through.
    await _send(_chan(id=4, sender_name="Max", text="Max: secours"))
    assert len(_keyword_events(emitted)) == 2
    # Everything is over after the sender window.
    now[0] += kw.SENDER_COOLDOWN_S + 1
    await _send(_chan(id=5, sender_name="Léa"))
    assert len(_keyword_events(emitted)) == 3


@pytest.mark.asyncio
async def test_a_rule_that_reaches_no_channel_starts_no_cooldown(test_db, emitted):
    await _set_rules([_kw(cooldown_s=3600, channels=["m"])], message_channel=("m",))
    await _send(_chan(id=1))
    assert _keyword_events(emitted) == []
    await _set_rules([_kw(cooldown_s=3600, channels=["m"])], message_channel=())
    await _send(_chan(id=2, sender_name="Max", text="Max: secours"))
    assert len(_keyword_events(emitted)) == 1


@pytest.mark.asyncio
async def test_each_rule_fires_on_its_own(test_db, emitted):
    await _set_rules([_kw("r1", "secours"), _kw("r2", "au", match="word", channels=["j"])])
    await _send(_chan())
    assert sorted(e["channels"][0] for e in _keyword_events(emitted)) == ["j", "k"]


# --- the dispatcher: pauses and dedup -------------------------------------------


@pytest.mark.asyncio
async def test_the_global_pause_holds_a_keyword_event(test_db):
    from app.notifications import rules as rules_mod
    from app.notifications.dispatcher import Dispatcher

    rules = {"paused_until": time.time() + 3600, "events": {}}
    event = ev.event_from_payload(_payload())
    assert event is not None
    targets = rules_mod.apply_pauses(
        rules,
        ["k"],
        name=event.event,
        urgent=event.urgent,
        channel_paused_until={},
        now=time.time(),
    )
    assert [t.paused for t in targets] == [True]
    assert Dispatcher  # the real queueing is covered by test_notification_dispatcher


# --- the API --------------------------------------------------------------------


async def test_api_roundtrip_and_limits(test_db, client):
    async def patch(keywords):
        return await client.patch("/api/notifications/rules", json={"keywords": keywords})

    ok = await patch([{"pattern": " secours ", "channels": []}])
    assert ok.status_code == 200
    stored = ok.json()["keywords"][0]
    assert stored["pattern"] == "secours" and stored["id"].startswith("kw_")
    assert stored["scope"] == "all" and stored["match"] == "contains"
    for bad in (
        [{"pattern": "x" * 65}],
        [{"pattern": f"p{i}", "id": f"r{i}"} for i in range(21)],
        [{"pattern": "a", "channels": ["nope"]}],
        [{"pattern": "a", "channels": ["builtin:push"]}],
        [{"pattern": "a", "match": "regex"}],
    ):
        response = await patch(bad)
        assert response.status_code == 400, bad
        assert response.json()["detail"]["code"] == "invalid_config"
    # The refused lists left the stored one alone; 20 rules are accepted.
    assert [
        r["pattern"] for r in (await client.get("/api/notifications/rules")).json()["keywords"]
    ] == ["secours"]
    twenty = await patch([{"pattern": f"p{i}", "id": f"r{i}"} for i in range(20)])
    assert twenty.status_code == 200 and len(twenty.json()["keywords"]) == 20
    assert (await patch([])).json()["keywords"] == []


async def test_the_event_is_in_the_catalogue_but_not_configurable(test_db, client):
    events = {e["id"]: e for e in (await client.get("/api/notifications/events")).json()["events"]}
    assert events["message.keyword"]["configurable"] is False
    assert events["message.keyword"]["urgent"] is False
    refused = await client.patch(
        "/api/notifications/rules", json={"events": {"message.keyword": {"channels": []}}}
    )
    assert refused.status_code == 400
