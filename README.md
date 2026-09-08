# cctab

A Telegram push when a long Claude Code task finishes, carrying what it cost and
how much of your rate limit it burned. When it stops to ask permission, two
buttons in the same chat set it going again.

![A finished task in Telegram](docs/task-done.png)

You start something big, walk away, and come back to find it either done twenty
minutes ago or stopped on a yes-or-no the whole time. cctab closes that gap, and
tells you the price while it is at it.

## What makes it different

Twenty-odd projects send Claude Code notifications. None of them put money in
the message, because Claude Code does not hand cost to hooks
([#11008](https://github.com/anthropics/claude-code/issues/11008)) and you have
to read the session transcripts yourself to work it out.

| | cctab | Typical notifier | Official Telegram channel |
|---|---|---|---|
| Says a task finished | yes | yes | no, it answers you in chat |
| Dollars spent | **yes** | no | no |
| Rate limit left | **yes** | no | no |
| What each tab cost | **yes** | no | no |
| Silent on short tasks | **yes** | no | n/a |
| Settings from your phone | **yes** | no | no |
| Approve a tool from your phone | yes, two buttons | no | yes, by typing a reply code |
| What it takes to run | a hook | a hook | Bun, an MCP server, `--channels` |
| Token in the system keychain | yes | usually a plain file | yes |

The official Telegram channel is a chat bridge that can also
[relay permission prompts](https://code.claude.com/docs/en/channels-reference#relay-permission-prompts)
— it is in research preview, runs an MCP server on Bun, and you answer by typing
`yes` with a five-character request id. cctab is a plain hook with two buttons.
They do not fight; run both if you want the chat as well.

## Quiet by default

The reason most people uninstall a notifier is that it fires on everything. Out
of the box cctab says nothing until a task has run for thirty minutes. Failures
always come through, whatever the threshold.

![Settings, over Telegram](docs/settings.png)

Send `/settings` to your bot and change it from wherever you are. One message,
three tabs: how long a task must run before it counts, which messages reach you,
and what every tab of yours has cost. It rewrites itself in place, so the chat
never fills up with old menus. No config file, no restart.

`/spend` opens the third tab on its own. It reads the session transcripts, takes
the tab names Claude Code already wrote there, and prices every one of them.

## Install

```
/plugin marketplace add USER/cctab
/plugin install cctab
```

Then ask Claude to set up cctab, or run the helper directly:

```
scripts/setup.py
```

It hands you a bot username nobody has taken yet, along with the three lines to
send @BotFather. Paste the token back and it checks the token, warns you if a
webhook would swallow the updates, and saves it. Press Start in the chat and you
are connected. About forty seconds.

To keep the token in your system keychain rather than a file, paste it into the
plugin's `bot_token` setting afterwards.

## What it looks like

A task that fell over, so you know before you sit back down:

![A failed task](docs/task-failed.png)

Something waiting on you, with the command it wants to run:

![A permission request](docs/permission.png)

A question, with the options it is choosing between:

![A question](docs/question.png)

And a warning while there is still time to do something about it:

![A limit warning](docs/limit.png)

## Where the rate limits come from

Claude Code hands rate limits to the statusline command and nowhere else, so a
hook cannot see them. cctab takes them in this order:

1. **A statusline wrapper.** Official numbers, no network, no tokens. It reads
   the limits and passes the payload straight through to whatever statusline you
   already run, so nothing you have set up breaks. Point `statusline_command` at
   your existing command and add this to `settings.json`:

   ```json
   "statusLine": { "type": "command", "command": "~/.claude/plugins/cctab/scripts/statusline.py" }
   ```

2. **The usage endpoint**, off unless you turn on `use_usage_api`. It reads your
   local OAuth token and calls an endpoint Anthropic has not documented. It is
   opt-in on purpose.

3. **Nothing.** The message still carries what the task cost.

Cost itself never depends on any of this. It is computed from the session
transcript, deduplicated per request, priced per model.

## Settings

| Option | Default | What it does |
|---|---|---|
| `bot_token` | none | From @BotFather. Stored in the keychain |
| `chat_id` | auto | Captured the first time you message the bot |
| `min_seconds` | 1800 | Starting quiet threshold; change it from the phone later |
| `statusline_command` | none | Your existing statusline, run untouched after ours |
| `use_usage_api` | off | Fall back to the undocumented usage endpoint |
| `name_tasks` | off | Label each push using Haiku. Costs you one small call per session |

## Approvals, and their limits

When Claude Code needs permission, cctab puts the ask in front of you with the
tool, the actual command, and two buttons. Tap one and the session carries on
without you. Ignore it and after two minutes the prompt goes to the terminal
exactly as it would have.

What it will never do is take dictation. Nothing can be typed into a session
from the phone: the only thing that travels back is a yes or a no to something
Claude Code asked first. A stolen chat cannot compose its own command — though
it can approve one, so treat the chat as you treat the machine.

Questions are different. A hook can answer yes or no and nothing else, so when
Claude asks you to *choose* between options, cctab can only tell you it is
waiting. There is nowhere to put the answer. Those messages say GO TO THE
TERMINAL, and mean it.

## What it does not do

No server of ours in the middle. Your token talks to your bot, and nothing
leaves your machine except the message itself. To drive a session remotely
rather than answer it, Claude Code has Remote Control built in.

## License

MIT
