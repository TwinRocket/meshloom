"""Auto-responder: answers messages with replies written as one-line rules, no code.

A rule is `trigger[,option=value...]: text => reply`, for example
`prefix,channel=#test,cooldown=120: !ping => Pong, {sender}!`. This module has no
dependency besides the SDK; `parse_rule` and `Rule.matches` are plain functions the
tests call directly. The host enforces the hard limits whatever this plugin does (the
send quota, one reply per 30 s to the same sender, the destination budgets); the
cooldowns and the hourly cap here only keep the plugin below them. Docs:
docs/dev/plugins.md ("Bundled plugins").
"""

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from meshloom_plugin import Message, Plugin
from meshloom_plugin.errors import HostError
from meshloom_plugin.sdk import error_code

MAX_REGEX_LENGTH = 100
REGEX_COMPILE_BUDGET = 0.1  # seconds
# A regex runs on the head of the message only: `re` has no timeout, so the work is bounded.
MATCH_TEXT_LIMIT = 200
HOUR = 3600
STORAGE_KEY = "sent"
MAX_TRACKED_SENDERS = 2000

# The head of a rule, up to the colon. No nested or overlapping repeats (it runs on
# user text); the rest of the line is split on the first " => " with `str.partition`.
_HEAD = re.compile(r" ?(keyword|prefix|regex)((?: ?, ?[a-z]+=[^,:\s]+)*) ?: ?")
_STATUS = {
    "en": "{rules} rule(s), {replies} answer(s) in the last hour",
    "fr": "{rules} règle(s), {replies} réponse(s) la dernière heure",
}


class RuleError(ValueError):
    """A rule that cannot be used; the message is safe to log."""


@dataclass(frozen=True)
class Rule:
    trigger: str
    text: str
    reply: str
    channel: str | None  # a channel name without "#", lowercase; "dm" = direct messages only
    cooldown: int
    case_sensitive: bool
    pattern: "re.Pattern[str] | None" = None

    def matches(self, msg: Message) -> bool:
        if self.channel is not None:
            if msg.is_dm:
                if self.channel != "dm":
                    return False
            elif self.channel != _channel_key(msg.channel_name):
                return False
        text = msg.text.strip()
        if self.pattern is not None:
            return self.pattern.search(text[:MATCH_TEXT_LIMIT]) is not None
        if not self.case_sensitive:
            text = text.casefold()
        wanted = self.text if self.case_sensitive else self.text.casefold()
        if self.trigger == "prefix":
            return text.startswith(wanted)
        return re.search(rf"(?<!\w){re.escape(wanted)}(?!\w)", text) is not None

    def render(self, msg: Message) -> str:
        values = {
            "{sender}": msg.sender_name or "",
            "{channel}": msg.channel_name or "",
            "{text}": msg.text,
        }
        return re.sub(r"\{(?:sender|channel|text)\}", lambda m: values[m.group(0)], self.reply)


def _channel_key(name: str | None) -> str:
    return (name or "").strip().lstrip("#").casefold()


# The regex dialect is a structural whitelist on the parse tree (`re` has no timeout, so
# a pattern must be cheap by construction, not by test). Refused: back-references,
# lookarounds, a large repeat whose body holds another repeat or an alternation, more than
# two large repeats in a row, and more than two large repeats in all. A repeat is
# "large" above SMALL_REPEAT. Everything else is polynomial of low degree on at most
# MATCH_TEXT_LIMIT characters.
SMALL_REPEAT = 5
MAX_LARGE_REPEATS = 2
MAX_ADJACENT_LARGE_REPEATS = 2  # also the cap in all
_REPEATS = ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT")
_REFUSED_OPS = ("GROUPREF", "GROUPREF_EXISTS", "ASSERT", "ASSERT_NOT")


def _name(op: Any) -> str:
    return getattr(op, "name", str(op))


def _is_large(node: Any) -> bool:
    return _name(node[0]) in _REPEATS and node[1][1] > SMALL_REPEAT


def _flatten(nodes: Any) -> list[Any]:
    """The nodes of a sequence with plain (non-repeated) groups inlined."""
    out: list[Any] = []
    for node in nodes:
        if _name(node[0]) in ("SUBPATTERN", "ATOMIC_GROUP"):
            out.extend(_flatten(node[1][-1] if _name(node[0]) == "SUBPATTERN" else node[1]))
        else:
            out.append(node)
    return out


def _children(node: Any) -> list[Any]:
    op, av = _name(node[0]), node[1]
    if op in _REPEATS:
        return [av[2]]
    if op == "SUBPATTERN":
        return [av[3]]
    if op == "ATOMIC_GROUP":
        return [av]
    if op == "BRANCH":
        return list(av[1])
    return []


def _contains(nodes: Any, wanted: tuple[str, ...]) -> bool:
    for node in nodes:
        if _name(node[0]) in wanted or any(_contains(c, wanted) for c in _children(node)):
            return True
    return False


def _check_tree(nodes: Any, counts: list[int]) -> None:
    run = 0
    for node in _flatten(nodes):
        op = _name(node[0])
        if op in _REFUSED_OPS:
            raise RuleError("a regex may not use back-references or lookarounds")
        if _is_large(node):
            run += 1
            counts[0] += 1
            if run > MAX_ADJACENT_LARGE_REPEATS or counts[0] > MAX_LARGE_REPEATS:
                raise RuleError("a regex has at most 2 repeats such as * or +")
            if _contains(node[1][2], (*_REPEATS, "BRANCH")):
                raise RuleError("a regex may not repeat a group that holds a repeat or a choice")
        else:
            run = 0
        for child in _children(node):
            _check_tree(child, counts)


def check_regex(source: str) -> None:
    """Raise RuleError unless `source` is inside the restricted dialect."""
    try:
        tree = re._parser.parse(source)  # type: ignore[attr-defined]
    except (re.error, RecursionError, OverflowError) as exc:
        raise RuleError(f"invalid regex: {getattr(exc, 'msg', 'too complex')}") from None
    _check_tree(tree, [0])


def compile_pattern(source: str, *, case_sensitive: bool) -> "re.Pattern[str]":
    if len(source) > MAX_REGEX_LENGTH:
        raise RuleError(f"a regex is {MAX_REGEX_LENGTH} characters at most")
    check_regex(source)
    started = time.perf_counter()
    try:
        pattern = re.compile(source, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise RuleError(f"invalid regex: {exc.msg}") from None
    if time.perf_counter() - started > REGEX_COMPILE_BUDGET:
        raise RuleError("the regex takes too long to compile")
    return pattern


def parse_rule(line: str, *, default_cooldown: int, case_sensitive: bool = False) -> Rule:
    head = _HEAD.match(line.strip())
    if head is None:
        raise RuleError("expected 'trigger: text => reply'")
    text, arrow, reply = line.strip()[head.end() :].partition(" => ")
    text, reply = text.strip(), reply.strip()
    if not arrow or not text or not reply:
        raise RuleError("expected 'trigger: text => reply'")
    channel: str | None = None
    cooldown = default_cooldown
    for option in filter(None, (o.strip() for o in head[2].split(","))):
        name, _, value = option.partition("=")
        value = value.strip()
        if name.strip() == "channel":
            channel = _channel_key(value)
            if not channel:
                raise RuleError("channel= needs a name")
        elif name.strip() == "cooldown":
            if not value.isdigit() or not 30 <= int(value) <= 86400:
                raise RuleError("cooldown= is a number of seconds from 30 to 86400")
            cooldown = int(value)
        else:
            raise RuleError(f"unknown option '{name.strip()}'")
    pattern = None
    if head[1] == "regex":
        pattern = compile_pattern(text, case_sensitive=case_sensitive)
    return Rule(head[1], text, reply, channel, cooldown, case_sensitive, pattern)


def parse_rules(
    lines: Any, *, default_cooldown: int, case_sensitive: bool
) -> tuple[list[Rule], list[str]]:
    """The usable rules, and one problem line per rule that was left out."""
    rules: list[Rule] = []
    problems: list[str] = []
    for number, line in enumerate(lines if isinstance(lines, list) else [], start=1):
        if not isinstance(line, str) or not line.strip():
            continue
        try:
            rules.append(
                parse_rule(line, default_cooldown=default_cooldown, case_sensitive=case_sensitive)
            )
        except RuleError as exc:
            problems.append(f"rule {number} ignored: {exc}")
    return rules, problems


class AutoResponder(Plugin):
    def __init__(self) -> None:
        super().__init__()
        self.rules: list[Rule] = []
        self.clock = time.time
        self._last: dict[tuple[int, str], float] = {}
        self._sent: deque[float] = deque()

    async def on_start(self) -> None:
        self._load_rules()
        stored = await self._safe_storage_get()
        now = self.clock()
        self._sent = deque(sorted(t for t in stored if now - HOUR < t <= now))
        await self._status()

    async def on_config_changed(self, params: Any) -> None:
        await super().on_config_changed(params)
        self._load_rules()
        await self._status()

    def _load_rules(self) -> None:
        config = self.config
        cooldown = config.get("cooldown_seconds")
        rules, problems = parse_rules(
            config.get("rules", []),
            default_cooldown=cooldown if type(cooldown) is int else 120,
            case_sensitive=config.get("case_sensitive") is True,
        )
        self.rules = rules
        self._last.clear()
        for problem in problems:
            self.log("warning", problem)

    @property
    def max_per_hour(self) -> int:
        value = self.config.get("max_replies_per_hour")
        return value if type(value) is int and value > 0 else 20

    async def on_message(self, msg: Message) -> None:
        if msg.is_outgoing or not msg.text.strip():
            return
        if msg.is_dm and self.config.get("answer_direct_messages") is False:
            return
        for index, rule in enumerate(self.rules):
            if rule.matches(msg):
                await self._answer(index, rule, msg)
                return  # the first rule that matches decides, even when it stays silent

    async def _answer(self, index: int, rule: Rule, msg: Message) -> None:
        now = self.clock()
        who = msg.sender_key or msg.sender_name or ""
        if now - self._last.get((index, who), 0.0) < rule.cooldown:
            return
        while self._sent and now - self._sent[0] >= HOUR:
            self._sent.popleft()
        if len(self._sent) >= self.max_per_hour:
            self.log("info", "hourly answer cap reached, staying silent")
            return
        # Counted before the send: a refused or failed send still uses the budget.
        self._last[(index, who)] = now
        if len(self._last) > MAX_TRACKED_SENDERS:
            self._last = {k: t for k, t in self._last.items() if now - t < rule.cooldown}
        self._sent.append(now)
        try:
            await self.reply(msg, self.fit(rule.render(msg)))
        except HostError as exc:
            self.log("info", f"answer refused: {error_code(exc) or 'host error'}")
            return
        await self._safe_storage_set()
        await self._status()

    async def _status(self) -> None:
        now = self.clock()
        replies = sum(1 for t in self._sent if now - t < HOUR)
        text = _STATUS.get(self.lang, _STATUS["en"]).format(rules=len(self.rules), replies=replies)
        try:
            await self.set_status(text)
        except HostError:
            pass

    async def _safe_storage_get(self) -> list[float]:
        try:
            stored = await self.storage.get(STORAGE_KEY, [])
        except HostError:
            return []
        return [t for t in stored if type(t) in (int, float)] if isinstance(stored, list) else []

    async def _safe_storage_set(self) -> None:
        try:
            await self.storage.set(STORAGE_KEY, list(self._sent))
        except HostError:
            self.log("warning", "could not save the answer counter")
