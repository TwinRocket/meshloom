"""The bundled official plugins (`meshloom_plugin/official/`): `meshloom.echo` and
`meshloom.auto-responder`, as packages (`dev check`), as rules (the parser and matcher),
through the host (install offline from the real tree, a captured test message) and
against the host guards in the two-host simulation."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.plugins import installer
from app.plugins.host import Budgets, PluginManager
from app.plugins.quotas import GuardRefused, SendGuard
from meshloom_plugin import dev
from meshloom_plugin.errors import HostError
from meshloom_plugin.official.auto_responder import plugin as ar
from meshloom_plugin.official.auto_responder.plugin import (
    AutoResponder,
    RuleError,
    compile_pattern,
    parse_rule,
    parse_rules,
)

ROOT = Path(__file__).resolve().parent.parent
OFFICIAL = ROOT / "meshloom_plugin" / "official"
HOUR = 3600
LANGS = ("en", "fr")


def msg(text, *, sender="Ann", channel="#test", dm=False, key=None, outgoing=False):
    from meshloom_plugin import Message

    return Message(
        text=text,
        sender_name=sender,
        sender_key=key or ("ab" * 32 if dm else None),
        is_dm=dm,
        channel_key=None if dm else "CD" * 16,
        channel_name=None if dm else channel,
        is_outgoing=outgoing,
        raw={"event_id": 1},
    )


# --- packages ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["echo", "auto_responder"])
def test_dev_check_passes_as_a_bundled_package(name):
    report = dev.check_directory(OFFICIAL / name, origin="bundled")
    assert report.ok, report.errors
    assert report.ran
    assert not [w for w in report.warnings if not w.informative]


def test_echo_stays_silent_on_other_text():
    report = dev.check_directory(OFFICIAL / "echo", origin="bundled")
    assert report.sends == []  # "!test hello" is not "!pling"


@pytest.mark.parametrize("name", ["echo", "auto_responder"])
def test_every_user_facing_string_exists_in_english_and_french(name):
    directory = OFFICIAL / name
    manifest = json.loads((directory / "manifest.json").read_text("utf-8"))
    for field in ("name", "summary"):
        assert set(manifest[field]) >= set(LANGS), field
    for lang in LANGS:
        assert (directory / manifest["readme"][lang]).read_text("utf-8").strip()
    if "config_schema" in manifest:
        schema = json.loads((directory / manifest["config_schema"]).read_text("utf-8"))
        for node in [schema, *schema["properties"].values()]:
            for key in ("title", "description"):
                if key in node:
                    assert set(node[key]) >= set(LANGS), (key, node)


def test_plugin_directories_hold_the_files_the_loader_expects():
    plugins = installer.list_bundled()
    assert set(plugins) == {"meshloom.echo", "meshloom.auto-responder"}
    assert (OFFICIAL / "echo" / "LICENSE").is_file()


# --- rules -----------------------------------------------------------------------


def rule(line, **kwargs):
    return parse_rule(line, default_cooldown=120, **kwargs)


def test_keyword_matches_whole_words_ignoring_case():
    r = rule("keyword: hello => hi")
    assert r.matches(msg("well HELLO there"))
    assert r.matches(msg("hello!"))
    assert not r.matches(msg("othello"))
    assert not rule("keyword: Hello => hi", case_sensitive=True).matches(msg("hello"))


def test_prefix_and_regex():
    assert rule("prefix: !ping => pong").matches(msg("!PING now"))
    assert not rule("prefix: !ping => pong").matches(msg("say !ping"))
    r = rule(r"regex: ^(hi|hey)\b => yo")
    assert r.matches(msg("Hey you"))
    assert not r.matches(msg("they"))


def test_regex_only_reads_the_head_of_the_message():
    r = rule("regex: needle => x")
    assert r.matches(msg("a" * 150 + "needle"))
    assert not r.matches(msg("a" * 250 + "needle"))


def test_channel_option_and_dm():
    in_test = rule("keyword,channel=#test: hi => x")
    assert in_test.matches(msg("hi", channel="#Test"))
    assert not in_test.matches(msg("hi", channel="#other"))
    assert not in_test.matches(msg("hi", dm=True))
    only_dm = rule("keyword,channel=dm: hi => x")
    assert only_dm.matches(msg("hi", dm=True))
    assert not only_dm.matches(msg("hi"))


def test_options_and_default_cooldown():
    assert rule("keyword: a => b").cooldown == 120
    assert rule("keyword, cooldown=300 , channel=#x: a => b").cooldown == 300


def test_template_fills_sender_channel_and_text_only():
    r = rule("keyword: hi => {sender}|{channel}|{text}|{other}|{{sender}}")
    assert r.render(msg("hi there", sender="Ann")) == "Ann|#test|hi there|{other}|{Ann}"


@pytest.mark.parametrize(
    "line",
    [
        "hello => hi",
        "word: hello => hi",
        "keyword: hello",
        "keyword: hello =>",
        "keyword,color=red: hello => hi",
        "keyword,cooldown=5: hello => hi",
        "keyword,cooldown=abc: hello => hi",
        "keyword,channel=: hello => hi",
        "regex: ( => hi",
        "regex: (a+)+$ => hi",
        "regex: (a|b*)* => hi",
        r"regex: (a)\1 => hi",
        "regex: " + "a" * 101 + " => hi",
    ],
)
def test_unusable_rules_are_refused(line):
    with pytest.raises(RuleError):
        rule(line)


CATASTROPHIC = [
    "(a+)+",
    "(a*)*",
    "(x+x+)+y",
    "(.*a){5,}",
    "(a|aa)+$",
    "(a|a?)+$",
    "(a|a)+$",
    "(?:a|aa)*b",
    "a*a*a*a*a*a*a*b",
    ".*a.*a.*a.*b",
    "a*a*a*b",
    "(a?){30}",
    "(a{1,9})*",
    "(?:a*)(?:a*)a*b",
    r"(a)\1",
    "(?=a)a",
    "(?!a)a",
]
FINE = [
    "^!ping$",
    r"\bhi\b",
    "(hi|hey) there",
    "[a-z]+ (now|later)",
    ".*a.*b",
    "(ab){2,4}c",
    r"\d+-\d+",
    r"^(hi|hey)\b",
    ".*x.*x",
    "a*a*b",
    "[ab]+[ab]+c",
]
HOSTILE_TEXTS = ["a" * 200, "ab" * 100, "x" * 200, "a-" * 100, "a" * 199 + "!", "ab" * 99 + "!"]


@pytest.mark.parametrize("pattern", CATASTROPHIC)
def test_catastrophic_patterns_are_refused_at_config_time(pattern):
    with pytest.raises(RuleError):
        compile_pattern(pattern, case_sensitive=False)
    with pytest.raises(RuleError):
        rule(f"regex: {pattern} => x")


@pytest.mark.parametrize("pattern", FINE)
def test_accepted_patterns_are_cheap_on_the_longest_input(pattern):
    import time

    # CPU time (not wall clock) so xdist/CI contention does not count, and the best of a
    # few runs so a stray preemption or cache miss cannot fail it. A pattern that slips
    # past the structural guard backtracks exponentially and misses the budget by orders
    # of magnitude, while an accepted one needs a few milliseconds at most.
    compiled = compile_pattern(pattern, case_sensitive=False)
    for text in HOSTILE_TEXTS:
        best = float("inf")
        for _ in range(3):
            started = time.process_time()
            compiled.search(text[: ar.MATCH_TEXT_LIMIT])
            best = min(best, time.process_time() - started)
        assert best < 0.1, (pattern, text[:4])


def test_a_slow_compile_is_refused(monkeypatch):
    ticks = iter([0.0, 5.0])
    monkeypatch.setattr(ar.time, "perf_counter", lambda: next(ticks))
    with pytest.raises(RuleError, match="too long"):
        compile_pattern("hi", case_sensitive=False)


def test_bad_rules_are_skipped_with_their_line_number():
    rules, problems = parse_rules(
        ["keyword: a => b", "", "oops", 5, "prefix: c => d"],
        default_cooldown=60,
        case_sensitive=False,
    )
    assert [r.trigger for r in rules] == ["keyword", "prefix"]
    assert len(problems) == 1 and problems[0].startswith("rule 3 ignored")
    assert parse_rules("not a list", default_cooldown=60, case_sensitive=False) == ([], [])


# --- the plugin object -------------------------------------------------------------


class FakeHost:
    def __init__(self):
        self.calls = []
        self.fail = None

    async def call(self, method, params):
        self.calls.append((method, params))
        if self.fail and method == "messages.send":
            raise HostError(self.fail)
        return None

    def notify(self, method, params):
        self.calls.append((method, params))

    @property
    def sent(self):
        return [p["text"] for m, p in self.calls if m == "messages.send"]


@pytest.fixture
def host(monkeypatch):
    fake = FakeHost()
    import meshloom_plugin

    monkeypatch.setattr(meshloom_plugin, "_host", fake)
    return fake


async def start(config, now=1_000_000.0):
    plugin = AutoResponder()
    clock = SimpleNamespace(now=now)
    plugin.clock = lambda: clock.now
    await plugin.on_init({"config": config, "lang": "en"})
    return plugin, clock


async def test_it_answers_then_respects_the_cooldown_per_rule_and_sender(host):
    plugin, clock = await start({"rules": ["keyword: hello => Hi {sender}!"]})
    await plugin.on_message(msg("hello", sender="Ann"))
    await plugin.on_message(msg("hello again", sender="Ann"))
    await plugin.on_message(msg("hello", sender="Bob"))
    assert host.sent == ["Hi Ann!", "Hi Bob!"]
    clock.now += 121
    await plugin.on_message(msg("hello", sender="Ann"))
    assert host.sent == ["Hi Ann!", "Hi Bob!", "Hi Ann!"]


async def test_a_rule_own_cooldown_and_the_first_match_deciding(host):
    plugin, clock = await start(
        {"rules": ["keyword,cooldown=600: a => one", "keyword: a => two"], "cooldown_seconds": 60}
    )
    await plugin.on_message(msg("a"))
    clock.now += 100
    await plugin.on_message(msg("a"))  # rule 1 is still cooling down: silence, rule 2 not tried
    assert host.sent == ["one"]


async def test_the_hourly_cap_holds_and_survives_a_restart(host):
    rules = [f"keyword: w{i} => r{i}" for i in range(5)]
    plugin, clock = await start({"rules": rules, "max_replies_per_hour": 2})
    for i in range(5):
        await plugin.on_message(msg(f"w{i}"))
    assert host.sent == ["r0", "r1"]
    stored = [p for m, p in host.calls if m == "storage.set"][-1]["value"]
    assert len(stored) == 2
    # A new process reads the counter back from storage.
    again = AutoResponder()
    again.clock = plugin.clock
    host.calls.clear()

    async def stored_get(method, params):
        host.calls.append((method, params))
        return stored if method == "storage.get" else None

    host.call = stored_get
    await again.on_init({"config": {"rules": rules, "max_replies_per_hour": 2}})
    await again.on_message(msg("w4", sender="Zed"))
    assert not [p for m, p in host.calls if m == "messages.send"]
    clock.now += HOUR + 1
    await again.on_message(msg("w4", sender="Zed"))
    assert [p["text"] for m, p in host.calls if m == "messages.send"] == ["r4"]


async def test_it_ignores_its_own_messages_and_dms_when_told_to(host):
    plugin, _ = await start({"rules": ["keyword: hi => x"], "answer_direct_messages": False})
    await plugin.on_message(msg("hi", outgoing=True))
    await plugin.on_message(msg("hi", dm=True))
    await plugin.on_message(msg("   "))
    assert host.sent == []
    await plugin.on_message(msg("hi"))
    assert host.sent == ["x"]


async def test_dm_reply_goes_to_the_sender(host):
    plugin, _ = await start({"rules": ["prefix: !ping => Pong"]})
    await plugin.on_message(msg("!ping", dm=True))
    (_, params) = [c for c in host.calls if c[0] == "messages.send"][0]
    assert params["to"] == {"contact_key": "ab" * 32} and params["text"] == "Pong"


async def test_a_refused_send_is_logged_and_counted(host):
    host.fail = {"code": -32010, "message": "x", "data": {"code": "quota_exceeded"}}
    plugin, _ = await start({"rules": ["keyword: hi => x"]})
    await plugin.on_message(msg("hi"))
    assert any("quota_exceeded" in str(p) for m, p in host.calls if m == "log")
    assert len(plugin._sent) == 1  # a refused send still uses the plugin's own budget


async def test_the_reply_is_cut_to_the_radio_limit(host):
    plugin, _ = await start({"rules": ["keyword: hi => " + "é" * 300]})
    await plugin.on_message(msg("hi"))
    assert len(host.sent[0].encode()) <= 156


async def test_bad_rules_are_logged_at_start_and_status_is_localised(host):
    plugin = AutoResponder()
    await plugin.on_init({"config": {"rules": ["nonsense"]}, "lang": "fr"})
    logs = [p["message"] for m, p in host.calls if m == "log"]
    assert logs == ["rule 1 ignored: expected 'trigger: text => reply'"]
    status = [p["summary"] for m, p in host.calls if m == "status.set"]
    assert status == ["0 règle(s), 0 réponse(s) la dernière heure"]


async def test_config_change_reloads_the_rules(host):
    plugin, _ = await start({"rules": ["keyword: a => one"]})
    await plugin.on_config_changed({"config": {"rules": ["keyword: b => two"]}})
    await plugin.on_message(msg("a"))
    await plugin.on_message(msg("b"))
    assert host.sent == ["two"]


# --- two hosts, two auto-responders, the real host guards ---------------------------


async def test_two_auto_responders_on_two_hosts_never_exceed_their_budgets(monkeypatch):
    """Both answer anything that says "hello", including each other, for two hours, on
    the real `SendGuard` with a controlled clock. The exchange ends, and no host sends
    more than its cap."""
    from test_plugin_quotas import CHANNEL, T0, Clock, MemoryStore

    clock = Clock(T0)
    config = {"rules": ["keyword: hello => hello back"], "max_replies_per_hour": 5}
    hosts = {}
    for name in "AB":
        plugin = AutoResponder()
        plugin.clock = clock
        plugin.config = dict(config)
        plugin._load_rules()
        hosts[name] = SimpleNamespace(
            plugin=plugin, guard=SendGuard(clock, MemoryStore()), sent=0, refused=[]
        )

    log = [(T0, "human")]
    heard = [(T0 + 1, n, "human") for n in hosts]
    while heard:
        heard.sort(key=lambda h: h[0])
        at, name, author = heard.pop(0)
        if at > T0 + 2 * HOUR:
            break
        clock.now = at
        h = hosts[name]
        if author == name:
            continue
        instance = f"ar-{name}"
        h.guard.note_inbound(
            instance, {"channel_key": CHANNEL, "sender_name": author, "received_at": int(at)}
        )

        async def reply(message, text, *, region=None, _h=h, _i=instance, _at=at, _n=name):
            try:
                await _h.guard.check(
                    _i,
                    is_dm=False,
                    key=CHANNEL,
                    sender=f"name:{message.sender_name}",
                    received_at=int(_at),
                )
            except GuardRefused as exc:
                _h.refused.append(exc.scope)
                raise HostError({"code": -32010, "message": "x"}) from None
            await _h.guard.record(
                _i, is_dm=False, key=CHANNEL, sender=f"name:{message.sender_name}"
            )
            _h.sent += 1
            log.append((_at, _n))
            for other in hosts:
                heard.append((_at + 1, other, _n))

        h.plugin.reply = reply
        h.plugin.set_status = lambda s: _noop()
        h.plugin.storage = SimpleNamespace(set=lambda *a: _noop())
        h.plugin.log = lambda *a: None
        message = msg("hello", sender=author)
        await h.plugin.on_message(message)

    assert 2 <= len(log) <= 8, log
    for h in hosts.values():
        assert h.sent <= 5
    assert hosts["A"].sent + hosts["B"].sent == len(log) - 1


async def _noop():
    return None


# --- through the host ---------------------------------------------------------------

pytestmark_host = pytest.mark.skipif(sys.platform == "win32", reason="no plugins on Windows")

FAST = Budgets(
    init_timeout=10.0,
    event_timeout=5.0,
    cancel_grace=0.6,
    shutdown_grace=1.0,
    stop_timeout=5.0,
    backoff_initial=0.05,
    backoff_max=0.2,
    stagger=0.05,
    message_settle=0.0,
)


@pytest.fixture
async def api(test_db, tmp_path, monkeypatch):
    from app.main import app
    from app.routers import plugins as router

    mgr = PluginManager(
        FAST,
        plugins_root=tmp_path / "plugins",
        cpu_reader=None,
        mem_total=lambda: 8 * 1024**3,
    )
    monkeypatch.setattr(router, "plugin_manager", mgr)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.manager = mgr  # type: ignore[attr-defined]
        yield client
    await mgr.stop_all(5.0)


async def _install(api, plugin_id, grant, config=None):
    response = await api.post(
        "/api/plugins/install",
        json={
            "kind": "bundled",
            "plugin_id": plugin_id,
            "granted_permissions": grant,
            "config": config or {},
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()
    instance = api.manager.get(data["instance_id"])
    deadline = 15.0
    import asyncio

    while instance.status != "running" and deadline > 0:
        await asyncio.sleep(0.02)
        deadline -= 0.02
    assert instance.status == "running"
    return data


@pytestmark_host
async def test_both_plugins_are_listed_and_install_offline(api):
    listed = {p["plugin_id"]: p for p in (await api.get("/api/plugins/bundled")).json()}
    assert set(listed) == {"meshloom.echo", "meshloom.auto-responder"}
    assert listed["meshloom.auto-responder"]["permissions"] == {
        "messages.read": {"channels": "public", "direct": True, "outgoing": False},
        "messages.send": {"targets": "reply"},
        "storage": {},
    }
    assert listed["meshloom.echo"]["config_schema"] == {}


@pytestmark_host
async def test_echo_answers_pling_through_the_host(api):
    grant = {
        "messages.read": {"channels": "public", "direct": True, "outgoing": False},
        "messages.send": {"targets": "reply"},
    }
    created = await _install(api, "meshloom.echo", grant)
    assert created["kind"] == "bundled"
    url = f"/api/plugins/{created['instance_id']}/test"
    answered = (await api.post(url, json={"text": "!pling"})).json()
    assert [s["text"] for s in answered["sends"]] == ["Plong!"]
    silent = (await api.post(url, json={"text": "pling"})).json()
    assert silent["sends"] == []


@pytestmark_host
async def test_auto_responder_through_the_host_with_cooldown_and_cap(api):
    listed = {p["plugin_id"]: p for p in (await api.get("/api/plugins/bundled")).json()}
    grant = listed["meshloom.auto-responder"]["permissions"]
    config = {
        "rules": ["keyword: hello => Hi {sender}!", "keyword: bye => Bye"],
        "max_replies_per_hour": 1,
    }
    created = await _install(api, "meshloom.auto-responder", grant, config)
    assert created["status"] in {"installed", "starting", "running"}
    url = f"/api/plugins/{created['instance_id']}/test"
    first = (await api.post(url, json={"text": "hello world"})).json()
    assert [s["text"] for s in first["sends"]] == ["Hi Tester!"]
    again = (await api.post(url, json={"text": "hello world"})).json()
    assert again["sends"] == []  # the rule's cooldown for this sender
    capped = (await api.post(url, json={"text": "bye"})).json()
    assert capped["sends"] == []  # one answer per hour was the cap


@pytestmark_host
async def test_install_rejects_an_invalid_rule_count(api):
    listed = {p["plugin_id"]: p for p in (await api.get("/api/plugins/bundled")).json()}
    grant = listed["meshloom.auto-responder"]["permissions"]
    response = await api.post(
        "/api/plugins/install",
        json={
            "kind": "bundled",
            "plugin_id": "meshloom.auto-responder",
            "granted_permissions": grant,
            "config": {"rules": ["keyword: a => b"] * 51},
        },
    )
    assert response.status_code == 400
