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
paste it, then feed it in on stdin rather than as an argument — an argument is
visible in `ps` and stays in their shell history:

```
echo "<token>" | setup.py -
```

It checks the shape, checks the token against Telegram, warns if a webhook
would swallow our updates, and prints a link, plus a QR code when the qrcode
library happens to be installed.

If Telegram refuses the token, say so plainly and go back to step 1. Do not
guess at what went wrong.

## 3. Pairing

Ask the user to open the link and press Start. Their first
message is what teaches cctab the chat to write to; nothing else is needed.

## 4. Done, and one optional thing

It already works: setup.py saved the token. Mention that pasting it into the
plugin's `bot_token` setting moves it into the system keychain, which is worth
doing but not required today.

Then say what happens next: nothing, for a while. cctab stays quiet until a task
runs longer than the threshold, 30 minutes out of the box. Send `/settings` to
the bot to change that from the phone, or `/spend` to see what every tab of
theirs has cost.

One thing worth saying out loud, because it surprises people: when Claude Code
stops to ask permission, the ask arrives in the chat with two buttons, and
tapping one lets the session carry on without them. Nothing can be typed into a
session from Telegram — only yes or no to something Claude Code asked first —
but anyone holding that chat can approve a command. Say that plainly once.

If they want the rate-limit numbers, run this too:

```
setup.py --statusline ~/.claude/plugins/cctab/scripts/statusline.py
```

Limits reach the statusline command and nowhere else, so cctab has to sit in
that slot; their own statusline keeps running right after it. One exception
worth saying out loud: **the VS Code extension has no statusline at all**, so
there the wrapper never runs — those users need `use_usage_api` turned on
instead.

Permission relay needs the `PermissionRequest` hook, which Claude Code reads at
startup. If the user installed the plugin inside a running session, the buttons
begin working after they restart Claude Code. Tell them, or they will think it
is broken.
