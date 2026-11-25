# Troubleshooting

**First step for most things:** start the launcher from a terminal, so you see its messages:
```bash
python3 terminal-launcher.py
```
To see what it would open, without opening anything:
`python3 terminal-launcher.py --autostart --dry-run` (or `--all --dry-run`).

### The window doesn't open: `Namespace Adw not available`
GTK 4 or libadwaita is missing. An error that names `ToolbarView` instead means libadwaita is older
than 1.4. See [Installing](Installing#what-you-need) for the packages and a one-line version check.

### A row is red and its ▶ is greyed out
Its folder doesn't exist (any more). The second line says *Folder not found: …*. Use
**⋮ → Edit…** to point it at the right folder, or create the folder.

### "kitty isn't installed yet"
**Open in** is set to **My kitty window**, but there's no kitty. Install it (kitty's own installer
puts it in `~/.local/kitty.app`, or `sudo apt install kitty`), or choose another **Open in**.

### "kitty started but isn't answering; try again"
The launcher started a new kitty window but couldn't reach it within about 8 seconds. Usually the
window is just slow to come up the first time: try again. If it keeps happening, start kitty by hand
from a terminal to see its own error messages.

### Kitty tabs open in a new window, not in the kitty I already have open
On purpose: the launcher only adds tabs to **its own** kitty window, the one it started on its
private socket. A kitty you opened yourself is never touched. Once the launcher's window is open,
everything goes there until you close it. See [Open in](Open-In-Modes#my-kitty-window).

### "One new window" opens separate windows
GNOME Terminal (`gnome-terminal`) isn't installed, so the launcher falls back to one window each.
Install it to get tabs.

### ▶ opens a COSMIC Terminal window although Open in says "One new window"
That's how it works: **▶** and **Folder…** open one terminal, so it gets a window of its own (COSMIC
Terminal first, then GNOME Terminal). The tabs are for the button at the top.

### `command not found` in a new terminal
Entries run in `bash -lc`, which reads `~/.profile` but stops early in most `~/.bashrc` files. A
`PATH` change added at the end of `~/.bashrc` (by nvm, a language installer, …) is then missing.
Move it to `~/.profile` and log out and in once. See [Entry types](Entry-Types#your-shell-setup).

### The agent starts a new session instead of resuming
- The **Session ID** is empty or mistyped. The row's second line says *new session* when it's empty.
- For Claude Code, pick the session in **Start from a recent Claude session**, so the folder and the
  ID come from the same session.
- For omp and opencode, use the ID you'd pass to `omp --resume` or `opencode --session`.

### "Recent sessions" says *None found*
There's no Claude Code history in `~/.claude/projects` yet. The list only shows Claude Code
sessions; omp and opencode IDs have to be typed in.

### Nothing opened when I logged in
1. Is any entry marked? Its second line should end in *starts at login*.
2. Is the login entry there? `ls ~/.config/autostart/net.vyechi.TerminalLauncher.desktop`.
   If not, save any change in the launcher, or run `bash install.sh`.
3. Did you move or rename the cloned folder? Run `bash install.sh` again from its new place.
4. What does the log say? `tail ~/.cache/terminal-launcher/launcher.log`. No new line at all means
   the login entry didn't run.
5. Does the dry run show what you expect? `python3 terminal-launcher.py --autostart --dry-run`.

It runs when you **log in**, not at power-on: nothing opens while the PC waits at the login screen.

### Things open twice at login
Look in `~/.config/autostart/` for another entry that starts the same terminals, for example one
you made by hand before. Terminal Launcher only ever writes `net.vyechi.TerminalLauncher.desktop`.
Also check the list for the same entry twice.

### The login entry came back after `install.sh remove`
`install.sh remove` deletes the login entry but leaves your entries marked, and the launcher writes
it again on its next save. Turn off **Start when I log in** on each entry. See
[Start at login](Start-At-Login#turning-it-off).

### The list is empty after I edited sessions.json
The file isn't valid JSON any more. Started from a terminal, the launcher prints `can't read …` with
the line and column. **Don't add anything until it's fixed**: the next save would replace the broken
file. See [Files and settings](Files-and-Settings#editing-it-by-hand).

### Dragging a row does nothing
- With one row there's nothing to move, so there's no handle.
- Drop it **on another row**: it takes that row's place. Dropped anywhere else (below the list, on the
  **Open in** row), it stays where it was.
- **⋮ → Move up / Move down** move a row one step, too.

### The app-menu entry doesn't start anything
The cloned folder was moved or deleted. Run `bash install.sh` again from where it is now.
