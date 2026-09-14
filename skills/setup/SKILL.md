---
name: setup
description: Use when the user types just "cctab", asks about cctab, wants to connect cctab to Telegram, or says notifications are not arriving and the bot may not be paired yet.
---

# Connecting cctab

The script is `${CLAUDE_PLUGIN_ROOT}/scripts/setup.py`. Every step prints one
keyword first (`LINK`, `BOT`, `CONNECTED`, `EXPIRED`...) so you know what
happened without guessing.

**The one rule that matters.** Once you hand out a link, never end your turn
and never run anything in the background until you have `CONNECTED` or
`EXPIRED`. Show the link in a message, then in the same turn run the waiting
step in the foreground with a Bash timeout of 420000. Between turns you see
nothing: if you stop, the user presses the button and gets silence.

Always write links as a bare URL on their own line. Inside a code block they
cannot be clicked.

## 0. Check first

Run `setup.py status`.

- `READY @name`: already connected. Say so, mention `/settings` in the bot, stop.
- `NOT_PAIRED @name owner`: the bot exists, nobody pressed Start. Tell them to
  open @name in Telegram and press Start, then same turn run `setup.py pair`.
- `NOT_PAIRED @name link`: run `setup.py relink` and go to "Start link" below.
- `NO_BOT`: ask one question and end your turn: a link, or a QR code to scan
  with the phone?

## 1. Create and connect

Run `setup.py link`, or `setup.py link --qr` if they chose the QR. It prints
`LINK <url>`, and with `--qr` also `QR <path>`: the picture is already open on
their screen.

Send one message: the link as a bare URL (and that the QR is on the screen, if
they chose it), then two short lines. Confirm the window Telegram opens, the
name and username are already filled in. Then press Start in the bot chat that
opens right after. Nothing else is needed.

Then, same turn, run `setup.py wait`. It waits up to 3 minutes for the window,
dresses the bot, then waits up to 3 more for Start.

## 2. What `wait` prints

- `BOT @name`, `DRESSED ...`, `CONNECTED`: done. A welcome message is already in
  their Telegram. Nothing arrives until a task runs past 30 minutes; `/settings`
  in the bot changes that, `/spend` shows the cost per tab.
- `BOT ...` then `EXPIRED`: the bot exists but Start was not pressed. Tell them
  to press Start in @name, then run `setup.py pair`.
- `EXPIRED` alone: nobody confirmed the window. Say the link ran out and that
  typing cctab again gives a fresh one. Stop.
- `PAIR <url>`: an older path that needs a Start link. Go to "Start link".
- `UNREACHABLE` followed by three lines for @BotFather: the manager did not
  answer. Show those lines. When they send the token, pass it on stdin, never as
  an argument, since an argument shows up in `ps` and shell history:
  `echo "<token>" | setup.py -`. It prints `BOT` and `PAIR`; go to "Start link".
- `BAD_TOKEN`: say what it says, do not guess why.

## Start link

Only for `PAIR <url>`. Send it as a bare URL: open it and press Start, 3
minutes. Then same turn run `setup.py pair`. `CONNECTED` means done;
`EXPIRED` means run `relink` and try once more.

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
