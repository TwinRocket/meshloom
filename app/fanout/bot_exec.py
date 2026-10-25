"""
Bot execution module for automatic message responses.

This module provides functionality for executing user-defined Python code
in response to incoming messages. The user's code can process message data
and optionally return a response string or a list of strings.

SECURITY WARNING: This executes arbitrary Python code provided by the user.
It should only be enabled on trusted systems where the user understands
the security implications.
"""

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

from app.loop_lock import LoopBoundSemaphore
from app.message_limits import is_message_too_long
from app.plugins.quotas import SEND_SPACING_SECONDS, send_spacer
from meshloom_plugin.compat import (
    REGION_UNSET,
    BotResult,
    call_bot,
    coerce_result,
)
from meshloom_plugin.compat import analyze_bot_signature as _analyze_bot_signature

logger = logging.getLogger(__name__)

# Limit concurrent bot executions to prevent resource exhaustion
_bot_semaphore = LoopBoundSemaphore(100)

# Dedicated thread pool for bot execution (separate from default executor)
_bot_executor = ThreadPoolExecutor(max_workers=100, thread_name_prefix="bot_")

# Timeout for bot code execution (seconds)
BOT_EXECUTION_TIMEOUT = 10

# Minimum spacing between automatic sends (seconds), shared with the plugin
# instances: `app/plugins/quotas.py` (`send_spacer`) owns it.
BOT_MESSAGE_SPACING = SEND_SPACING_SECONDS

# global container for persistent data storage between bot executions, will be added to execution namespace
_bot_globals: dict[str, Any] = {}


@dataclass(frozen=True)
class BotReply:
    """A structured bot response that may scope its outgoing message(s).

    Produced when a bot returns ``{"region": ..., "message": ...}``. The plain
    ``str``/``list[str]`` return shapes are unaffected and never produce a BotReply.

    ``flood_scope_override`` carries the per-send region decision for the reply:
      - ``None``  → no region specified; use the channel's persisted override
      - ``""``    → region explicitly cleared; send unscoped/plain flood
      - region    → scope the reply send to that region name
    """

    messages: list[str]
    flood_scope_override: str | None = None


def _to_bot_reply(result: BotResult) -> BotReply:
    """Turn a normalised bot result into a BotReply, region in the radio's form.

    An absent region means "use the channel default", ``None`` means "send
    unscoped", and a string is normalized to the radio's region form.
    """
    from app.region_scope import normalize_region_scope

    if result.region is REGION_UNSET:
        override: str | None = None
    elif result.region is None:
        override = ""  # explicit: send unscoped
    else:
        override = normalize_region_scope(result.region)
    return BotReply(messages=result.messages, flood_scope_override=override)


def execute_bot_code(
    code: str,
    sender_name: str | None,
    sender_key: str | None,
    message_text: str,
    is_dm: bool,
    channel_key: str | None,
    channel_name: str | None,
    sender_timestamp: int | None,
    path: str | None,
    is_outgoing: bool = False,
    path_bytes_per_hop: int | None = None,
    packet_hash: str | None = None,
    region: str | None = None,
    scoped: bool = False,
) -> str | list[str] | BotReply | None:
    """
    Execute user-provided bot code with message context.

    The code should define a function:
    `bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path, is_outgoing, path_bytes_per_hop, packet_hash)`
    or use named parameters / `**kwargs`.

    `region` and `scoped` are only delivered to bots that opt in via `**kwargs`
    or by naming the parameter; the positional call styles are unchanged for
    backward compatibility.

    The bot returns either None (no response), a string (single response message),
    a list of strings (multiple messages sent in order), or a dict
    `{"region": <name|None>, "message": <str|list[str]>}` to scope the reply to a
    region for this send only (`None`/empty region = unscoped flood). The dict
    form only affects channel replies; `region` is ignored for DM replies.

    Legacy bot functions with older signatures are detected via inspect and
    called without the newer parameters for backward compatibility.

    Args:
        code: Python code defining the bot function
        sender_name: Display name of the sender (may be None)
        sender_key: 64-char hex public key of sender for DMs, None for channel messages
        message_text: The message content
        is_dm: True for direct messages, False for channel messages
        channel_key: 32-char hex channel key for channel messages, None for DMs
        channel_name: Channel name (e.g. "#general" with hash), None for DMs
        sender_timestamp: Sender's timestamp from the message (may be None)
        path: Hex-encoded routing path (may be None)
        is_outgoing: True if this is our own outgoing message
        path_bytes_per_hop: Number of bytes per routing hop (1, 2, or 3), if known
        packet_hash: MeshCore packet hash (first 16 hex chars of SHA256, uppercase), if known
        scoped: True if the message carried a regional flood scope, False for
            plain/unscoped flood. Check this first — it is the meaningful signal.
            Set for scoped DMs too (flood-direct messages can carry a scope).
        region: Only meaningful when scoped is True. When scoped is False, region
            is always None and should be ignored. When scoped is True, region is
            the decoded region name, or None if the scope matched none of your
            known_regions (i.e. scoped, but region unrecognized). region is never
            enough on its own to tell "unscoped" from "unrecognized" — use scoped.

    Returns:
        Response string, list of strings, or None.

    Note: This executes arbitrary code. Only use with trusted input.
    """
    if not code or not code.strip():
        return None

    # Build execution namespace with allowed imports
    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
        "_bot_globals": _bot_globals,
    }

    try:
        # Execute the user's code to define the bot function
        exec(code, namespace)
    except Exception:
        logger.exception("Bot code compilation failed")
        return None

    # Check if bot function was defined
    if "bot" not in namespace or not callable(namespace["bot"]):
        logger.debug("Bot code does not define a callable 'bot' function")
        return None

    bot_func = namespace["bot"]
    try:
        call_plan = _analyze_bot_signature(bot_func)
    except ValueError as exc:
        logger.error("%s", exc)
        return None

    try:
        result = call_bot(
            bot_func,
            call_plan,
            {
                "sender_name": sender_name,
                "sender_key": sender_key,
                "message_text": message_text,
                "is_dm": is_dm,
                "channel_key": channel_key,
                "channel_name": channel_name,
                "sender_timestamp": sender_timestamp,
                "path": path,
                "is_outgoing": is_outgoing,
                "path_bytes_per_hop": path_bytes_per_hop,
                "packet_hash": packet_hash,
                "region": region,
                "scoped": scoped,
            },
        )

        normalized = coerce_result(result, logger.debug)
        if normalized is None:
            return None
        if isinstance(result, dict):
            # Structured reply: {"region": ..., "message": str | list[str]}
            return _to_bot_reply(normalized)
        if isinstance(result, str):
            return result
        return normalized.messages

    except Exception:
        logger.exception("Bot function execution failed")
        return None


async def process_bot_response(
    response: str | list[str] | BotReply,
    is_dm: bool,
    sender_key: str,
    channel_key: str | None,
) -> None:
    """
    Send the bot's response message(s) using the existing message sending endpoints.

    For DMs, sends a direct message back to the sender.
    For channel messages, sends to the same channel.

    Bot messages are rate-limited to ensure at least BOT_MESSAGE_SPACING seconds
    between sends, giving repeaters time to return to listening mode.

    Args:
        response: The response to send — a string, a list of messages to send in
            order, or a BotReply carrying a per-send region scope
        is_dm: Whether the original message was a DM
        sender_key: Public key of the original sender (for DM replies)
        channel_key: Channel key for channel message replies
    """
    # Normalize to (messages, flood_scope_override) for uniform processing.
    if isinstance(response, BotReply):
        messages = response.messages
        flood_scope_override = response.flood_scope_override
    elif isinstance(response, str):
        messages = [response]
        flood_scope_override = None
    else:
        messages = response
        flood_scope_override = None

    for message_text in messages:
        await _send_single_bot_message(
            message_text, is_dm, sender_key, channel_key, flood_scope_override
        )


async def _send_single_bot_message(
    message_text: str,
    is_dm: bool,
    sender_key: str,
    channel_key: str | None,
    flood_scope_override: str | None = None,
) -> None:
    """
    Send a single bot message with rate limiting.

    Args:
        message_text: The message text to send
        is_dm: Whether the original message was a DM
        sender_key: Public key of the original sender (for DM replies)
        channel_key: Channel key for channel message replies
        flood_scope_override: Per-send region scope for channel replies (None =
            channel default, "" = unscoped, region name = scope to that region).
            Ignored for DMs, which are not region-scoped.
    """
    from app.models import SendChannelMessageRequest, SendDirectMessageRequest
    from app.routers.messages import send_channel_message, send_direct_message

    # Serialize automatic sends (bots and plugins) and enforce the minimum spacing
    async with send_spacer.slot() as slot:
        try:
            if is_dm:
                if flood_scope_override is not None:
                    logger.debug(
                        "Ignoring bot region scope %r on DM reply (DMs are not region-scoped)",
                        flood_scope_override,
                    )
                logger.info("Bot sending DM reply to %s", sender_key[:12])
                request = SendDirectMessageRequest(destination=sender_key, text=message_text)
                await send_direct_message(request)
            elif channel_key:
                logger.info("Bot sending channel reply to %s", channel_key[:8])
                request = SendChannelMessageRequest(
                    channel_key=channel_key,
                    text=message_text,
                    flood_scope_override=flood_scope_override,
                )
                await send_channel_message(request)
            else:
                logger.warning("Cannot send bot response: no destination")
                return  # Don't update timestamp if we didn't send
        except HTTPException as e:
            if is_message_too_long(e):
                logger.warning("Bot reply refused, too long: %s", e.detail)
                return
            logger.error("Bot failed to send response: %s", e.detail, exc_info=True)
            return  # Don't update timestamp on failure
        except Exception:
            logger.exception("Bot failed to send response")
            return  # Don't update timestamp on failure

        # Only a successful send moves the spacing reference
        slot.sent()
