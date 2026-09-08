---
name: setup
description: Use when connecting cctab to Telegram for the first time, or when the user says notifications are not arriving and the bot may not be paired yet.
---

# Connecting cctab

Four steps. Do them one at a time and wait for the user between each.

## 1. Offer a username

Run `${CLAUDE_PLUGIN_ROOT}/scripts/setup.py` with no arguments. It prints three
lines for @BotFather, including a generated username that is almost certainly
free. Show those lines to the user exactly as printed.

The generated username is the point of this step. People lose minutes to
"Sorry, this username is already taken", so never ask them to invent one.

## 2. Take the token

BotFather answers with a token shaped like `8123456789:AAF...`. Ask the user to
paste it, then feed it in on stdin rather than as an argument. An argument shows
up in `ps` and stays in their shell history:

```
echo "<token>" | ${CLAUDE_PLUGIN_ROOT}/scripts/setup.py -
```

It checks the shape, checks the token against Telegram, warns if a webhook
would swallow our updates, and prints a link, plus a QR code when the qrcode
library happens to be installed.

If Telegram refuses the token, say so plainly and go back to step 1. Do not
guess at what went wrong.

## 3. Pairing

Ask the user to open the link and press Start. Their first message is what
teaches cctab the chat to write to. Nothing else is needed.

## 4. Done, and what comes next

It already works: setup.py saved the token to `~/.cctab/config.json`. Mention
that pasting it into the plugin's `bot_token` setting moves it into the system
keychain, which is worth doing but not required today.

Then say what happens next: nothing, for a while. cctab stays quiet until a task
runs longer than the threshold, 30 minutes out of the box. `/settings` in the
bot changes that from the phone. `/spend` shows what every tab of theirs has
cost.

If they want the rate-limit numbers, run this too:

```
${CLAUDE_PLUGIN_ROOT}/scripts/setup.py --statusline
```

Limits reach the statusline command and nowhere else, so cctab has to sit in
that slot. Their own statusline keeps running right after it. One exception
worth saying out loud: the VS Code extension has no statusline at all, so there
the wrapper never runs. Those users need `use_usage_api` turned on instead.

## Approvals, only if they ask

The permission buttons are off by default and stay off unless the user turns
them on in `/settings` under Messages. Before they do, tell them two things.
First, while cctab waits for a tap, Claude Code waits too, for up to a minute
per prompt. Second, nothing can be typed into a session from Telegram, only yes
or no to something Claude Code asked first, but anyone holding that chat can
approve a command.

The buttons run on the `PermissionRequest` hook, which Claude Code reads at
startup. If the plugin was installed inside a running session, they start
working after a restart. Say so, or the user will think it is broken.
