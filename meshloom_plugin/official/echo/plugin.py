"""Echo: answers "!pling" with "Plong!" in the conversation it came from.

The smallest useful plugin, and the reference to copy: subclass `Plugin`, override
`on_message`, answer with `self.reply`. Docs: docs/dev/plugins.md ("SDK").
"""

from meshloom_plugin import Message, Plugin


class Echo(Plugin):
    async def on_message(self, msg: Message) -> None:
        if msg.text.strip() == "!pling":
            await self.reply(msg, "Plong!")
