# Echo

Answers `!pling` with `Plong!`, in the conversation the message came from (a public
channel or a direct message). It is the reference plugin: its whole code is
`plugin.py`, a few lines you can copy to write your own.

- **Reads** the messages of public channels and your direct messages.
- **Sends** only replies, in the conversation of the message it answers.
- No settings, no stored data, no network.

Meshloom's limits apply: at most one answer every 30 seconds to the same sender, 30
messages per hour for this copy.
