# Security policy

## What Terminal Launcher is, and what could go wrong

Terminal Launcher is a small GTK app that runs as you, on your own machine. It keeps a list in
`~/.config/terminal-launcher/` and starts terminals (kitty, GNOME Terminal, COSMIC Terminal) with a
shell, an agent (`claude`, `omp`, `opencode`) or a command you saved. In kitty mode it starts one
kitty window with remote control on a socket only (`unix:@claude-launcher-kitty`) and adds tabs
to it. At login it can run from one XDG autostart entry, `~/.config/autostart/net.vyechi.TerminalLauncher.desktop`.

When **Byte watches my kitty tabs** is on (in kitty mode), it asks that same kitty window, read-only
(`kitty @ ls`), what each tab is doing, and shows only a command's name and subcommand; it never reads
the windows' environment and keeps nothing.

It makes **no network connections**, has no server, account or telemetry, and needs no sudo.

Realistic problems we want to hear about:
- A crafted **folder name, entry name, session ID or command** (for example in a `sessions.json`
  someone shares) that makes the launcher run something other than what the entry shows.
- The **"recent Claude sessions"** picker running code or misbehaving because of a crafted file in
  Claude Code's history.
- `install.sh` or the login entry **writing or deleting files** outside the launcher's own.
- The kitty remote-control socket being **reachable by another user** on the same machine.

## Supported versions

| Version | Supported |
| --- | --- |
| latest `main` | yes |
| anything older | no: update first (`git pull`) |

## Reporting a vulnerability

**Preferred: report it privately** through GitHub's private vulnerability reporting:
[Security → Report a vulnerability](../../security/advisories/new). Only the maintainer sees it.

If you're comfortable with it being public (for example a low-risk hardening idea), a normal
[issue](../../issues) is fine too.

Please include your distribution and desktop, which **Open in** mode you use, the steps to
reproduce, and the output of `python3 terminal-launcher.py --autostart --dry-run` if it helps
(remove anything private).

## What to expect

- A first answer within a week.
- A fix in the next change to `main`, with a check in `scripts/check.sh`, and a GitHub security
  advisory when it affects users.
- Credit in the advisory, unless you'd rather not be named.

## Out of scope

- Bugs in the terminals or agents themselves: report those to kitty, GNOME Terminal, COSMIC
  Terminal, Claude Code, omp or opencode.
- Attacks that need code already running as your user (it can change your list, your autostart
  folder or anything else you own anyway).
- Commands you saved yourself doing what they say: an entry of type "Run a command" runs that command.
