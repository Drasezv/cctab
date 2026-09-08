# cctab

Telegram message when a long Claude Code task finishes, with the cost and how
much of the rate limit it used.

<img src="docs/task-done.png" alt="A finished task in Telegram" width="440">

The dollar figure is what those tokens cost at Anthropic's API rates. On a
subscription you are not billed per token, so treat it as a comparison number
between tabs, not a charge.

## Compared to other notifiers

Most of them just say "done". A few do more:
[claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram)
appends cost, [echook](https://github.com/ChanMeng666/echook) does rate limits
and a quiet threshold, [ccgram](https://github.com/jsayubi/ccgram) does limits
and approval buttons, [tg-claude-bot](https://github.com/xhyumiracle/tg-claude-bot)
does usage bars and settings from the phone,
[Lucarne](https://github.com/tuchg/Lucarne) and
[claude-ntfy-hook](https://github.com/nickknissen/claude-ntfy-hook) do
approvals. Anthropic's [Channels](https://code.claude.com/docs/en/channels)
relays permission prompts.

I wanted all of it in one hook, with no server and nothing to install but
Python.

| | cctab | Official Telegram channel |
|---|---|---|
| Says a long task finished | yes | no |
| Cost, unprompted | yes | no |
| Rate limit left | yes | no |
| Cost per tab | yes | no |
| Silent on short tasks | yes | n/a |
| Settings from the phone | yes | no |
| Approve from the phone | two buttons | reply with a code |
| Needs | a hook | Bun, MCP server, `--channels` |

Cost is the awkward part. Claude Code does not pass it to hooks
([#11008](https://github.com/anthropics/claude-code/issues/11008)), so it comes
out of the session transcripts, per request, per model.

## Quiet by default

Nothing until a task has run for thirty minutes. Failures always come through.

<img src="docs/settings.png" alt="Settings, over Telegram" width="440">
<img src="docs/settings-time.png" alt="The time tab" width="440">
<img src="docs/settings-messages.png" alt="The messages tab" width="440">

`/settings` in the bot: threshold, which messages reach you, and spend. One
message with three tabs, rewritten in place. No config file, no restart.

`/spend` reads the transcripts and prices every tab, using the names Claude Code
already put there.

<img src="docs/spend.png" alt="What every tab cost" width="440">

## Install

```
/plugin marketplace add drasezv/cctab
/plugin install cctab
```

Then ask Claude to set up cctab. It gives you a free bot username, the lines for
@BotFather, and takes the token. Press Start in the chat. About forty seconds.

The token goes to `~/.cctab/config.json`, readable only by you. Paste it into
the plugin's `bot_token` setting to move it into the keychain.

Hooks are read at startup, so restart Claude Code once after installing.

## Messages

<img src="docs/greeting.png" alt="The first message" width="440">

A failed task shows the error it died on, not the answer it never reached:

<img src="docs/task-failed.png" alt="A failed task" width="440">

A permission request, with the command:

<img src="docs/permission.png" alt="A permission request" width="440">

A question, which has to be answered in the terminal:

<img src="docs/question.png" alt="A question" width="440">

A window running out, said once:

<img src="docs/limit.png" alt="A limit warning" width="440">

## Rate limits

Claude Code passes limits to the statusline and nowhere else. cctab tries, in
order:

1. **Statusline wrapper.** Ask Claude to wire it, or run `scripts/setup.py
   --statusline`. It takes the statusline slot and calls your own statusline
   after itself.

2. **Usage endpoint**, off unless `use_usage_api` is on. Reads the local OAuth
   token and calls an endpoint Anthropic has not documented.

   Needed in the VS Code extension: no statusline there, so the wrapper never
   runs.

3. **Nothing.** Cost still works; it comes from the transcript either way.

## Settings

| Option | Default | |
|---|---|---|
| `bot_token` | none | From @BotFather |
| `chat_id` | auto | Taken from your first message to the bot |
| `min_seconds` | 1800 | Starting threshold, changeable from the phone |
| `statusline_command` | none | Your statusline, run after ours |
| `use_usage_api` | off | Undocumented usage endpoint |
| `name_tasks` | off | Title each push with Haiku, one small call per session |

## Approvals

Off until you turn them on: `/settings`, Messages, `approvals`.

Once on, a permission request arrives with the command and two buttons. Tap one
and the session continues. While it waits, Claude Code waits too, up to a
minute, then the prompt goes to the terminal. That pause is why it ships off.

Nothing can be typed into a session from Telegram, only yes or no to what Claude
Code asked, and only from your own chat. Someone with your chat cannot write a
command, but can approve one.

Questions are different. A hook returns yes or no, so when Claude asks you to
pick an option, cctab can only say it is waiting.

## What leaves your machine

Only the messages, only to your bot, no server in between. A message can carry
the tab name, the last ~700 characters of the answer, an error, the command
waiting for approval, and on `/spend` your tab names across projects. Anything
shaped like a key or password is blanked first.

Telegram bot chats are not end-to-end encrypted.

Two settings reach out on their own: `name_tasks` sends the start of a session
to Haiku for a title, `use_usage_api` reads your OAuth token and asks Anthropic
for the limits.

## Uninstall

```
/plugin uninstall cctab
```

Delete `~/.cctab`, it holds the token. If you wired the statusline, put your old
command back in `~/.claude/settings.json`; it is saved as `statusline_command`
in `~/.cctab/config.json`.

## Requirements

Python 3.8+, nothing else. Tested on 3.9 and 3.10. Approvals need a Claude Code
with the `PermissionRequest` hook.

## License

MIT. [Drasezv](https://github.com/drasezv)
