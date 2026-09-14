---
name: setup
description: Use when the user types just "cctab", asks about cctab, wants to connect cctab to Telegram, or says notifications are not arriving and the bot may not be paired yet.
---

# Connecting cctab

The script is `${CLAUDE_PLUGIN_ROOT}/scripts/setup.py`. Every step prints one
keyword first (`LINK`, `BOT`, `PAIR`, `CONNECTED`, `EXPIRED`...) so you know
what happened without guessing.

**The one rule that matters.** From the moment you hand out a link until the
user is connected, never end your turn and never run anything in the
background. Show the link in a message, then in the same turn run the waiting
step in the foreground with a Bash timeout of 240000. Between turns you see
nothing: if you stop, the user presses the button and gets silence.

Always write links as a bare URL on their own line. Inside a code block they
cannot be clicked.

## 0. Check first

Run `setup.py status`.

- `READY @name`: already connected. Say so, mention `/settings` in the bot, stop.
- `NOT_PAIRED @name`: the bot exists but no chat. Go to step 3 with `relink`.
- `NO_BOT`: ask one question and end your turn: link, or a QR code to scan with
  the phone?

## 1. Create the bot

Run `setup.py link`, or `setup.py link --qr` if they chose the QR. It prints
`LINK <url>`, and with `--qr` also `QR <path>`: the picture is already open on
their screen.

Send a message with the link as a bare URL and one line: Telegram opens a window
with the name and username filled in, confirm it, the link works for 3 minutes.
If they chose the QR, say the code is open on the screen.

Then, same turn, run `setup.py wait` (add `--qr` if they chose it).

## 2. What `wait` prints

- `BOT @name`, `DRESSED ...`, `PAIR <url>`: the bot exists with its name,
  description, commands and picture. Go to step 3.
- `EXPIRED`: nobody confirmed within 3 minutes. Say the link ran out and that
  typing cctab again gives a fresh one. Stop.
- `UNREACHABLE` followed by three lines for @BotFather: the manager did not
  answer. Show those lines. When they send the token, pass it on stdin, never as
  an argument, since an argument shows up in `ps` and shell history:
  `echo "<token>" | setup.py -`. It prints the same `BOT` and `PAIR` lines.
- `BAD_TOKEN`: say what it says, do not guess why.

## 3. Pair the chat

If you came from `NOT_PAIRED`, first run `setup.py relink` (with `--qr` if
wanted) to get a `PAIR <url>`.

Send the `PAIR` link as a bare URL: open it and press Start, 3 minutes. Only
this link pairs the chat, so a stranger writing to the bot cannot take it over.

Then, same turn, run `setup.py pair`.

- `CONNECTED`: a welcome message is already in their Telegram. Tell them it is
  done and that nothing comes until a task runs past 30 minutes; `/settings` in
  the bot changes that, `/spend` shows the cost per tab.
- `EXPIRED`: Start was not pressed in time. Run `relink`, send the new link, run
  `pair` again.

## After

The token is in `~/.cctab/config.json`, readable only by the user.

For the rate-limit numbers: `setup.py --statusline`. Limits reach the
statusline and nowhere else, so cctab sits in that slot and calls their old
statusline after itself. The VS Code extension has no statusline, so there they
need `use_usage_api` instead.

## Approvals

Off by default, and they stay off unless the user turns them on in `/settings`
under Messages. Two things to say before they do. While cctab waits for a tap,
Claude Code waits too, up to a minute per prompt. And nothing can be typed into
a session from Telegram, only yes or no, but anyone with that chat can approve
a command.

They run on the `PermissionRequest` hook, read at startup. Installed inside a
running session, they start working after a restart. Say so, or it looks broken.
