---
name: setup
description: Use when connecting cctab to Telegram for the first time, or when the user says notifications are not arriving and the bot may not be paired yet.
---

# Connecting cctab

Four steps, one at a time, waiting for the user between each.

## 1. Username

Run `${CLAUDE_PLUGIN_ROOT}/scripts/setup.py` with no arguments. It prints three
lines for @BotFather, including a generated username that is almost certainly
free. Show them exactly as printed.

The generated name is the point: people lose minutes to "this username is
already taken", so never ask them to invent one.

## 2. Token

BotFather replies with something like `8123456789:AAF...`. Take it on stdin, not
as an argument, since an argument shows up in `ps` and shell history:

```
echo "<token>" | ${CLAUDE_PLUGIN_ROOT}/scripts/setup.py -
```

It checks the shape, checks the token against Telegram, warns about a webhook
that would eat our updates, and prints a link plus a QR code if the qrcode
library is installed.

If Telegram refuses it, say so and go back to step 1. Do not guess why.

## 3. Pairing

They open the link and press Start. Their first message tells cctab which chat
to write to.

## 4. After

It works already: the token is in `~/.cctab/config.json`. Pasting it into the
plugin's `bot_token` setting moves it to the keychain. Not urgent.

Then: nothing happens for a while. Quiet until a task runs past thirty minutes.
`/settings` changes that, `/spend` shows the cost per tab.

For the rate-limit numbers:

```
${CLAUDE_PLUGIN_ROOT}/scripts/setup.py --statusline
```

Limits reach the statusline and nowhere else, so cctab sits in that slot and
calls their statusline after itself. Exception: the VS Code extension has no
statusline, so there the wrapper never runs and they need `use_usage_api`.

## Approvals

Off by default, and they stay off unless the user turns them on in `/settings`
under Messages. Two things to say before they do. While cctab waits for a tap,
Claude Code waits too, up to a minute per prompt. And nothing can be typed into
a session from Telegram, only yes or no, but anyone with that chat can approve
a command.

They run on the `PermissionRequest` hook, read at startup. Installed inside a
running session, they start working after a restart. Say so, or it looks broken.
