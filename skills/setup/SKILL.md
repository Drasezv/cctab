---
name: setup
description: Use when the user types just "cctab", asks about cctab, wants to connect cctab to Telegram, or says notifications are not arriving and the bot may not be paired yet.
---

# Connecting cctab

Three steps, one at a time, waiting for the user between each.

## 1. The link

Run `${CLAUDE_PLUGIN_ROOT}/scripts/setup.py` with no arguments. It prints a
`t.me/newbot/...` link and a QR code, then waits. Show them exactly as printed
and say they can scan the QR with their phone instead of opening the link.

What they see: a window with the bot's username and name already filled in, and
one button to confirm. No @BotFather, no token to copy. The script picks the
token up by itself and moves on to step 2.

If nothing comes back, the script says so and falls back to the old way: three
lines for @BotFather and a generated username that is almost certainly free.
Take the token on stdin, never as an argument, since an argument shows up in
`ps` and in shell history:

```
echo "<token>" | ${CLAUDE_PLUGIN_ROOT}/scripts/setup.py -
```

If Telegram refuses the token, say so and go back. Do not guess why.

## 2. Pairing

The script prints a second link, this one with a one-off code that expires in
fifteen minutes, plus its own QR. They open it and press Start. Only that link
pairs the chat, so a stranger writing to the bot cannot take it over.

It also sets the bot's name, description, commands and picture along the way,
so none of that needs @BotFather either.

## 3. After

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
