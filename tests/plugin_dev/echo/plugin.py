"""Echo: answers every message with its own text (the reference plugin of `dev check`)."""

from meshloom_plugin import host


async def on_message(event):
    await host().call("messages.send", {"to": "reply", "text": event["message_text"]})
