# Start at login

Any entry can open by itself **when you log in**. It's off for every entry until you turn it on.

## Turning it on
Open **⋮ → Edit…** on a row (or **Add** for a new one) and turn on **Start when I log in** under
**Startup**, then **Save**. The row's second line now ends in *starts at login*.

At login, only the marked entries open, the way **Open in** is set: as tabs in the launcher's kitty
window, as tabs in one new GNOME Terminal window, or in separate windows (see
[Open in](Open-In-Modes)).

## At login, not at power-on
This uses the desktop's standard autostart (XDG autostart), which runs when **you log in** to your
desktop session. If the PC starts at the login screen, nothing opens until you sign in. Logging out
and back in opens them again.

## How it works
While at least one entry is marked, the launcher keeps one login entry:
```
~/.config/autostart/net.vyechi.TerminalLauncher.desktop
```
It looks like this (with the full path of your clone):
```ini
[Desktop Entry]
Type=Application
Name=Terminal Launcher (start at login)
Comment=Opens the terminals marked "Start when I log in" in Terminal Launcher
Exec=python3 "<your clone>/terminal-launcher.py" --autostart
Icon=<your clone>/assets/terminal-launcher.svg
Terminal=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
```
- The launcher writes it every time the list is saved and something is marked, and **removes it**
  when nothing is marked any more. You never need to edit it.
- It runs `terminal-launcher.py --autostart`, which doesn't open the launcher's window. It waits
  **5 seconds** for the desktop to finish starting, then opens the marked entries and exits.
- Because it holds the full path of your clone, run `bash install.sh` again after moving the folder
  (see [Installing](Installing#moving-the-folder)).

## Checking it
See what would open, without opening anything (no 5 second wait, nothing logged):
```bash
python3 terminal-launcher.py --autostart --dry-run
```
It prints a `would start: …` line for each window (in kitty mode, `would add kitty tab: …` for
each tab), and a line for each entry whose folder is missing.

Each real login start adds a line to `~/.cache/terminal-launcher/launcher.log`:
```
2026-11-24 09:02:15 login start: ok
2026-11-25 08:57:40 login start: Folder not found: ~/code/old-project
```
*ok* means everything marked opened (or was handed to the terminal). Otherwise the line lists what
went wrong. A missing folder only skips that entry; a kitty problem in kitty mode means the kitty tabs
didn't open.

Running `--autostart` by hand opens the marked entries right now, like a login would. Agent
sessions that are already open would then open a second time, so use `--dry-run` to check.

## Turning it off
- **For one entry:** **⋮ → Edit…**, turn off **Start when I log in**, **Save**.
- **For all of them:** turn it off on each marked entry. When the last one is off, the login entry
  is removed.
- `bash install.sh remove` also deletes the login entry, but your entries stay marked: the next time
  you save anything in the launcher, the login entry is written again.
