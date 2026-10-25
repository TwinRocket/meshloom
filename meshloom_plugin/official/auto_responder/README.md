# Auto-responder

Answers messages with replies you write as simple rules. No code.

- **Reads** the messages of public channels and your direct messages.
- **Sends** only replies, in the conversation of the message it answers.
- **Stores** a small counter of its recent answers, never message text. No network.

## Rules

One rule per line in **Rules**:

```
trigger[,option=value]: text to look for => reply   (a space on each side of =>)
```

| Trigger | The rule applies when... |
|---|---|
| `keyword` | the word or phrase appears in the message (whole words) |
| `prefix` | the message starts with the text |
| `regex` | the text, a pattern of 100 characters at most, is found in the first 200 characters of the message |

Options, after the trigger and separated by commas:

- `channel=#name` limits the rule to one channel (`channel=dm` for direct messages only);
- `cooldown=N` sets the pause, in seconds (30 to 86400), before this rule answers the same person again. The default is the **Pause** setting.

The reply may contain `{sender}` (the sender's name), `{channel}` (the channel name) and `{text}` (the message). Examples:

```
keyword: hello => Hello {sender}!
prefix,channel=#test,cooldown=300: !ping => Pong, {sender}!
regex: ^(hi|hey)\b => Hi {sender}, welcome to {channel}
```

The first rule that matches decides. A rule that cannot be read is ignored and written to the log with its line number. 
### The regex dialect

A pattern must be cheap to run, so `regex` accepts a restricted dialect and a rule outside it is ignored (with the reason in the log). Allowed: literals, `.`, character classes, `^`, `$`, `\b`, `\d`, `\w`, `\s`, groups, `|` and repeats. Refused:

- at most **2** repeats of more than 5 times in a pattern (`*`, `+`, `?` does not count, `{6,}`, `{1,50}`...);
- a repeated group that holds another repeat or a choice: `(a+)+`, `(a|aa)*`, `(ab|c){6,}`;
- back-references (`\1`) and lookarounds (`(?=...)`, `(?!...)`, `(?<=...)`);
- more than 100 characters.

The pattern is only tried on the first 200 characters of the message.

## Limits

The plugin never answers more than **Most answers per hour** (20 by default), which stays under the 30 per hour Meshloom allows each copy. On top of that Meshloom itself answers nothing faster than one message every 30 seconds to the same person, and caps what all plugins send together. Upper and lower case are ignored unless you turn on **Respect upper and lower case**. Messages you send yourself are never answered.
